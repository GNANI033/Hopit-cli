#!/usr/bin/env python3
"""
lazyctl — a fast TUI overlay that makes common Linux admin commands short and
tab-completable, instead of typing the full underlying command every time.

Everything — real commands AND builtins like 'help'/'clear'/'exit' — goes
through the same prefix resolver. So 'hel' resolves to 'help' and 'cl'
resolves to 'clear' as long as nothing else shares that prefix, exactly like
'sta' resolves to 'status'. If something IS ambiguous (e.g. 'st' could mean
status/start/stop), lazyctl lists the candidates instead of guessing.

Example:
    systemctl status nginx     ->  status nginx   /  sta nginx  /  st<Tab>
    journalctl -u nginx -n 50  ->  logs nginx
    journalctl -u nginx -f     ->  live nginx        (Ctrl-C stops following)
    apt install htop           ->  install htop      (autocompletes package names)
    apt remove htop            ->  remove htop       (autocompletes installed packages)
    shutdown -r now            ->  reboot
    shutdown -r +10            ->  reboot 10         (minutes from now)
    shutdown -r 23:30          ->  reboot 23:30      (clock time)
    shutdown -h now            ->  shutdown
    shutdown -c                ->  cancel            (cancel a pending shutdown/reboot)

HOW TO ADD A NEW COMMAND LATER
--------------------------------
Add one entry to the COMMANDS dict inside build_commands(). Prefix matching,
tab-completion, help text, ambiguity handling, and sudo escalation all pick
it up automatically because they all read from this same dict.
"""

import os

# Must be set before prompt_toolkit creates its output object. Some terminals
# don't answer cursor-position reports reliably, and waiting for those replies
# can make typing appear frozen.
os.environ.setdefault("PROMPT_TOOLKIT_NO_CPR", "1")

import re
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, List, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

console = Console()


# --------------------------------------------------------------------------
# Command registry
# --------------------------------------------------------------------------

@dataclass
class Command:
    run: Callable[[str], List[str]]                    # builds the real argv to execute
    desc: str                                            # shown in help / completion menu
    needs_arg: bool = True                               # whether an argument is required
    needs_sudo: bool = False                             # auto-prepend sudo if not already root
    mode: str = "capture"                                # "capture" (render nicely) or "stream" (live passthrough)
    arg_completions: Optional[Callable[[], List[str]]] = None  # candidates for arg tab-completion
    arg_completion_kind: Optional[str] = None             # service / installed_pkg / available_pkg


BUILTIN_DESCRIPTIONS = {
    "help": "Show this help",
    "clear": "Clear the screen",
    "exit": "Leave lazyctl",
    "quit": "Leave lazyctl",
}


# Keep tab completion responsive even when package managers return tens of
# thousands of names. Prompt-toolkit renders every yielded completion.
MAX_ARG_COMPLETIONS = 80
MIN_ARG_PREFIX_CHARS = {
    "available_pkg": 2,
    "installed_pkg": 1,
    "service": 0,
    "path": 0,
}


# --------------------------------------------------------------------------
# Distro / package-manager detection
# --------------------------------------------------------------------------

MANAGER_DISPLAY_NAME = {
    "apt-get": "apt", "dnf": "dnf", "yum": "yum",
    "pacman": "pacman", "zypper": "zypper", "apk": "apk",
}

MANAGER_UPDATE_CMDS = {
    "apt-get": "apt-get update && apt-get upgrade -y",
    "dnf":     "dnf upgrade -y",
    "yum":     "yum update -y",
    "pacman":  "pacman -Syu --noconfirm",
    "zypper":  "zypper --non-interactive update",
    "apk":     "apk update && apk upgrade",
}

