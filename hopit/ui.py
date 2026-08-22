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

from hopit.config import IS_WINDOWS, IS_MACOS, console, with_privilege, get_active_theme
from hopit.loaders import load_adapters
from hopit.commands import BUILTIN_DESCRIPTIONS

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

def get_git_completions(words: list[str]) -> list[tuple[str, str]]:
    if len(words) == 2:
        git_subcommand_descs = {
            "status": "Show working tree status",
            "log": "Show commit logs",
            "branch": "List, create, or delete branches",
            "diff": "Show changes between commits/files",
            "add": "Add file contents to the index",
            "commit": "Record changes to the repository",
            "push": "Update remote refs and commits",
            "pull": "Fetch and integrate with remote",
            "checkout": "Switch branches or restore files",
            "clone": "Clone a repository into a new directory",
        }
        return [(cmd, desc) for cmd, desc in git_subcommand_descs.items()]

    subcmd = words[1].lower()
    word = words[-1]

    # 1. Get git status changes
    changed_files = {}
    try:
        res = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=1)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if len(line) > 3:
                    status = line[:2]
                    filename = line[3:].strip()
                    if "M" in status:
                        desc = "⚠️ modified"
                    elif "?" in status:
                        desc = "🆕 untracked"
                    elif "D" in status:
                        desc = "❌ deleted"
                    elif "A" in status:
                        desc = "🟢 staged"
                    else:
                        desc = "changed file"
                    changed_files[filename] = desc
    except Exception:
        pass

    # 2. Get git branch names
    branches = []
    try:
        res = subprocess.run(["git", "branch", "--format=%(refname:short)"], capture_output=True, text=True, timeout=1)
        if res.returncode == 0:
            branches = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    except Exception:
        pass

    if subcmd in ("add", "rm", "restore", "stage", "reset"):
        candidates = []
        for fn, desc in changed_files.items():
            candidates.append((fn, desc))
        from hopit.loaders import load_path_entries
        paths = load_path_entries(word)
        existing = {c[0] for c in candidates}
        for p in paths:
            if p not in existing:
                desc = "📁 folder" if os.path.isdir(p) else "📄 file"
                candidates.append((p, desc))
        return candidates

    elif subcmd in ("checkout", "switch"):
        candidates = []
        for b in branches:
            candidates.append((b, "🌿 branch"))
        for fn, desc in changed_files.items():
            candidates.append((fn, desc))
        from hopit.loaders import load_path_entries
        paths = load_path_entries(word)
        existing = {c[0] for c in candidates}
        for p in paths:
            if p not in existing:
                desc = "📁 folder" if os.path.isdir(p) else "📄 file"
                candidates.append((p, desc))
        return candidates

    elif subcmd in ("branch", "merge", "rebase", "push", "pull"):
        return [(b, "🌿 branch") for b in branches]

    elif subcmd == "diff":
        candidates = []
        for fn, desc in changed_files.items():
            candidates.append((fn, desc))
        from hopit.loaders import load_path_entries
        paths = load_path_entries(word)
        existing = {c[0] for c in candidates}
        for p in paths:
            if p not in existing:
                desc = "📁 folder" if os.path.isdir(p) else "📄 file"
                candidates.append((p, desc))
        return candidates

    return []


