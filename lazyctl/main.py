import os
import shlex
import shutil
import subprocess
import getpass
from datetime import datetime
from prompt_toolkit import PromptSession, prompt
from prompt_toolkit.completion import DummyCompleter, WordCompleter
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML
from rich.text import Text
from rich.panel import Panel

from lazyctl.config import (
    IS_WINDOWS,
    IS_MACOS,
    IS_WINDOWS_TERMINAL,
    console,
    detect_package_manager,
    read_os_pretty_name,
    detect_editor,
    get_git_branch,
    with_privilege,
)
from lazyctl.loaders import (
    load_service_names,
    load_installed_packages,
    load_available_packages,
    load_path_entries,
    load_adapters,
    BackgroundNames,
    MANAGER_PKG,
    MANAGER_DISPLAY_NAME,
)
from lazyctl.commands import build_commands, BUILTIN_DESCRIPTIONS
from lazyctl.ui import (
    LazyCompleter,
    resolve_command,
    print_help,
    render_result,
    configure_macos_network,
    configure_windows_network,
)
from lazyctl.translation import translate_cross_platform


def detect_user_shell() -> str:
    """Return the user's login shell binary path (e.g. /bin/bash, /bin/zsh)."""
    if IS_WINDOWS:
        return os.environ.get("COMSPEC") or "cmd.exe"
    shell = os.environ.get("SHELL", "")
    if shell:
        return shell
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_shell or "/bin/sh"
    except Exception:
        return "/bin/sh"


def shell_rc_file(shell: str) -> str:
    """Return the primary rc file path for the given shell binary."""
    if IS_WINDOWS:
        return os.path.expanduser(r"~\lazyctl-aliases.cmd")
    name = os.path.basename(shell)
    rc_map = {
        "bash":  os.path.expanduser("~/.bashrc"),
        "zsh":   os.path.expanduser("~/.zshrc"),
        "fish":  os.path.expanduser("~/.config/fish/config.fish"),
        "ksh":   os.path.expanduser("~/.kshrc"),
        "dash":  os.path.expanduser("~/.dashrc"),
    }
    return rc_map.get(name, os.path.expanduser("~/.bashrc"))


def load_shell_aliases(shell: str) -> dict:
    """Ask the user's shell to dump all its aliases and return them as a dict."""
    if IS_WINDOWS:
        return {}
    try:
        # -i = interactive (sources rc), -c 'alias' prints all aliases
        result = subprocess.run(
            [shell, "-i", "-c", "alias"],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "PS1": "_"}   # suppress PS1 noise
        )
        aliases = {}
        for line in result.stdout.splitlines():
            # Formats:  alias ll='ls -la'   OR   ll='ls -la'
            line = line.strip()
            if line.startswith("alias "):
                line = line[6:]
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'\"")
            if key:
                aliases[key] = val
        return aliases
    except Exception:
        return {}


def expand_aliases(line: str, aliases: dict) -> str:
    """Expand the first token of a command line if it matches a known alias."""
    if not aliases:
        return line
    try:
        tokens = shlex.split(line, posix=not IS_WINDOWS)
    except ValueError:
        return line
    if not tokens:
        return line
    head = tokens[0]
    if head in aliases:
        expanded = aliases[head]
        rest = " ".join(shlex.quote(t) for t in tokens[1:])
        return (expanded + " " + rest).strip() if rest else expanded
    return line


def write_alias_to_rc(shell: str, name: str, value: str) -> str:
    """Append an alias definition to the user's rc file. Returns the rc path."""
    rc = shell_rc_file(shell)
    if IS_WINDOWS:
        line = f"\ndoskey {name}={value} $*\n"
        with open(rc, "a") as f:
            f.write(line)
        return rc
    shell_name = os.path.basename(shell)
    if shell_name == "fish":
        line = f"\nabbr --add {name} '{value}'\n"
    else:
        line = f"\nalias {name}='{value}'\n"
    with open(rc, "a") as f:
        f.write(line)
    return rc


def run_shell_line(line: str, shell: str):
    if IS_WINDOWS:
        subprocess.run(line, shell=True)
    else:
        subprocess.run(line, shell=True, executable=shell)