# Per-manager package install/remove argv builders, plus how to enumerate
# installed / available package names for tab-completion.
MANAGER_PKG = {
    "apt-get": {
        "install": lambda pkg: ["apt-get", "install", "-y", pkg],
        "remove": lambda pkg: ["apt-get", "remove", "-y", pkg],
        "installed_cmd": ["dpkg-query", "-W", "-f=${Package}\n"],
        "available_cmd": ["apt-cache", "pkgnames"],
        "available_parse": None,
    },
    "dnf": {
        "install": lambda pkg: ["dnf", "install", "-y", pkg],
        "remove": lambda pkg: ["dnf", "remove", "-y", pkg],
        "installed_cmd": ["rpm", "-qa", "--qf", "%{NAME}\n"],
        "available_cmd": ["dnf", "-q", "repoquery", "--available", "--qf", "%{NAME}\n"],
        "available_parse": None,
    },
    "yum": {
        "install": lambda pkg: ["yum", "install", "-y", pkg],
        "remove": lambda pkg: ["yum", "remove", "-y", pkg],
        "installed_cmd": ["rpm", "-qa", "--qf", "%{NAME}\n"],
        "available_cmd": ["yum", "-q", "list", "available"],
        "available_parse": "yum",  # needs special header/column parsing
    },
    "pacman": {
        "install": lambda pkg: ["pacman", "-S", "--noconfirm", pkg],
        "remove": lambda pkg: ["pacman", "-R", "--noconfirm", pkg],
        "installed_cmd": ["pacman", "-Qq"],
        "available_cmd": ["pacman", "-Slq"],
        "available_parse": None,
    },
    "zypper": {
        "install": lambda pkg: ["zypper", "--non-interactive", "install", pkg],
        "remove": lambda pkg: ["zypper", "--non-interactive", "remove", pkg],
        "installed_cmd": ["rpm", "-qa", "--qf", "%{NAME}\n"],
        "available_cmd": None,  # no fast, reliable generic listing — skip completion
        "available_parse": None,
    },
    "apk": {
        "install": lambda pkg: ["apk", "add", pkg],
        "remove": lambda pkg: ["apk", "del", pkg],
        "installed_cmd": ["apk", "info"],
        "available_cmd": ["apk", "search", "-q"],
        "available_parse": None,
    },
}


def detect_package_manager() -> Optional[str]:
    """Detect by checking which manager binary is actually on PATH —
    more reliable across distro derivatives than parsing os-release."""
    for mgr in ("apt-get", "dnf", "yum", "pacman", "zypper", "apk"):
        if shutil.which(mgr):
            return mgr
    return None


def read_os_pretty_name() -> str:
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except FileNotFoundError:
        pass
    return "unknown distro"


def with_privilege(argv: List[str], needs_sudo: bool) -> List[str]:
    if needs_sudo and os.geteuid() != 0:
        return ["sudo"] + argv
    return argv


# --------------------------------------------------------------------------
# Name-list loaders (services, installed packages, available packages)
# --------------------------------------------------------------------------

def _run_lines(argv: List[str], timeout: int) -> List[str]:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return out.stdout.splitlines()
    except Exception:
        return []


def load_service_names() -> List[str]:
    lines = _run_lines(
        ["systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager"],
        timeout=3,
    )
    names = []
    for line in lines:
        parts = line.split()
        if parts:
            names.append(parts[0].removesuffix(".service"))
    return sorted(names)


def load_installed_packages(manager: Optional[str]) -> List[str]:
    if not manager:
        return []
    cmd = MANAGER_PKG[manager]["installed_cmd"]
    lines = _run_lines(cmd, timeout=5)
    return sorted(set(l.strip() for l in lines if l.strip()))


def _parse_yum_available(lines: List[str]) -> List[str]:
    names = []
    for l in lines:
        l = l.strip()
        if not l or l.lower().endswith("packages") or l.lower().startswith("last metadata"):
            continue
        name = l.split()[0]
        if "." in name:
            name = name.rsplit(".", 1)[0]
        names.append(name)
    return names