def get_user_group_perm_completions(words: list[str], commands: dict) -> list[tuple[str, str]]:
    if len(words) < 2:
        return []
    cmd = words[0].lower()
    
    if cmd == "user":
        if len(words) == 2:
            user_subs = {
                "add": "Add a new system user",
                "remove": "Delete a system user",
                "delete": "Delete a system user",
                "passwd": "Change a user's password",
                "password": "Change a user's password",
                "join": "Add a user to a group",
                "list": "List system users",
            }
            return [(k, v) for k, v in user_subs.items()]
            
        sub = words[1].lower()
        word = words[-1]
        
        if sub in ("remove", "delete", "passwd", "password"):
            from hopit.loaders import load_users
            users = load_users()
            return [(u, "👤 user") for u in users]
            
        elif sub == "join":
            if len(words) == 3:
                from hopit.loaders import load_groups
                groups = load_groups()
                return [(g, "👥 group") for g in groups]
            elif len(words) == 4:
                from hopit.loaders import load_users
                users = load_users()
                return [(u, "👤 user") for u in users]
                
    elif cmd == "group":
        if len(words) == 2:
            group_subs = {
                "add": "Add a new system group",
                "remove": "Delete a system group",
                "delete": "Delete a system group",
                "list": "List system groups",
            }
            return [(k, v) for k, v in group_subs.items()]
            
        sub = words[1].lower()
        if sub in ("remove", "delete"):
            from hopit.loaders import load_groups
            groups = load_groups()
            return [(g, "👥 group") for g in groups]
            
    elif cmd in ("permission", "permissions"):
        if len(words) == 2:
            perm_subs = {
                "set": "Set read/write/execute permissions (chmod)",
                "owner": "Change owner of file/folder (chown)",
                "group": "Change group of file/folder (chgrp)",
            }
            return [(k, v) for k, v in perm_subs.items()]
            
        sub = words[1].lower()
        word = words[-1]
        if sub == "set":
            if len(words) == 3:
                return [("755", "rwxr-xr-x"), ("644", "rw-r--r--"), ("700", "rwx------"), ("+x", "make executable")]
            elif len(words) >= 4:
                from hopit.loaders import load_path_entries
                paths = load_path_entries(word)
                return [(p, "📁 folder" if os.path.isdir(p) else "📄 file") for p in paths]
                
        elif sub == "owner":
            if len(words) == 3:
                from hopit.loaders import load_users
                users = load_users()
                return [(u, "👤 user") for u in users]
            elif len(words) >= 4:
                from hopit.loaders import load_path_entries
                paths = load_path_entries(word)
                return [(p, "📁 folder" if os.path.isdir(p) else "📄 file") for p in paths]
                
        elif sub == "group":
            if len(words) == 3:
                from hopit.loaders import load_groups
                groups = load_groups()
                return [(g, "👥 group") for g in groups]
            elif len(words) >= 4:
                from hopit.loaders import load_path_entries
                paths = load_path_entries(word)
                return [(p, "📁 folder" if os.path.isdir(p) else "📄 file") for p in paths]

    elif cmd == "firewall":
        if len(words) == 2:
            fw_subs = {
                "status": "Check active firewall rule profiles and status",
                "allow": "Allow inbound traffic on port",
                "block": "Block inbound traffic on port",
                "delete": "Delete a specific firewall rule by ID or port",
            }
            return [(k, v) for k, v in fw_subs.items()]

        sub = words[1].lower()
        if sub in ("delete", "remove"):
            if len(words) == 3:
                try:
                    from hopit.firewall import parse_firewall_rules
                    rules = parse_firewall_rules()
                    return [(str(r["id"]), f"Rule #{r['id']}: {r['name']} ({r['port']}/{r['proto']})") for r in rules]
                except Exception:
                    return [("1", "Rule #1"), ("2", "Rule #2")]
        elif sub in ("allow", "block", "deny"):
            if len(words) == 3:
                return [
                    ("<port>", "Or type any custom port number / range of your choosing"),
                    ("80", "HTTP Web Server"),
                    ("443", "HTTPS Secure Web"),
                    ("22", "SSH Remote Access"),
                    ("8080", "Web Alt / App Server"),
                    ("3306", "MySQL Database"),
                    ("5432", "PostgreSQL Database"),
                    ("27017", "MongoDB Database"),
                    ("6379", "Redis Cache"),
                ]
            elif len(words) == 4:
                return [
                    ("tcp", "Transmission Control Protocol (Default)"),
                    ("udp", "User Datagram Protocol"),
                    ("both", "Both TCP and UDP protocols"),
                ]
            elif len(words) == 5:
                from hopit.loaders import load_adapters
                ifaces = load_adapters()
                res = [("all", "All network interfaces")]
                for i in ifaces:
                    if i != "all":
                        res.append((i, "🌐 network adapter"))
                return res

    elif cmd in ("disk", "drive"):
        if len(words) == 2:
            disk_subs = {
                "list": "List physical drives and volumes",
                "usage": "Check disk space utilization",
                "mount": "Mount a drive or partition",
                "unmount": "Unmount a mounted volume",
                "check": "Perform filesystem integrity check",
            }
            return [(k, v) for k, v in disk_subs.items()]

    elif cmd in ("archive", "compress"):
        if len(words) == 2:
            arch_subs = {
                "create": "Create a compressed archive (.zip, .tar.gz)",
                "extract": "Extract files from a compressed archive",
            }
            return [(k, v) for k, v in arch_subs.items()]
        elif len(words) >= 3:
            from hopit.loaders import load_path_entries
            word = words[-1]
            paths = load_path_entries(word)
            return [(p, "📁 folder" if os.path.isdir(p) else "📄 file") for p in paths]

    elif cmd == "create":
        if len(words) == 2:
            create_subs = {
                "folder": "Create a new directory (including parent directories)",
                "file": "Create a new empty file",
            }
            return [(k, v) for k, v in create_subs.items()]
        elif len(words) >= 3:
            from hopit.loaders import load_path_entries
            word = words[-1]
            paths = load_path_entries(word)
            return [(p, "📁 folder" if os.path.isdir(p) else "📄 file") for p in paths]

    elif cmd == "show":
        if len(words) == 2:
            show_subs = {
                "file": "Show contents of a file (cat)",
                "start": "Show the first N lines of a file (head)",
                "end": "Show the last N lines of a file (tail)",
                "tree": "Show directory structure in a tree (tree)",
                "env": "View or filter environment variables (env)",
                "history": "Show the session command history (history)",
            }
            return [(k, v) for k, v in show_subs.items()]
        elif len(words) >= 3:
            sub = words[1].lower()
            if sub in ("file", "start", "end", "tree"):
                from hopit.loaders import load_path_entries
                word = words[-1]
                paths = load_path_entries(word)
                return [(p, "📁 folder" if os.path.isdir(p) else "📄 file") for p in paths]
                
    return []


