import os
import re
import shlex
import shutil
import subprocess
from prompt_toolkit import prompt
from prompt_toolkit.completion import Completer, Completion, WordCompleter, DummyCompleter
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from lazyctl.config import IS_WINDOWS, IS_MACOS, console, with_privilege
from lazyctl.loaders import load_adapters
from lazyctl.commands import BUILTIN_DESCRIPTIONS

MAX_ARG_COMPLETIONS = 80
MIN_ARG_PREFIX_CHARS = {
    "available_pkg": 2,
    "installed_pkg": 1,
    "service": 0,
    "path": 0,
}


def resolve_command(all_names, token: str):
    """
    Returns:
        (name, None)         if resolved unambiguously (exact match wins first)
        (None, [candidates]) if ambiguous (2+ prefix matches)
        (None, [])           if no match at all
    """
    token = token.lower()
    names = list(all_names)
    if token in names:
        return token, None
    matches = sorted(set(n for n in names if n.startswith(token)))
    if len(matches) == 1:
        return matches[0], None
    return None, matches


def completion_matches(text_before_cursor: str, commands: dict, all_names: list[str]) -> list[str]:
    """Return bounded matching command or argument candidates."""
    words = text_before_cursor.split(" ")

    if len(words) == 1:
        word = words[0].lower()
        if not word:
            return []
        return [name for name in all_names if name.startswith(word)]

    head = words[0].lower()
    resolved, _ = resolve_command(all_names, head)
    if not resolved or resolved not in commands:
        return []

    cmd = commands[resolved]
    if not cmd.arg_completions:
        return []

    word = words[-1]
    min_prefix = MIN_ARG_PREFIX_CHARS.get(cmd.arg_completion_kind or "", 0)
    if len(word) < min_prefix:
        return []

    # path and available_pkg completions receive the current typed word as a
    # prefix so they can list the right directory / run the right search.
    kind = cmd.arg_completion_kind or ""
    try:
        if kind in ("path", "available_pkg"):
            candidates = cmd.arg_completions(word)
        else:
            candidates = cmd.arg_completions()
    except TypeError:
        candidates = cmd.arg_completions()

    matches = []
    word_lower = word.lower()
    for cand in candidates:
        if cand.lower().startswith(word_lower):
            matches.append(cand)
            if len(matches) >= MAX_ARG_COMPLETIONS:
                break
    return matches


class LazyCompleter(Completer):
    def __init__(self, commands: dict):
        self.commands = commands
        self.all_names = list(commands.keys()) + list(BUILTIN_DESCRIPTIONS.keys())

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        words = text.split(" ")
        word = words[-1]
        matches = completion_matches(text, self.commands, self.all_names)

        # Detect if we're completing a path-type argument
        arg_kind = None
        if len(words) > 1:
            head = words[0].lower()
            resolved, _ = resolve_command(self.all_names, head)
            if resolved and resolved in self.commands:
                arg_kind = self.commands[resolved].arg_completion_kind

        for match in matches:
            if arg_kind == "path":
                if os.path.isdir(match):
                    meta = "📁 folder"
                else:
                    meta = "📄 file"
            else:
                cmd = self.commands.get(match)
                meta = cmd.desc if cmd else BUILTIN_DESCRIPTIONS.get(match, "")
            yield Completion(match, start_position=-len(word), display_meta=meta)


def render_result(proc: subprocess.CompletedProcess, label: str):
    output = (proc.stdout or "") + (proc.stderr or "")
    output = output.rstrip("\n")

    if "active (running)" in output:
        border = "green"
    elif "failed" in output or proc.returncode not in (0, 3):
        border = "red"
    elif "inactive" in output or "dead" in output:
        border = "yellow"
    else:
        border = "cyan"

    content = Text.from_ansi(output) if output else "(no output)"
    console.print(Panel(content, title=label, border_style=border, expand=False))


def print_help(commands: dict, manager: str | None):
    table = Table(title="lazyctl — available commands", show_lines=False)
    table.add_column("Command", style="bold cyan")
    table.add_column("Description")
    for name, cmd in commands.items():
        privilege_label = "admin" if IS_WINDOWS else "sudo"
        desc = cmd.desc + (f"  [dim]({privilege_label})[/dim]" if cmd.needs_sudo else "")
        table.add_row(name, desc)
    for name, desc in BUILTIN_DESCRIPTIONS.items():
        if name == "quit":
            continue  # shown together with 'exit'
        label = "exit / quit" if name == "exit" else name
        table.add_row(label, desc)
    console.print(table)
    console.print(
        "[dim]Nothing needs to be typed in full — 'hel' -> help, 'cl' -> clear, "
        "'sta nginx' -> status nginx, all work as long as the prefix is unambiguous. "
        "If two names share a prefix (e.g. 'status'/'start' both start with 'st', "
        "or 'reboot'/'remove' both start with 're'), lazyctl lists the candidates "
        "instead of guessing.[/dim]"
    )
    if not manager:
        console.print("[yellow]No supported package manager detected — install/remove/update unavailable.[/yellow]")