def load_available_packages(manager: Optional[str]) -> List[str]:
    if not manager:
        return []
    cfg = MANAGER_PKG[manager]
    cmd = cfg.get("available_cmd")
    if not cmd:
        return []
    lines = _run_lines(cmd, timeout=20)
    if cfg.get("available_parse") == "yum":
        return sorted(set(_parse_yum_available(lines)))
    return sorted(set(l.strip() for l in lines if l.strip()))


def load_path_entries() -> List[str]:
    """List files and directories in the current working directory."""
    try:
        return sorted(os.listdir("."))
    except OSError:
        return []


class BackgroundNames:
    """Loads a (possibly slow) name list in the background so startup and
    the prompt never block on it. Reads of `.names` are safe without a lock
    because CPython list-reference assignment is atomic."""

    def __init__(self, loader: Callable[[], List[str]], start_immediately: bool = True):
        self.names: List[str] = []
        self._loader = loader
        self._started = False
        self._lock = threading.Lock()
        if start_immediately:
            self.start()

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
            threading.Thread(target=self._load, daemon=True).start()

    def get(self) -> List[str]:
        self.start()
        return self.names

    def _load(self):
        try:
            self.names = self._loader()
        except Exception:
            self.names = []


# --------------------------------------------------------------------------
# Reboot / shutdown time-argument handling
# --------------------------------------------------------------------------

_CLOCK_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def shutdown_time_arg(arg: str) -> str:
    """Turns a user-friendly arg into what the 'shutdown' binary expects:
    (none) / 'now'  -> 'now'
    '10'            -> '+10'   (minutes from now)
    '23:30'         -> '23:30' (clock time, passed through as-is)
    anything else is passed through unchanged and 'shutdown' will report its
    own error, which the user sees directly since these run in stream mode.
    """
    arg = (arg or "").strip()
    if not arg or arg.lower() == "now":
        return "now"
    if arg.isdigit():
        return f"+{arg}"
    return arg  # covers valid HH:MM and anything invalid (shutdown will say so)


# --------------------------------------------------------------------------
# Build the command registry
# --------------------------------------------------------------------------