class LazyCompleter(Completer):
    def __init__(self, commands: dict):
        self.commands = commands
        self.all_names = [k for k in commands if k not in ("permissions", "drive", "compress")] + list(BUILTIN_DESCRIPTIONS.keys())

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        words = text.split(" ")
        words = [w for w in words[:-1] if w] + [words[-1]]
        word = words[-1]

        # Check if we are completing for git or structured subcommands
        if len(words) > 1:
            head = words[0].lower()
            resolved, _ = resolve_command(self.all_names, head)
            if head == "permissions":
                resolved = "permission"
            elif head == "drive":
                resolved = "disk"
            elif head == "compress":
                resolved = "archive"

            if resolved == "git":
                git_candidates = get_git_completions(words)
                word_lower = word.lower()
                matches = []
                for cand, meta in git_candidates:
                    if cand.lower().startswith(word_lower):
                        matches.append((cand, meta))
                        if len(matches) >= MAX_ARG_COMPLETIONS:
                            break
                for match, meta in matches:
                    yield Completion(match, start_position=-len(word), display_meta=meta)
                return
            elif resolved == "config":
                from hopit.config import THEMES
                if len(words) == 2:
                    candidates = [("set", "Change a configuration setting"), ("reset", "Reset configuration to defaults")]
                elif len(words) == 3 and words[1].lower() == "set":
                    candidates = [
                        ("theme", "Color scheme appearance"),
                        ("editor", "Default text editor"),
                        ("package_manager", "Default package manager override"),
                    ]
                elif len(words) == 4 and words[1].lower() == "set" and words[2].lower() == "theme":
                    candidates = [(k, t["name"]) for k, t in THEMES.items()]
                elif len(words) == 4 and words[1].lower() == "set" and words[2].lower() == "editor":
                    candidates = [("nano", "Nano text editor"), ("vim", "Vim text editor"), ("code", "VS Code editor"), ("micro", "Micro text editor")]
                elif len(words) == 4 and words[1].lower() == "set" and words[2].lower() == "package_manager":
                    candidates = [("apt-get", "APT package manager"), ("dnf", "DNF package manager"), ("pacman", "Pacman package manager"), ("brew", "Homebrew"), ("winget", "Windows Package Manager")]
                else:
                    candidates = []

                word_lower = word.lower()
                matches = []
                for cand, meta in candidates:
                    if cand.lower().startswith(word_lower):
                        matches.append((cand, meta))
                        if len(matches) >= MAX_ARG_COMPLETIONS:
                            break
                for match, meta in matches:
                    yield Completion(match, start_position=-len(word), display_meta=meta)
                return
            elif resolved in ("user", "group", "permission", "permissions", "firewall", "disk", "drive", "archive", "compress", "show"):
                candidates = get_user_group_perm_completions(words, self.commands)
                word_lower = word.lower()
                matches = []
                for cand, meta in candidates:
                    if cand.lower().startswith(word_lower):
                        matches.append((cand, meta))
                        if len(matches) >= MAX_ARG_COMPLETIONS:
                            break
                for match, meta in matches:
                    yield Completion(match, start_position=-len(word), display_meta=meta)
                return
            elif resolved == "create":
                if len(words) >= 3:
                    yield Completion(
                        "",
                        start_position=0,
                        display="💡 Enter name to create here or full path",
                        display_meta="info"
                    )
                candidates = get_user_group_perm_completions(words, self.commands)
                word_lower = word.lower()
                matches = []
                for cand, meta in candidates:
                    if cand.lower().startswith(word_lower):
                        matches.append((cand, meta))
                        if len(matches) >= MAX_ARG_COMPLETIONS:
                            break
                for match, meta in matches:
                    yield Completion(match, start_position=-len(word), display_meta=meta)
                return

        matches = completion_matches(text, self.commands, self.all_names)

        # Detect if we're completing a path-type argument
        arg_kind = None
        if len(words) > 1:
            head = words[0].lower()
            resolved, _ = resolve_command(self.all_names, head)
            if resolved and resolved in self.commands:
                arg_kind = self.commands[resolved].arg_completion_kind

        git_subcommand_descs = {
            "status": "Show working tree status",
            "log": "Show commit logs",
            "branch": "List, create, or delete branches",
            "diff": "Show changes between commits, commit and working tree, etc.",
            "add": "Add file contents to the index",
            "commit": "Record changes to the repository",
            "push": "Update remote refs along with associated objects",
            "pull": "Fetch from and integrate with another repository or a local branch",
            "checkout": "Switch branches or restore working tree files",
            "clone": "Clone a repository into a new directory",
        }

        for match in matches:
            if len(words) > 1:
                # Completing an argument
                if arg_kind == "path":
                    if os.path.isdir(match):
                        meta = "📁 folder"
                    else:
                        meta = "📄 file"
                elif arg_kind == "git_subcommand":
                    meta = git_subcommand_descs.get(match.lower(), "git subcommand")
                else:
                    meta = arg_kind if arg_kind else ""
            else:
                # Completing a command name
                cmd = self.commands.get(match)
                meta = cmd.desc if cmd else BUILTIN_DESCRIPTIONS.get(match, "")
            yield Completion(match, start_position=-len(word), display_meta=meta)