def main():
    # Clear the terminal on startup for a clean slate
    os.system("cls" if IS_WINDOWS else "clear")

    manager = detect_package_manager()
    shell = detect_user_shell()
    aliases = load_shell_aliases(shell)

    services = load_service_names()
    installed_pkgs = load_installed_packages(manager)               # fast enough to load synchronously

    # For winget, available package search is done live at completion time
    # (passing the current prefix to `winget search`). For all other managers
    # we pre-load the full list in the background as before.
    if manager and MANAGER_PKG[manager].get("available_cmd") == "winget_search":
        available_pkg_getter = lambda prefix="": load_available_packages(manager, prefix)
    else:
        available_pkgs_holder = BackgroundNames(
            lambda: load_available_packages(manager),
            start_immediately=False,
        )
        available_pkg_getter = available_pkgs_holder.get

    names = {
        "service": lambda: services,
        "installed_pkg": lambda: installed_pkgs,
        "available_pkg": available_pkg_getter,
        "path": load_path_entries,
        "adapter": load_adapters,
    }

    commands = build_commands(manager, names)
    all_names = list(commands.keys()) + list(BUILTIN_DESCRIPTIONS.keys())
    completer = LazyCompleter(commands)

    style = Style.from_dict({
        "lazyctl": "bg:#f38ba8 fg:#1e1e2e bold",
        "lazyctl_sep": "fg:#f38ba8 bg:#fab387",
        "user": "bg:#fab387 fg:#1e1e2e bold",
        "user_sep": "fg:#fab387 bg:#a6e3a1",
        "cwd": "bg:#a6e3a1 fg:#1e1e2e bold",
        "cwd_sep": "fg:#a6e3a1 bg:#89b4fa",
        "cwd_sep_git": "fg:#a6e3a1 bg:#cba6f7",
        "git": "bg:#cba6f7 fg:#1e1e2e bold",
        "git_sep": "fg:#cba6f7 bg:#89b4fa",
        "time": "bg:#89b4fa fg:#1e1e2e bold",
        "time_sep": "fg:#89b4fa",
        "bottom-toolbar": "bg:#222222 #aaaaaa",
    })

    def bottom_toolbar():
        return HTML(" <b>Tab</b> complete  •  <b>Enter</b> run  •  <b>Ctrl-D</b> quit  •  type 'help'")

    session = PromptSession(
        history=InMemoryHistory(),
        completer=completer,
        complete_while_typing=True,
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
            
            user = getpass.getuser()
            now = datetime.now().strftime("%H:%M")
            branch = get_git_branch()
            
            # Powerline arrow glyph and git icon need a Nerd Font.
            # Linux/Windows Terminal: use powerline glyphs.
            # Plain cmd.exe: fall back to plain > so the prompt still
            # looks structured even without a special font installed.
            use_powerline = (not IS_WINDOWS) or IS_WINDOWS_TERMINAL
            sep      = "\ue0b0" if use_powerline else ">"
            git_icon = " " if use_powerline else ""

            prompt_fragments = [
                ("class:lazyctl", " lazyctl "),
                ("class:lazyctl_sep", sep),
                ("class:user", f" {user} "),
                ("class:user_sep", sep),
                ("class:cwd", f" {display_cwd} "),
            ]

            if branch:
                prompt_fragments.extend([
                    ("class:cwd_sep_git", sep),
                    ("class:git", f" {git_icon}{branch} "),
                    ("class:git_sep", sep),
                ])
            else:
                prompt_fragments.append(("class:cwd_sep", sep))

            prompt_fragments.extend([
                ("class:time", f" {now} "),
                ("class:time_sep", sep),
                ("", " "),
            ])
            
            line = session.prompt(prompt_fragments).strip()
        except EOFError:
            break
        except KeyboardInterrupt:
            continue

        if not line:
            continue

        try:
            tokens = shlex.split(line, posix=not IS_WINDOWS)
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
                # Try cross-platform translation first (cp->copy, del->rm, etc.)
                translated = translate_cross_platform(tokens)
                if translated is not None:
                    try:
                        run_shell_line(translated, shell)
                    except Exception as e:
                        console.print(f"[red]Command failed: {e}[/red]")
                else:
                    # Fallback: expand aliases then run as a raw shell command
                    expanded = expand_aliases(line, aliases)
                    try:
                        run_shell_line(expanded, shell)
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

        if name in ("open", "cd"):
            if not rest:
                if name == "cd":
                    target = os.path.expanduser("~")
                    try:
                        os.chdir(target)
                        console.print(f"[green]→ {os.getcwd()}[/green]")
                    except OSError as e:
                        console.print(f"[red]{e}[/red]")
                else:
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
                    if name == "cd":
                        console.print(f"[red]'{target}' is not a directory.[/red]")
                    else:
                        # Check for "in <editor>" syntax: open file in vim
                        editor = None
                        if len(rest) >= 3 and rest[1].lower() == "in":
                            editor = rest[2]
                            if not shutil.which(editor):
                                console.print(f"[red]Editor '{editor}' not found on this system.[/red]")
                                continue
                        if not editor:
                            editor = detect_editor()
                        if not editor:
                            console.print(f"[red]No text editor found (tried nano, vim, vi, micro).[/red]")
                            continue
                        try:
                            subprocess.run([editor, target])
                        except FileNotFoundError:
                            console.print(f"[red]'{editor}' not found.[/red]")
                else:
                    console.print(f"[red]'{rest[0]}' — no such file or directory.[/red]")
            continue

        if name == "back":
            try:
                os.chdir("..")
                console.print(f"[green]→ {os.getcwd()}[/green]")
            except OSError as e:
                console.print(f"[red]{e}[/red]")
            continue

        if name == "alias":
            shell_name = os.path.basename(shell)
            rc = shell_rc_file(shell)
            try:
                console.print(f"\n[bold cyan]Alias Wizard[/bold cyan]  (shell: [green]{shell_name}[/green]  •  rc: [dim]{rc}[/dim])")
                alias_name = prompt(
                    [("class:prompt", "Alias name (shortcut): ")],
                    completer=DummyCompleter(), style=style
                ).strip()
                if not alias_name:
                    console.print("[red]Alias name cannot be empty. Aborting.[/red]")
                    continue
                if " " in alias_name:
                    console.print("[red]Alias name must not contain spaces. Aborting.[/red]")
                    continue
                alias_val = prompt(
                    [("class:prompt", f"Command for '{alias_name}': ")],
                    completer=DummyCompleter(), style=style
                ).strip()
                if not alias_val:
                    console.print("[red]Command cannot be empty. Aborting.[/red]")
                    continue

                rc_path = write_alias_to_rc(shell, alias_name, alias_val)
                # Also register it live for this session
                aliases[alias_name] = alias_val
                console.print(f"[bold green]Alias added![/bold green] [cyan]{alias_name}[/cyan] → [yellow]{alias_val}[/yellow]")
                if IS_WINDOWS:
                    console.print(f"[dim]Saved to {rc_path} — run it in a new Command Prompt to apply globally.[/dim]")
                else:
                    console.print(f"[dim]Saved to {rc_path} — run 'source {rc_path}' in a new terminal to apply globally.[/dim]")
            except KeyboardInterrupt:
                console.print("\n[dim]Cancelled.[/dim]")
            continue

        # ── Universal file-system operations (Python shutil) ─────────────────
        if name == "copy":
            if not rest:
                console.print("[yellow]Usage: copy <src> <dest>[/yellow]")
                continue
            src  = os.path.expanduser(rest[0])
            dest = os.path.expanduser(rest[1]) if len(rest) > 1 else "."
            try:
                if os.path.isdir(src):
                    dst = dest if not os.path.exists(dest) else os.path.join(dest, os.path.basename(src))
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dest)
                console.print(f"[green]Copied[/green] {src} → {dest}")
            except Exception as e:
                console.print(f"[red]copy: {e}[/red]")
            continue

        if name == "move":
            if len(rest) < 2:
                console.print("[yellow]Usage: move <src> <dest>[/yellow]")
                continue
            src  = os.path.expanduser(rest[0])
            dest = os.path.expanduser(rest[1])
            try:
                shutil.move(src, dest)
                console.print(f"[green]Moved[/green] {src} → {dest}")
            except Exception as e:
                console.print(f"[red]move: {e}[/red]")
            continue

        if name == "remove":
            if not rest:
                console.print("[yellow]Usage: remove <path>[/yellow]")
                continue
            target = os.path.expanduser(rest[0])
            try:
                if os.path.isdir(target):
                    if os.listdir(target):  # non-empty dir — ask first
                        ans = prompt(
                            [("class:prompt", f"Remove '{target}' and all its contents? [y/N]: ")],
                            style=style,
                        ).strip().lower()
                        if ans != "y":
                            console.print("[dim]Cancelled.[/dim]")
                            continue
                    shutil.rmtree(target)
                else:
                    os.remove(target)
                console.print(f"[green]Removed[/green] {target}")
            except Exception as e:
                console.print(f"[red]remove: {e}[/red]")
            continue

        if name == "mkdir":
            if not rest:
                console.print("[yellow]Usage: mkdir <path>[/yellow]")
                continue
            try:
                os.makedirs(os.path.expanduser(rest[0]), exist_ok=True)
                console.print(f"[green]Created[/green] {rest[0]}")
            except Exception as e:
                console.print(f"[red]mkdir: {e}[/red]")
            continue

        if name == "netconfig":
            if not rest:
                console.print("[yellow]Please specify an adapter, e.g., 'netconfig eth0'[/yellow]")
                continue
            if IS_WINDOWS:
                configure_windows_network(rest[0], style)
                continue
            if IS_MACOS:
                configure_macos_network(rest[0], style)
                continue
            adapter = rest[0]
            if not os.path.exists(f"/sys/class/net/{adapter}"):
                console.print(f"[red]Adapter '{adapter}' not found on this system.[/red]")
                continue

            if not shutil.which("nmcli"):
                console.print("[red]NetworkManager (nmcli) is not installed. Currently, only NetworkManager is supported for this feature.[/red]")
                continue

            conn_name = None
            try:
                out = subprocess.run(["nmcli", "-t", "-f", "NAME,DEVICE", "con", "show"], capture_output=True, text=True)
                for line in out.stdout.splitlines():
                    if ":" in line:
                        cname, dev = line.split(":", 1)
                        if dev == adapter:
                            conn_name = cname
                            break
            except Exception:
                pass
                
            try:
                console.print(f"\n[bold cyan]Configuring {adapter}[/bold cyan] (Current connection profile: {conn_name or 'none'})")
                
                mode_completer = WordCompleter(["dhcp", "static", "up", "down"], ignore_case=True)
                mode = prompt([("class:prompt", "Action [dhcp/static/up/down]: ")], completer=mode_completer, style=style).strip().lower()
                
                if mode not in ("dhcp", "static", "up", "down"):
                    console.print("[red]Invalid action. Aborting.[/red]")
                    continue
                
                if not conn_name:
                    if mode in ("up", "down"):
                        console.print(f"[red]Cannot bring {mode} a non-existent connection. Please use dhcp or static first.[/red]")
                        continue
                    console.print("[yellow]No existing connection found. Creating a new one...[/yellow]")
                    subprocess.run(with_privilege(["nmcli", "con", "add", "type", "ethernet", "ifname", adapter, "con-name", adapter], True), check=True)
                    conn_name = adapter
                
                if mode == "up":
                    console.print(f"[green]Bringing up {conn_name}...[/green]")
                    subprocess.run(with_privilege(["nmcli", "con", "up", conn_name], True))
                    console.print("[bold green]Done.[/bold green]")
                elif mode == "down":
                    console.print(f"[yellow]Bringing down {conn_name}...[/yellow]")
                    subprocess.run(with_privilege(["nmcli", "con", "down", conn_name], True))
                    console.print("[bold yellow]Done.[/bold yellow]")
                elif mode == "dhcp":
                    console.print(f"[green]Applying DHCP to {conn_name}...[/green]")
                    subprocess.run(with_privilege(["nmcli", "con", "mod", conn_name, "ipv4.method", "auto"], True), check=True)
                    subprocess.run(with_privilege(["nmcli", "con", "up", conn_name], True))
                    console.print("[bold green]Success![/bold green]")
                else:
                    empty = DummyCompleter()
                    ip_addr = prompt([("class:prompt", "IP Address with subnet (e.g. 192.168.1.50/24): ")], completer=empty, style=style).strip()
                    gw = prompt([("class:prompt", "Gateway (e.g. 192.168.1.1): ")], completer=empty, style=style).strip()
                    dns = prompt([("class:prompt", "DNS (e.g. 8.8.8.8): ")], completer=empty, style=style).strip()
                    
                    if not ip_addr:
                        console.print("[red]IP address is required. Aborting.[/red]")
                        continue
                        
                    cmds = ["nmcli", "con", "mod", conn_name, "ipv4.method", "manual", "ipv4.addresses", ip_addr]
                    if gw:
                        cmds.extend(["ipv4.gateway", gw])
                    if dns:
                        cmds.extend(["ipv4.dns", dns])
                        
                    console.print(f"[green]Applying static IP to {conn_name}...[/green]")
                    subprocess.run(with_privilege(cmds, True), check=True)
                    subprocess.run(with_privilege(["nmcli", "con", "up", conn_name], True))
                    console.print("[bold green]Success![/bold green]")
            except subprocess.CalledProcessError as e:
                console.print(f"[red]NetworkManager error: {e}[/red]")
            except KeyboardInterrupt:
                console.print("\n[dim]Cancelled.[/dim]")
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