def build_commands(manager: Optional[str], names) -> dict:
    """`names` is a dict of zero-arg callables returning current candidate
    lists: {"service": ..., "installed_pkg": ..., "available_pkg": ...}"""

    commands = {
        "status": Command(
            run=lambda svc: ["systemctl", "status", svc],
            desc="Show the status of a service",
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "start": Command(
            run=lambda svc: ["systemctl", "start", svc],
            desc="Start a service",
            needs_sudo=True,
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "stop": Command(
            run=lambda svc: ["systemctl", "stop", svc],
            desc="Stop a service",
            needs_sudo=True,
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "restart": Command(
            run=lambda svc: ["systemctl", "restart", svc],
            desc="Restart a service",
            needs_sudo=True,
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "logs": Command(
            run=lambda svc: ["journalctl", "-u", svc, "-n", "50", "--no-pager"],
            desc="Show recent logs for a service",
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "live": Command(
            run=lambda svc: ["journalctl", "-u", svc, "-f"],
            desc="Follow a service's logs live (Ctrl-C to stop)",
            mode="stream",
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "reboot": Command(
            run=lambda arg: ["shutdown", "-r", shutdown_time_arg(arg)],
            desc="Reboot now, in N minutes, or at HH:MM ('reboot 10', 'reboot 23:30')",
            needs_arg=False,
            needs_sudo=True,
            mode="stream",
        ),
        "shutdown": Command(
            run=lambda arg: ["shutdown", "-h", shutdown_time_arg(arg)],
            desc="Power off now, in N minutes, or at HH:MM ('shutdown 10')",
            needs_arg=False,
            needs_sudo=True,
            mode="stream",
        ),
        "cancel": Command(
            run=lambda _: ["shutdown", "-c"],
            desc="Cancel a pending scheduled shutdown/reboot",
            needs_arg=False,
            needs_sudo=True,
            mode="stream",
        ),
        "list": Command(
            run=lambda arg: ["ls", "-la", "--color=always"] if arg.lower() == "all"
                            else (["ls", "--color=always", arg] if arg else ["ls", "--color=always"]),
            desc="List directory contents ('list all' for detailed view)",
            needs_arg=False,
            mode="stream",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "open": Command(
            run=lambda path: [],  # handled specially in main loop
            desc="Open a folder (cd) or file (xdg-open); no arg shows cwd",
            needs_arg=False,
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
    }

    if manager:
        commands["install"] = Command(
            run=lambda pkg: MANAGER_PKG[manager]["install"](pkg),
            desc=f"Install a package (via {MANAGER_DISPLAY_NAME[manager]})",
            needs_sudo=True,
            mode="stream",
            arg_completions=names["available_pkg"],
            arg_completion_kind="available_pkg",
        )
        commands["remove"] = Command(
            run=lambda pkg: MANAGER_PKG[manager]["remove"](pkg),
            desc=f"Remove a package (via {MANAGER_DISPLAY_NAME[manager]})",
            needs_sudo=True,
            mode="stream",
            arg_completions=names["installed_pkg"],
            arg_completion_kind="installed_pkg",
        )
        commands["update"] = Command(
            run=lambda _: ["bash", "-c", MANAGER_UPDATE_CMDS[manager]],
            desc=f"Update the system (via {MANAGER_DISPLAY_NAME[manager]})",
            needs_arg=False,
            needs_sudo=True,
            mode="stream",
        )

    return commands


# --------------------------------------------------------------------------
# Unified resolver: works for real commands AND builtins alike, so 'hel'
# resolves to 'help' and 'cl' resolves to 'clear' exactly like 'sta'
# resolves to 'status' — same rule, same code path, everywhere.
# --------------------------------------------------------------------------

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


def completion_matches(text_before_cursor: str, commands: dict, all_names: List[str]) -> List[str]:
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
    if not cmd.needs_arg or not cmd.arg_completions:
        return []

    word = words[-1]
    min_prefix = MIN_ARG_PREFIX_CHARS.get(cmd.arg_completion_kind or "", 0)
    if len(word) < min_prefix:
        return []

    matches = []
    word_lower = word.lower()
    for cand in cmd.arg_completions():
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
        word = text.split(" ")[-1]
        matches = completion_matches(text, self.commands, self.all_names)

        for match in matches:
            cmd = self.commands.get(match)
            meta = cmd.desc if cmd else BUILTIN_DESCRIPTIONS.get(match, "")
            yield Completion(match, start_position=-len(word), display_meta=meta)


# --------------------------------------------------------------------------
# Output rendering
# --------------------------------------------------------------------------

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

    console.print(Panel(output or "(no output)", title=label, border_style=border, expand=False))


def print_help(commands: dict, manager: Optional[str]):
    table = Table(title="lazyctl — available commands", show_lines=False)
    table.add_column("Command", style="bold cyan")
    table.add_column("Description")
    for name, cmd in commands.items():
        desc = cmd.desc + ("  [dim](sudo)[/dim]" if cmd.needs_sudo else "")
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


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

def main():
    manager = detect_package_manager()

    services = load_service_names()
    installed_pkgs = load_installed_packages(manager)               # fast enough to load synchronously
    available_pkgs_holder = BackgroundNames(
        lambda: load_available_packages(manager),
        start_immediately=False,
    )

    names = {
        "service": lambda: services,
        "installed_pkg": lambda: installed_pkgs,
        "available_pkg": available_pkgs_holder.get,
        "path": load_path_entries,
    }

    commands = build_commands(manager, names)
    all_names = list(commands.keys()) + list(BUILTIN_DESCRIPTIONS.keys())
    completer = LazyCompleter(commands)

    style = Style.from_dict({
        "prompt": "bold ansigreen",
        "bottom-toolbar": "bg:#222222 #aaaaaa",
    })

    def bottom_toolbar():
        return HTML(" <b>Tab</b> complete  •  <b>Enter</b> run  •  <b>Ctrl-D</b> quit  •  type 'help'")

    session = PromptSession(
        history=InMemoryHistory(),
        completer=completer,
        complete_while_typing=True,
        complete_in_thread=True,
        style=style,
        bottom_toolbar=bottom_toolbar,
    )

    distro = read_os_pretty_name()
    mgr_label = MANAGER_DISPLAY_NAME.get(manager, "none detected")
    console.print(Panel.fit(
        Text("lazyctl", style="bold green") + Text(f"  —  {distro}  •  package manager: {mgr_label}"),
        border_style="green",
    ))
    console.print("[dim]Type 'help' to see commands, or just start typing (e.g. 'sta nginx').[/dim]\n")

    while True:
        try:
            cwd = os.getcwd()
            home = os.path.expanduser("~")
            display_cwd = cwd.replace(home, "~", 1) if cwd.startswith(home) else cwd
            line = session.prompt([("class:prompt", f"lazyctl {display_cwd}> ")]).strip()
        except EOFError:
            break
        except KeyboardInterrupt:
            continue

        if not line:
            continue

        try:
            tokens = shlex.split(line)
        except ValueError as e:
            console.print(f"[red]Parse error: {e}[/red]")
            continue

        head, *rest = tokens

        name, ambiguous = resolve_command(all_names, head)

        if name is None:
            if ambiguous:
                console.print(
                    f"[yellow]'{head}' is ambiguous — did you mean:[/yellow] "
                    + ", ".join(f"[bold]{m}[/bold]" for m in ambiguous)
                )
            else:
                # Fallback: run as a raw Linux command
                try:
                    subprocess.run(line, shell=True)
                except Exception as e:
                    console.print(f"[red]Command failed: {e}[/red]")
            continue

        if name == "help":
            print_help(commands, manager)
            continue
        if name == "clear":
            console.clear()
            continue
        if name in ("exit", "quit"):
            break

        if name == "open":
            if not rest:
                console.print(f"[cyan]📂 {os.getcwd()}[/cyan]")
            else:
                target = os.path.expanduser(rest[0])
                if os.path.isdir(target):
                    try:
                        os.chdir(target)
                        console.print(f"[green]→ {os.getcwd()}[/green]")
                    except OSError as e:
                        console.print(f"[red]{e}[/red]")
                elif os.path.isfile(target):
                    try:
                        subprocess.run(["xdg-open", target])
                    except FileNotFoundError:
                        console.print(f"[red]'xdg-open' not found — cannot open files.[/red]")
                else:
                    console.print(f"[red]'{rest[0]}' — no such file or directory.[/red]")
            continue

        cmd = commands[name]

        if cmd.needs_arg and not rest:
            console.print(f"[yellow]'{name}' needs an argument, e.g.:[/yellow] {name} <name>")
            continue

        arg = rest[0] if rest else ""
        real_cmd = with_privilege(cmd.run(arg), cmd.needs_sudo)

        if cmd.mode == "stream":
            # Live/interactive commands (install, update, live-tail, reboot...)
            # inherit the real terminal so sudo prompts, progress bars, and
            # Ctrl-C all work naturally. Ctrl-C here only stops this command.
            try:
                subprocess.run(real_cmd)
            except FileNotFoundError:
                console.print(f"[red]'{real_cmd[0]}' not found on this system.[/red]")
            except KeyboardInterrupt:
                console.print("\n[dim]stopped.[/dim]")
            continue

        try:
            proc = subprocess.run(real_cmd, capture_output=True, text=True, timeout=15)
        except FileNotFoundError:
            console.print(f"[red]'{real_cmd[0]}' not found on this system.[/red]")
            continue
        except subprocess.TimeoutExpired:
            console.print("[red]Command timed out.[/red]")
            continue

        render_result(proc, label=" ".join(real_cmd))

    console.print("[dim]bye[/dim]")


if __name__ == "__main__":
    main()