def render_result(
    proc: subprocess.CompletedProcess,
    label: str,
    cmd_name: str = None,
    cmd_arg: str = None,
    show_cmd: bool = False,
):
    output = (proc.stdout or "") + (proc.stderr or "")
    output = output.rstrip("\n")

    theme = get_active_theme()
    output_lower = output.lower()
    if "active (running)" in output_lower or "running" in output_lower or "online" in output_lower:
        border = "green"
    elif "failed" in output_lower or "error" in output_lower or proc.returncode not in (0, 3):
        border = "red"
    elif "inactive" in output_lower or "dead" in output_lower or "stopped" in output_lower:
        border = "yellow"
    else:
        border = theme.get("border", "cyan")

    if output:
        lines = output.splitlines()
        styled_lines = []
        for i, line in enumerate(lines):
            line_text = Text.from_ansi(line)
            is_header = False
            if i == 0 and len(line.strip()) > 0:
                header_words = ["netid", "state", "recv-q", "send-q", "local", "peer", "command", "pid", "user", "fd", "type", "device", "node", "name", "status", "ports", "service", "displayname"]
                line_lower = line.lower()
                if any(hw in line_lower for hw in header_words) or (len(line.split()) >= 3 and all(w[0].isupper() or w[0].isdigit() or not w[0].isalpha() for w in line.split() if w)):
                    is_header = True
            
            if is_header:
                line_text.stylize("bold cyan")
            else:
                line_text.highlight_regex(r"(?i)\b(active \(running\)|running|up|online|enabled|listen|listening|estab|established|success|succeeded)\b", "bold green")
                line_text.highlight_regex(r"(?i)\b(inactive \(dead\)|inactive|dead|disabled|down|offline|unconn|stopped)\b", "bold yellow")
                line_text.highlight_regex(r"(?i)\b(failed|error|severe|critical|stopped \(failed\))\b", "bold red")
                line_text.highlight_regex(r"^\s*[A-Za-z_-]+(?:\s+[A-Za-z_-]+)*\s*:", "bold cyan")
                line_text.highlight_regex(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\b", "cyan")
                line_text.highlight_regex(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(/\d+)?(:\d+)?\b", "green")
                line_text.highlight_regex(r"\b([a-fA-F0-9:]+::[a-fA-F0-9:]*(/\d+)?|::[a-fA-F0-9:]*(/\d+)?)\b", "green")
                line_text.highlight_regex(r"\b[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}\b", "yellow")
                line_text.highlight_regex(r"\b(\*|\[::\]|localhost|\[[a-fA-F0-9:]+\]):\d+\b", "green")
                line_text.highlight_regex(r"\bpid=\d+\b", "magenta")
                line_text.highlight_regex(r"users:\(\(.*?\)\)", "magenta")
                
            styled_lines.append(line_text)
        content = Text("\n").join(styled_lines)
    else:
        if proc.returncode in (0, 3):
            if cmd_name == "start":
                content_str = f"Service '{cmd_arg}' started successfully." if cmd_arg else "Service started successfully."
            elif cmd_name == "stop":
                content_str = f"Service '{cmd_arg}' stopped successfully." if cmd_arg else "Service stopped successfully."
            elif cmd_name == "restart":
                content_str = f"Service '{cmd_arg}' restarted successfully." if cmd_arg else "Service restarted successfully."
            elif cmd_name == "install":
                content_str = f"Package '{cmd_arg}' installed successfully." if cmd_arg else "Package installed successfully."
            elif cmd_name == "uninstall":
                content_str = f"Package '{cmd_arg}' uninstalled successfully." if cmd_arg else "Package uninstalled successfully."
            else:
                content_str = "Command executed successfully."
            content = Text(content_str, style="bold green")
        else:
            if cmd_name in ("start", "stop", "restart", "install", "uninstall") and cmd_arg:
                content_str = f"Failed to run action '{cmd_name}' on '{cmd_arg}' (exit code: {proc.returncode})."
            else:
                content_str = f"Command failed (exit code: {proc.returncode})."
            content = Text(content_str, style="bold red")

    title = label if show_cmd else None
    console.print(Panel(content, title=title, border_style=border, expand=False))


def print_help(commands: dict, manager: str | None):
    table = Table(title="hopit-cli — available commands", show_lines=False)
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
        "[dim]Note: Commands support prefix auto-resolution and interactive discovery. "
        "Type '?' after any command or sub-argument for positional context and usage syntax.[/dim]"
    )
    if not manager:
        console.print("[yellow]No supported package manager detected — install/uninstall/update unavailable.[/yellow]")


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
                              "You may need to run hopit-cli as Administrator.[/yellow]")
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
                              "You may need to run hopit-cli as Administrator.[/yellow]")
    except KeyboardInterrupt:
        console.print("\n[dim]Cancelled.[/dim]")