def configure_macos_network(adapter: str, style):
    """Interactive network configuration for macOS using networksetup."""
    try:
        console.print(f"\n[bold cyan]Configuring {adapter}[/bold cyan]")
        mode_completer = WordCompleter(["dhcp", "static", "up", "down"], ignore_case=True)
        mode = prompt([("class:prompt", "Action [dhcp/static/up/down]: ")],
                      completer=mode_completer, style=style).strip().lower()
        if mode not in ("dhcp", "static", "up", "down"):
            console.print("[red]Invalid action. Aborting.[/red]")
            return
        if mode == "up":
            subprocess.run(["sudo", "networksetup", "-setnetworkserviceenabled", adapter, "on"])
            console.print("[bold green]Done.[/bold green]")
        elif mode == "down":
            subprocess.run(["sudo", "networksetup", "-setnetworkserviceenabled", adapter, "off"])
            console.print("[bold yellow]Done.[/bold yellow]")
        elif mode == "dhcp":
            console.print(f"[green]Applying DHCP to {adapter}...[/green]")
            subprocess.run(["sudo", "networksetup", "-setdhcp", adapter])
            console.print("[bold green]Success![/bold green]")
        else:
            empty = DummyCompleter()
            ip_addr = prompt([("class:prompt", "IP Address (e.g. 192.168.1.50): ")], completer=empty, style=style).strip()
            mask    = prompt([("class:prompt", "Subnet mask (e.g. 255.255.255.0): ")], completer=empty, style=style).strip()
            gw      = prompt([("class:prompt", "Gateway (e.g. 192.168.1.1): ")],    completer=empty, style=style).strip()
            dns     = prompt([("class:prompt", "DNS (e.g. 8.8.8.8): ")],            completer=empty, style=style).strip()
            if not ip_addr or not mask:
                console.print("[red]IP address and subnet mask are required. Aborting.[/red]")
                return
            console.print(f"[green]Applying static IP to {adapter}...[/green]")
            cmd = ["sudo", "networksetup", "-setmanual", adapter, ip_addr, mask]
            if gw:
                cmd.append(gw)
            subprocess.run(cmd)
            if dns:
                subprocess.run(["sudo", "networksetup", "-setdnsservers", adapter, dns])
            console.print("[bold green]Success![/bold green]")
    except KeyboardInterrupt:
        console.print("\n[dim]Cancelled.[/dim]")


def configure_windows_network(adapter: str, style):
    adapters = load_adapters()
    if adapter not in adapters:
        console.print(f"[red]Adapter '{adapter}' not found on this system.[/red]")
        return

    try:
        console.print(f"\n[bold cyan]Configuring {adapter}[/bold cyan]")

        mode_completer = WordCompleter(["dhcp", "static", "up", "down"], ignore_case=True)
        mode = prompt([("class:prompt", "Action [dhcp/static/up/down]: ")], completer=mode_completer, style=style).strip().lower()

        if mode not in ("dhcp", "static", "up", "down"):
            console.print("[red]Invalid action. Aborting.[/red]")
            return

        if mode == "up":
            subprocess.run(["netsh", "interface", "set", "interface", adapter, "admin=enabled"])
            console.print("[bold green]Done.[/bold green]")
        elif mode == "down":
            subprocess.run(["netsh", "interface", "set", "interface", adapter, "admin=disabled"])
            console.print("[bold yellow]Done.[/bold yellow]")
        elif mode == "dhcp":
            console.print(f"[green]Applying DHCP to {adapter}...[/green]")
            r1 = subprocess.run(["netsh", "interface", "ip", "set", "address", f"name={adapter}", "source=dhcp"])
            r2 = subprocess.run(["netsh", "interface", "ip", "set", "dns", f"name={adapter}", "source=dhcp"])
            if r1.returncode == 0 and r2.returncode == 0:
                console.print("[bold green]Success![/bold green]")
            else:
                console.print("[yellow]netsh returned a non-zero exit code -- verify with 'ip' command. "
                              "You may need to run lazyctl as Administrator.[/yellow]")
        else:
            empty = DummyCompleter()
            ip_addr = prompt([("class:prompt", "IP Address (e.g. 192.168.1.50): ")], completer=empty, style=style).strip()
            mask = prompt([("class:prompt", "Subnet mask (e.g. 255.255.255.0): ")], completer=empty, style=style).strip()
            gw = prompt([("class:prompt", "Gateway (e.g. 192.168.1.1): ")], completer=empty, style=style).strip()
            dns = prompt([("class:prompt", "DNS (e.g. 8.8.8.8): ")], completer=empty, style=style).strip()

            if not ip_addr or not mask:
                console.print("[red]IP address and subnet mask are required. Aborting.[/red]")
                return

            console.print(f"[green]Applying static IP to {adapter}...[/green]")
            cmd = ["netsh", "interface", "ip", "set", "address", f"name={adapter}", "static", ip_addr, mask]
            if gw:
                cmd.append(gw)
            r1 = subprocess.run(cmd)
            r2 = subprocess.CompletedProcess([], 0)  # default success
            if dns:
                r2 = subprocess.run(["netsh", "interface", "ip", "set", "dns", f"name={adapter}", "static", dns])
            if r1.returncode == 0 and r2.returncode == 0:
                console.print("[bold green]Success![/bold green]")
            else:
                console.print("[yellow]netsh returned a non-zero exit code -- verify with 'ip' command. "
                              "You may need to run lazyctl as Administrator.[/yellow]")
    except KeyboardInterrupt:
        console.print("\n[dim]Cancelled.[/dim]")
