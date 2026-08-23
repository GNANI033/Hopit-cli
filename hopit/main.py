import os
import sys

# Ensure the parent directory containing the hopit package is in PYTHONPATH so subprocesses can locate hopit
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

existing_pythonpath = os.environ.get("PYTHONPATH", "")
if parent_dir not in existing_pythonpath.split(os.pathsep):
    if existing_pythonpath:
        os.environ["PYTHONPATH"] = f"{parent_dir}{os.pathsep}{existing_pythonpath}"
    else:
        os.environ["PYTHONPATH"] = parent_dir

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

from hopit.config import (
    IS_WINDOWS,
    IS_MACOS,
    IS_WINDOWS_TERMINAL,
    console,
    detect_package_manager,
    read_os_pretty_name,
    detect_editor,
    get_git_branch,
    with_privilege,
    get_active_theme,
    is_nerd_fonts_enabled,
    THEMES,
)
from hopit.loaders import (
    load_service_names,
    load_installed_packages,
    load_available_packages,
    load_path_entries,
    load_adapters,
    load_users,
    load_groups,
    load_block_devices,
    load_mount_points,
    BackgroundNames,
    MANAGER_PKG,
    MANAGER_DISPLAY_NAME,
)
from hopit.commands import build_commands, BUILTIN_DESCRIPTIONS, ip_cmd, disk_cmd
from hopit.ui import (
    LazyCompleter,
    resolve_command,
    print_help,
    render_result,
    configure_macos_network,
    configure_windows_network,
)
from hopit.translation import translate_cross_platform

user_session_shortcuts = set()

def create_prompt_style(theme: dict) -> Style:
    def get_fg(bg_color: str) -> str:
        try:
            bg_color = bg_color.lstrip("#")
            r, g, b = int(bg_color[0:2], 16), int(bg_color[2:4], 16), int(bg_color[4:6], 16)
            # YIQ formula for perceived brightness
            brightness = (r * 299 + g * 587 + b * 114) / 1000
            return "#111111" if brightness > 128 else "#ffffff"
        except Exception:
            return theme.get("text", "#111111")

    return Style.from_dict({
        "hopit": f"bg:{theme['hopit']} fg:{get_fg(theme['hopit'])} bold",
        "hopit_sep": f"fg:{theme['hopit']} bg:{theme['user']}",
        "user": f"bg:{theme['user']} fg:{get_fg(theme['user'])} bold",
        "user_sep": f"fg:{theme['user']} bg:{theme['cwd']}",
        "cwd": f"bg:{theme['cwd']} fg:{get_fg(theme['cwd'])} bold",
        "cwd_sep": f"fg:{theme['cwd']} bg:{theme['time']}",
        "cwd_sep_git": f"fg:{theme['cwd']} bg:{theme['git']}",
        "git": f"bg:{theme['git']} fg:{get_fg(theme['git'])} bold",
        "git_sep": f"fg:{theme['git']} bg:{theme['time']}",
        "time": f"bg:{theme['time']} fg:{get_fg(theme['time'])} bold",
        "time_sep": f"fg:{theme['time']}",
        "bottom-toolbar": "bg:#222222 #aaaaaa",
    })



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
        return os.path.expanduser(r"~\hopit-aliases.cmd")
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


def remove_alias_from_rc(shell: str, name: str) -> str:
    """Remove an alias definition from the user's rc file."""
    rc = shell_rc_file(shell)
    if not os.path.isfile(rc):
        return rc
    
    with open(rc, "r") as f:
        lines = f.readlines()
        
    shell_name = os.path.basename(shell)
    if IS_WINDOWS:
        prefix = f"doskey {name}="
    elif shell_name == "fish":
        prefix = f"abbr --add {name} "
    else:
        prefix = f"alias {name}="
        
    with open(rc, "w") as f:
        for line in lines:
            if line.strip().startswith(prefix):
                continue
            f.write(line)
            
    return rc


def run_shell_line(line: str, shell: str):
    if IS_WINDOWS:
        subprocess.run(line, shell=True)
    else:
        subprocess.run(line, shell=True, executable=shell)


def show_command_help(cmd_name: str, commands: dict):
    show_context_help([cmd_name], commands)


def resolve_subcommand(token: str, valid_subcmds: list[str]) -> tuple[str | None, list[str]]:
    token = token.lower()
    if token in valid_subcmds:
        return token, [token]
    matches = sorted(set(c for c in valid_subcmds if c.startswith(token)))
    if len(matches) == 1:
        return matches[0], matches
    return None, matches


def show_context_help(words: list[str], commands: dict):
    from rich.panel import Panel
    from rich.table import Table
    from rich.markup import escape
    
    first_word = words[0].lower()
    resolved, _ = resolve_command(list(commands.keys()) + list(BUILTIN_DESCRIPTIONS.keys()), first_word)
    if not resolved:
        console.print(f"[red]Unknown command: {first_word}[/red]")
        return
        
    subcmd = words[1].lower() if len(words) > 1 else ""
    if resolved == "show":
        matched_sub, _ = resolve_subcommand(subcmd, ["file", "start", "end", "tree", "env", "history", "arp", "mac", "gateway", "ip", "route", "hostname", "shortcut"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved == "lookup":
        matched_sub, _ = resolve_subcommand(subcmd, ["all", "a", "aaaa", "cname", "mx", "txt", "ns"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved == "create":
        matched_sub, _ = resolve_subcommand(subcmd, ["folder", "file", "shortcut", "venv"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved == "find":
        matched_sub, _ = resolve_subcommand(subcmd, ["file", "text"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved == "netconfig":
        matched_sub, _ = resolve_subcommand(subcmd, ["dhcp", "reset"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved == "enter":
        matched_sub, _ = resolve_subcommand(subcmd, ["venv"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved == "exit":
        matched_sub, _ = resolve_subcommand(subcmd, ["venv"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved == "schedule":
        matched_sub, _ = resolve_subcommand(subcmd, ["list", "add", "remove", "edit"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved == "sessions":
        matched_sub, _ = resolve_subcommand(subcmd, ["list", "kill"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved == "query":
        matched_sub, _ = resolve_subcommand(subcmd, ["user", "session"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved == "loginctl":
        matched_sub, _ = resolve_subcommand(subcmd, ["list-sessions", "terminate-session", "kill-session"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved in ("processes", "ps"):
        matched_sub, _ = resolve_subcommand(subcmd, ["cpu", "mem", "name", "pid"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved in ("archive", "compress"):
        matched_sub, _ = resolve_subcommand(subcmd, ["create", "extract"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved == "firewall":
        matched_sub, _ = resolve_subcommand(subcmd, ["status", "allow", "block", "delete"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved in ("disk", "drive"):
        matched_sub, _ = resolve_subcommand(subcmd, ["list", "usage", "mount", "unmount", "check", "health", "format"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved == "user":
        matched_sub, _ = resolve_subcommand(subcmd, ["add", "remove", "delete", "passwd", "password", "join", "list"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved == "group":
        matched_sub, _ = resolve_subcommand(subcmd, ["add", "remove", "delete", "list"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved in ("permission", "permissions"):
        matched_sub, _ = resolve_subcommand(subcmd, ["set", "owner", "group"])
        if matched_sub:
            subcmd = matched_sub
    elif resolved in ("k8s", "kubernetes"):
        matched_sub, _ = resolve_subcommand(subcmd, [
            "pods", "pod", "logs", "follow", "exec", "sh", "deployments", "deployment",
            "scale", "restart", "rollout", "services", "service", "nodes", "node", "drain",
            "cordon", "uncordon", "namespaces", "create", "delete", "top", "events",
            "cluster", "contexts", "use", "current", "forward", "apply"
        ])
        if matched_sub:
            subcmd = matched_sub
    elif resolved == "docker":
        matched_sub, _ = resolve_subcommand(subcmd, [
            "list", "containers", "ps", "images", "volumes", "networks", "stats", "usage",
            "start", "stop", "restart", "remove", "rm", "delete-image", "rmi",
            "logs", "follow", "tail", "exec", "shell", "run", "prune", "compose"
        ])
        if matched_sub:
            subcmd = matched_sub
    elif resolved in ("compose", "docker-compose"):
        matched_sub, _ = resolve_subcommand(subcmd, [
            "up", "down", "list", "ps", "logs", "restart", "build"
        ])
        if matched_sub:
            subcmd = matched_sub

    rest = words[2:]
    title = f"[bold green]Help: {' '.join(words)} ?[/bold green]"

    def print_cisco_help(items: list[tuple[str, str]], can_execute: bool = False):
        table = Table(show_header=False, box=None, padding=(0, 2))
        for name, desc in items:
            escaped_name = escape(name)
            if name.startswith("<") or name.startswith("["):
                arg_str = f"[yellow]{escaped_name}[/yellow]"
            elif name == "<cr>":
                arg_str = f"[cyan]{escaped_name}[/cyan]"
            else:
                arg_str = f"[green]{escaped_name}[/green]"
            table.add_row(arg_str, escape(desc))
        if can_execute:
            table.add_row("[cyan]<cr>[/cyan]", "Press Enter to execute the command")
        console.print(Panel(table, title=title, border_style="cyan", expand=False))

    # --- Builtin Commands ---
    if resolved in BUILTIN_DESCRIPTIONS:
        desc = BUILTIN_DESCRIPTIONS[resolved]
        if resolved in ("alias", "doskey"):
            print_cisco_help([
                (resolved, desc),
                (f"{resolved} [name='command']", "Create a new alias (e.g. ll='ls -l')")
            ], can_execute=True)
        elif resolved == "exit":
            pass
        else:
            print_cisco_help([(resolved, desc)], can_execute=True)
            return

    # --- USER ---
    if resolved == "user":
        if not subcmd:
            print_cisco_help([
                ("add", "Add a new system user account"),
                ("remove", "Delete an existing system user account"),
                ("delete", "Delete an existing system user account"),
                ("passwd", "Change a user's password"),
                ("password", "Change a user's password"),
                ("join", "Add a user to a group"),
                ("list", "List all local users"),
            ], can_execute=False)
            return
            
        if subcmd == "add":
            if not rest:
                print_cisco_help([("<username>", "Specify the name of the new user")], False)
            elif len(rest) == 1:
                print_cisco_help([("[password]", "Specify the password for the new user (optional)")], True)
            else:
                print_cisco_help([], True)
            return
            
        if subcmd in ("remove", "delete"):
            if not rest:
                print_cisco_help([("<username>", "Specify the user account to delete")], False)
            else:
                print_cisco_help([], True)
            return
            
        if subcmd in ("passwd", "password"):
            if not rest:
                print_cisco_help([("<username>", "Specify the user account to change password")], False)
            elif len(rest) == 1:
                print_cisco_help([("[password]", "Specify the new password (optional)")], True)
            else:
                print_cisco_help([], True)
            return
            
        if subcmd == "join":
            if not rest:
                print_cisco_help([("<group>", "Specify the group name")], False)
            elif len(rest) == 1:
                print_cisco_help([("<username>", "Specify the user to add to the group")], False)
            else:
                print_cisco_help([], True)
            return
            
        if subcmd == "list":
            print_cisco_help([], True)
            return

    # --- GROUP ---
    if resolved == "group":
        if not subcmd:
            print_cisco_help([
                ("add", "Add a new system group"),
                ("remove", "Delete an existing system group"),
                ("delete", "Delete an existing system group"),
                ("list", "List all local groups"),
            ], False)
            return
            
        if subcmd == "add":
            if not rest:
                print_cisco_help([("<groupname>", "Specify the name of the new group")], False)
            else:
                print_cisco_help([], True)
            return
            
        if subcmd in ("remove", "delete"):
            if not rest:
                print_cisco_help([("<groupname>", "Specify the group to delete")], False)
            else:
                print_cisco_help([], True)
            return
            
        if subcmd == "list":
            print_cisco_help([], True)
            return

    # --- PERMISSION / PERMISSIONS ---
    if resolved in ("permission", "permissions"):
        if not subcmd:
            print_cisco_help([
                ("set", "Set read/write/execute permissions (chmod)"),
                ("owner", "Change owner of file or folder (chown)"),
                ("group", "Change group of file or folder (chgrp)"),
            ], False)
            return
            
        if subcmd == "set":
            if not rest:
                print_cisco_help([("<permissions>", "Specify octal (e.g. 755, 644) or symbolic (e.g. +x, g+w) permissions")], False)
            elif len(rest) == 1:
                print_cisco_help([("<path>", "Specify the file or folder path")], False)
            else:
                print_cisco_help([], True)
            return
            
        if subcmd == "owner":
            if not rest:
                print_cisco_help([("<owner>", "Specify the username to assign as owner")], False)
            elif len(rest) == 1:
                print_cisco_help([("<path>", "Specify the file or folder path")], False)
            else:
                print_cisco_help([], True)
            return
            
        if subcmd == "group":
            if not rest:
                print_cisco_help([("<group>", "Specify the group to assign")], False)
            elif len(rest) == 1:
                print_cisco_help([("<path>", "Specify the file or folder path")], False)
            else:
                print_cisco_help([], True)
            return

    # --- Fallback traditional commands ---
    if resolved == "chmod":
        if not subcmd:
            print_cisco_help([("<permissions>", "Specify octal (e.g. 755, 644) or symbolic (e.g. +x) permissions")], False)
        elif len(words) == 2:
            print_cisco_help([("<path>", "Specify the file or folder path")], False)
        else:
            print_cisco_help([], True)
        return
        
    if resolved == "chown":
        if not subcmd:
            print_cisco_help([("<owner>", "Specify the owner username")], False)
        elif len(words) == 2:
            print_cisco_help([("<path>", "Specify the file or folder path")], False)
        else:
            print_cisco_help([], True)
        return
        
    if resolved == "chgrp":
        if not subcmd:
            print_cisco_help([("<group>", "Specify the group name")], False)
        elif len(words) == 2:
            print_cisco_help([("<path>", "Specify the file or folder path")], False)
        else:
            print_cisco_help([], True)
        return
        
    if resolved in ("useradd", "adduser"):
        if not subcmd:
            print_cisco_help([("<username>", "Specify the name of the new user")], False)
        elif len(words) == 2:
            print_cisco_help([("[password]", "Specify the password (optional)")], True)
        else:
            print_cisco_help([], True)
        return
        
    if resolved in ("userdel", "deluser"):
        if not subcmd:
            print_cisco_help([("<username>", "Specify the user account to delete")], False)
        else:
            print_cisco_help([], True)
        return
        
    if resolved == "passwd":
        if not subcmd:
            print_cisco_help([("[username]", "Specify the user account (defaults to current user)")], True)
        else:
            print_cisco_help([], True)
        return
        
    if resolved in ("groupadd", "addgroup"):
        if not subcmd:
            print_cisco_help([("<groupname>", "Specify the name of the new group")], False)
        else:
            print_cisco_help([], True)
        return
        
    if resolved in ("groupdel", "delgroup"):
        if not subcmd:
            print_cisco_help([("<groupname>", "Specify the group to delete")], False)
        else:
            print_cisco_help([], True)
        return

    if resolved == "usermod":
        if not subcmd:
            print_cisco_help([("-aG", "Specify flags (e.g. -aG to append to groups)")], False)
        elif len(words) == 2:
            print_cisco_help([("<group>", "Specify the group name")], False)
        elif len(words) == 3:
            print_cisco_help([("<username>", "Specify the user to modify")], False)
        else:
            print_cisco_help([], True)
        return

    # --- Schedule ---
    if resolved == "schedule":
        if not subcmd:
            print_cisco_help([
                ("list", "List all scheduled tasks"),
                ("add", "Add a new scheduled task interactively"),
                ("remove", "Remove an existing scheduled task"),
                ("edit", "Edit the raw scheduled tasks file"),
            ], True)
            console.print("[dim]Note: You can also pass native crontab or schtasks arguments directly (e.g. 'schedule -u root -l').[/dim]")
        elif subcmd == "add":
            if not rest:
                print_cisco_help([("<task_name>", "Specify the name of the new task")], False)
            elif len(rest) == 1:
                print_cisco_help([("<command>", "Specify the command to run")], False)
            elif len(rest) == 2:
                print_cisco_help([("<timing>", "Specify the frequency (e.g. Hourly, Daily, Reboot, or cron expr)")], False)
            else:
                print_cisco_help([], True)
        elif subcmd == "remove":
            if not rest:
                print_cisco_help([("<task_name>", "Specify the name of the task to remove")], False)
            else:
                print_cisco_help([], True)
        else:
            print_cisco_help([], True)
        return

    # --- Service control ---
    if resolved in ("status", "start", "stop", "restart", "logs", "live", "enable", "disable"):
        if not subcmd:
            print_cisco_help([("<service>", f"Specify the name of the service to {resolved}")], False)
        else:
            print_cisco_help([], True)
        return

    # --- Power commands ---
    if resolved in ("reboot", "shutdown"):
        if not subcmd:
            print_cisco_help([("[time]", "Specify time delay/target (e.g. '10', '23:30', or 'now') (optional)")], True)
        else:
            print_cisco_help([], True)
        return

    # --- Simple zero-arg commands ---
    if resolved in ("cancel", "sysinfo", "containers", "back", "ip", "update", "whoami", "pwd", "whereami", "history"):
        print_cisco_help([], True)
        return

    # --- Processes / ps ---
    if resolved in ("processes", "ps"):
        if not subcmd:
            print_cisco_help([
                ("cpu", "List processes sorted by CPU usage"),
                ("mem", "List processes sorted by memory usage"),
                ("name", "List processes sorted by name"),
                ("pid", "List processes sorted by process ID"),
            ], can_execute=True)
        else:
            print_cisco_help([], True)
        return

    # --- Path/directory commands ---
    if resolved in ("list", "cd", "open"):
        if not subcmd:
            print_cisco_help([("[path]", f"Specify the target path to {resolved if resolved != 'list' else 'list directory contents'} (optional)")], True)
        else:
            print_cisco_help([], True)
        return

    # --- File operations ---
    if resolved in ("copy", "move"):
        if not subcmd:
            print_cisco_help([("<source>", "Specify the file or folder to copy/move (use Tab/Arrow to select, →/Space to drill into folders)")], False)
        elif len(words) == 2:
            print_cisco_help([("<destination>", "Specify the target destination path (use Tab/Arrow to select, →/Space to drill into folders)")], False)
        else:
            print_cisco_help([], True)
        return

    if resolved in ("remove", "mkdir"):
        if not subcmd:
            if resolved == "remove":
                print_cisco_help([
                    ("shortcut <name>", "Remove a CLI shortcut"),
                    ("<path>", "Specify the file or folder to remove (use Tab/Arrow to select, →/Space to drill into folders)"),
                ], False)
            else:
                print_cisco_help([("<path>", "Specify the folder to create (use Tab/Arrow to select, →/Space to drill into folders)")], False)
        else:
            if resolved == "remove" and subcmd == "shortcut":
                if not rest:
                    print_cisco_help([("<name>", "Specify the shortcut alias name to remove")], False)
                else:
                    print_cisco_help([], True)
            else:
                print_cisco_help([], True)
        return

    # --- Create ---
    if resolved == "create":
        if not subcmd:
            print_cisco_help([
                ("folder", "Create a new directory (including parent directories)"),
                ("file", "Create a new empty file"),
                ("shortcut", "Create a CLI shortcut (alias) interactively"),
                ("venv", "Create a new Python virtual environment"),
            ], False)
        elif subcmd == "folder":
            if not rest:
                print_cisco_help([("<path>", "Specify the directory path to create (use Tab/Arrow to select, →/Space to drill into folders)")], False)
            else:
                print_cisco_help([], True)
        elif subcmd == "file":
            if not rest:
                print_cisco_help([("<path>", "Specify the file path to create (use Tab/Arrow to select, →/Space to drill into folders)")], False)
            else:
                print_cisco_help([], True)
        elif subcmd == "shortcut":
            if not rest:
                print_cisco_help([("<cr>", "Press ENTER to open the interactive shortcut wizard")], True)
            else:
                print_cisco_help([], True)
        elif subcmd == "venv":
            if not rest:
                print_cisco_help([("<path>", "Specify the path where the new virtual environment should be created (use Tab/Arrow to select, →/Space to drill into folders)")], False)
            else:
                print_cisco_help([], True)
        return

    # --- Show ---
    if resolved == "show":
        if not subcmd:
            print_cisco_help([
                ("file <path>", "Show contents of a file (cat)"),
                ("start <path>", "Show first N lines of a file (head)"),
                ("end <path>", "Show last N lines of a file (tail)"),
                ("tree [path]", "Show directory structure in a tree (tree)"),
                ("env [filter]", "View or filter environment variables (env)"),
                ("history", "Show the session command history (history)"),
                ("arp [args]", "View Address Resolution Protocol (ARP) table"),
                ("mac", "Display MAC addresses of active network interfaces"),
                ("gateway", "Display system default gateway IP address"),
                ("ip", "Show IP addresses and network interfaces"),
                ("route [args]", "View the system network routing table"),
                ("hostname [new_name]", "View or change the system's host name"),
                ("shortcut", "Show configured shortcuts and aliases"),
            ], False)
        elif subcmd == "file":
            if not rest:
                print_cisco_help([("<path>", "Specify the file path to show (use Tab/Arrow to select, →/Space to drill into folders)")], False)
            else:
                print_cisco_help([], True)
        elif subcmd in ("start", "end"):
            if not rest:
                print_cisco_help([
                    ("-n <lines>", "Specify the number of lines to display (default: 10)"),
                    ("<file_path>", f"Specify the file path to show {subcmd} of (use Tab/Arrow to select, →/Space to drill into folders)"),
                ], False)
            elif rest[0].lower() == "-n":
                if len(rest) == 1:
                    print_cisco_help([("<lines>", "Specify the number of lines")], False)
                elif len(rest) == 2:
                    print_cisco_help([("<file_path>", f"Specify the file path to show {subcmd} of (use Tab/Arrow to select, →/Space to drill into folders)")], False)
                else:
                    print_cisco_help([], True)
            else:
                print_cisco_help([], True)
        elif subcmd == "tree":
            if not rest:
                print_cisco_help([("[path]", "Specify optional directory path (use Tab/Arrow to select, →/Space to drill into folders)")], True)
            elif len(rest) == 1:
                print_cisco_help([("[depth]", "Specify optional search depth (integer)")], True)
            else:
                print_cisco_help([], True)
        elif subcmd == "env":
            if not rest:
                print_cisco_help([("[filter]", "Specify optional filter term")], True)
            else:
                print_cisco_help([], True)
        elif subcmd in ("history", "mac", "gateway", "ip", "shortcut"):
            print_cisco_help([], True)
        elif subcmd == "arp":
            if not rest:
                print_cisco_help([("[args]", "Optional arguments for the arp command")], True)
            else:
                print_cisco_help([], True)
        elif subcmd == "route":
            if not rest:
                print_cisco_help([("[args]", "Optional arguments for the route command")], True)
            else:
                print_cisco_help([], True)
        elif subcmd == "hostname":
            if not rest:
                print_cisco_help([("[new_name]", "Optional new hostname to set")], True)
            else:
                print_cisco_help([], True)
        return

    # --- Lookup ---
    if resolved == "lookup":
        if not subcmd:
            print_cisco_help([
                ("all", "Perform consolidated diagnostics (DNS, Ping, HTTP, Traceroute)"),
                ("A", "Query DNS A records (IPv4 addresses)"),
                ("AAAA", "Query DNS AAAA records (IPv6 addresses)"),
                ("CNAME", "Query DNS CNAME records (canonical names)"),
                ("MX", "Query DNS MX records (mail exchangers)"),
                ("TXT", "Query DNS TXT records (text records)"),
                ("NS", "Query DNS NS records (name servers)"),
                ("<host_or_ip>", "Specify the target hostname or IP address directly"),
            ], False)
        elif subcmd in ("all", "a", "aaaa", "cname", "mx", "txt", "ns"):
            if not rest:
                print_cisco_help([("<host_or_ip>", "Specify the target hostname or IP address")], False)
            else:
                print_cisco_help([], True)
        else:
            print_cisco_help([], True)
        return

    # --- Find ---
    if resolved == "find":
        if not subcmd:
            print_cisco_help([
                ("file", "Search files by name pattern"),
                ("text", "Search text pattern inside files"),
            ], False)
        elif subcmd == "file":
            if not rest:
                print_cisco_help([("<pattern>", "Specify name pattern to search")], False)
            elif len(rest) == 1:
                print_cisco_help([("[path]", "Specify optional search directory")], True)
            else:
                print_cisco_help([], True)
        elif subcmd == "text":
            if not rest:
                print_cisco_help([("<pattern>", "Specify text pattern to search for")], False)
            elif len(rest) == 1:
                print_cisco_help([("[path]", "Specify optional search directory")], True)
            else:
                print_cisco_help([], True)
        return

    # --- Sqlite ---
    if resolved == "sqlite":
        if not subcmd:
            print_cisco_help([("<database_path>", "Specify the path to the SQLite database file")], False)
        elif len(words) == 2:
            print_cisco_help([("[SQL query]", "Specify the SQL query to run against the database (optional)")], True)
        else:
            print_cisco_help([], True)
        return

    # --- Config ---
    if resolved == "config":
        if not subcmd:
            print_cisco_help([
                ("set", "Change a configuration setting"),
                ("reset", "Reset all configurations to defaults"),
            ], True)
        elif subcmd == "set":
            if not rest:
                print_cisco_help([("<setting>", "Specify the configuration setting name (e.g. theme)")], False)
            elif len(rest) == 1:
                print_cisco_help([("<value>", "Specify the new value for the setting")], False)
            else:
                print_cisco_help([], True)
        else:
            print_cisco_help([], True)
        return

    # --- Process / Kill / Pkill ---
    if resolved == "process":
        if not subcmd:
            print_cisco_help([("<pid_or_name>", "Specify the PID or process name to inspect")], False)
        else:
            print_cisco_help([], True)
        return

    if resolved == "kill":
        if not subcmd:
            print_cisco_help([("<PID_or_name>", "Specify the PID or process name to terminate")], False)
        else:
            print_cisco_help([], True)
        return

    if resolved == "pkill":
        if not subcmd:
            print_cisco_help([("<name_pattern>", "Specify process name pattern to terminate")], False)
        else:
            print_cisco_help([], True)
        return

    # --- Network Diagnostics ---
    if resolved == "ping":
        if not subcmd:
            print_cisco_help([("<host_or_ip>", "Specify the remote host or IP address to ping")], False)
        else:
            print_cisco_help([], True)
        return

    if resolved == "traceroute":
        if not subcmd:
            print_cisco_help([("<host_or_ip>", "Specify the remote host or IP address to trace")], False)
        else:
            print_cisco_help([], True)
        return

    if resolved in ("dns", "nslookup"):
        if not subcmd:
            print_cisco_help([("<host>", "Specify the domain name to query")], False)
        else:
            print_cisco_help([], True)
        return

    # --- Netstat ---
    if resolved == "netstat":
        if not subcmd:
            print_cisco_help([("[args...]", "Specify optional netstat options (e.g. -an, -p)")], True)
        else:
            print_cisco_help([], True)
        return

    # --- Which / Where / Findcommand ---
    if resolved in ("which", "where", "findcommand"):
        if not subcmd:
            print_cisco_help([("<command>", "Specify the name of the executable to locate")], False)
        else:
            print_cisco_help([], True)
        return

    # --- File/Directory Operations ---
    if resolved == "touch":
        if not subcmd:
            print_cisco_help([("<file_path>", "Specify the file path to create or update")], False)
        else:
            print_cisco_help([], True)
        return

    if resolved in ("cat", "less", "scrollfile"):
        if not subcmd:
            print_cisco_help([("<file_path>", "Specify the file path to view")], False)
        else:
            print_cisco_help([], True)
        return

    # --- Head / Tail / Viewstart / Viewend ---
    if resolved in ("head", "viewstart"):
        if not subcmd:
            print_cisco_help([
                ("-n <lines>", "Specify the number of lines to display (default: 10)"),
                ("<file_path>", "Specify the file path to view"),
            ], False)
        elif subcmd == "-n":
            if not rest:
                print_cisco_help([("<lines>", "Specify the number of lines")], False)
            elif len(rest) == 1:
                print_cisco_help([("<file_path>", "Specify the file path to view")], False)
            else:
                print_cisco_help([], True)
        else:
            print_cisco_help([], True)
        return

    if resolved in ("tail", "viewend"):
        if not subcmd:
            print_cisco_help([
                ("-n <lines>", "Specify the number of lines to display (default: 10)"),
                ("<file_path>", "Specify the file path to view"),
            ], False)
        elif subcmd == "-n":
            if not rest:
                print_cisco_help([("<lines>", "Specify the number of lines")], False)
            elif len(rest) == 1:
                print_cisco_help([("<file_path>", "Specify the file path to view")], False)
            else:
                print_cisco_help([], True)
        else:
            print_cisco_help([], True)
        return

    # --- SSH / SFTP / SCP ---
    if resolved == "ssh":
        if not subcmd:
            print_cisco_help([("<user@host>", "Specify the remote SSH connection string")], False)
        else:
            print_cisco_help([("[args...]", "Specify optional SSH arguments")], True)
        return

    if resolved == "sftp":
        if not subcmd:
            print_cisco_help([("<user@host>", "Specify the remote SFTP connection string")], False)
        else:
            print_cisco_help([("[args...]", "Specify optional SFTP arguments")], True)
        return

    if resolved == "scp":
        if not subcmd:
            print_cisco_help([("<source>", "Specify the file/folder to copy")], False)
        elif len(words) == 2:
            print_cisco_help([("<destination>", "Specify the remote destination (e.g. user@host:/path)")], False)
        else:
            print_cisco_help([], True)
        return

    # --- Git ---
    if resolved == "git":
        if not subcmd:
            print_cisco_help([("<subcommand>", "Specify the Git action (e.g. status, log, diff, branch, add, commit, push, pull)")], False)
        else:
            print_cisco_help([("[args...]", "Specify optional sub-arguments or options for the Git subcommand")], True)
        return

    if resolved == "gitsave":
        if not subcmd:
            print_cisco_help([("<message>", "Specify the commit message for the changes")], False)
        else:
            print_cisco_help([], True)
        return

    # --- Package management ---
    if resolved in ("install", "uninstall"):
        if not subcmd:
            print_cisco_help([("<package>", f"Specify the package name to {resolved}")], False)
        else:
            print_cisco_help([], True)
        return

    # --- Port ---
    if resolved == "port":
        if not subcmd:
            print_cisco_help([("<port_number | program_name>", "Specify a port number or program name to lookup")], False)
        else:
            print_cisco_help([], True)
        return

    # --- Netconfig ---
    if resolved == "netconfig":
        if not subcmd:
            print_cisco_help([
                ("reset", "Reset manual network interface settings"),
                ("dhcp", "DHCP release/renew commands"),
                ("<adapter>", "Specify interface adapter to configure"),
            ], False)
        elif subcmd == "reset":
            if not rest:
                print_cisco_help([("<adapter>", "Specify the adapter to reset to default DHCP")], False)
            else:
                print_cisco_help([], True)
        elif subcmd == "dhcp":
            if not rest:
                print_cisco_help([
                    ("release", "Release DHCP lease for the adapter"),
                    ("renew", "Renew DHCP lease for the adapter"),
                ], False)
            elif len(rest) == 1 and rest[0].lower() in ("release", "renew"):
                print_cisco_help([("<adapter>", "Specify the adapter for DHCP operation")], False)
            else:
                print_cisco_help([], True)
        else:
            print_cisco_help([], True)
        return

    # --- Firewall ---
    if resolved == "firewall":
        if not subcmd:
            print_cisco_help([
                ("status", "Check firewall rules with numbered IDs"),
                ("allow", "Allow incoming traffic on port"),
                ("block", "Block incoming traffic on port"),
                ("delete", "Delete a specific firewall rule by ID or port"),
            ], False)
        elif subcmd in ("allow", "block"):
            if len(rest) == 0:
                print_cisco_help([("<port>", "Specify the port number or range (e.g. 80, 22, 8080-8090)")], False)
            elif len(rest) == 1:
                print_cisco_help([
                    ("tcp", "Transmission Control Protocol (Default)"),
                    ("udp", "User Datagram Protocol"),
                    ("both", "Both TCP and UDP protocols"),
                ], True)
            elif len(rest) == 2:
                print_cisco_help([("[adapter]", "(Optional) Specify target network interface (e.g. eth0)")], True)
            elif len(rest) == 3:
                print_cisco_help([("[rule_name]", "(Optional) Specify custom label for this rule")], True)
            else:
                print_cisco_help([], True)
        elif subcmd in ("delete", "remove"):
            if len(rest) == 0:
                print_cisco_help([("<ID_or_port>", "Specify the Rule ID number or port to delete")], False)
            else:
                print_cisco_help([], True)
        else:
            print_cisco_help([], True)
        return

    # --- Disk / Drive ---
    if resolved in ("disk", "drive"):
        if not subcmd:
            print_cisco_help([
                ("list", "List physical disks, drives, and volume partitions"),
                ("usage", "Show disk space usage for path or system"),
                ("mount", "Mount a drive or partition"),
                ("unmount", "Unmount a mounted drive volume"),
                ("check", "Perform filesystem integrity check (fsck/chkdsk)"),
                ("health", "Check disk health / SMART status"),
                ("format", "Format a device with a filesystem (Destructive)"),
            ], False)
        elif subcmd == "usage":
            if not rest:
                print_cisco_help([("[path]", "Show usage for a specific path (optional)")], True)
            else:
                print_cisco_help([], True)
        elif subcmd == "mount":
            if not rest:
                print_cisco_help([("<dev>", "Specify device partition to mount")], False)
            elif len(rest) == 1:
                print_cisco_help([("<target>", "Specify mount point directory path")], False)
            else:
                print_cisco_help([], True)
        elif subcmd == "unmount":
            if not rest:
                print_cisco_help([("<target>", "Specify target partition or mount point to unmount")], False)
            else:
                print_cisco_help([], True)
        elif subcmd == "check":
            if not rest:
                print_cisco_help([("<target>", "Specify partition or mount point to check")], False)
            else:
                print_cisco_help([], True)
        elif subcmd == "health":
            if not rest:
                print_cisco_help([("[target]", "Specify target disk (e.g. /dev/sda) (optional)")], True)
            else:
                print_cisco_help([], True)
        elif subcmd == "format":
            if not rest:
                print_cisco_help([("<dev>", "Specify device to format")], False)
            elif len(rest) == 1:
                print_cisco_help([("<fs>", "Specify filesystem type (e.g. ext4, ntfs, fat32)")], False)
            else:
                print_cisco_help([], True)
        else:
            print_cisco_help([], True)
        return

    # --- Archive / Compress ---
    if resolved in ("archive", "compress"):
        if not subcmd:
            print_cisco_help([
                ("create", "Compress file/folder into archive"),
                ("extract", "Extract compressed archive into folder"),
            ], False)
        elif subcmd == "create":
            if not rest:
                print_cisco_help([("<out.zip>", "Specify output zip file path")], False)
            elif len(rest) == 1:
                print_cisco_help([("<path>", "Specify file or folder path to compress")], False)
            else:
                print_cisco_help([], True)
        elif subcmd == "extract":
            if not rest:
                print_cisco_help([("<archive>", "Specify archive zip file to extract")], False)
            elif len(rest) == 1:
                print_cisco_help([("[dest]", "Specify target destination directory (optional)")], True)
            else:
                print_cisco_help([], True)
        else:
            print_cisco_help([], True)
        return

    # --- Download ---
    if resolved == "download":
        if not subcmd:
            print_cisco_help([("<url>", "Specify the download URL (HTTP/HTTPS/FTP)")], False)
        elif len(words) == 2:
            print_cisco_help([("[destination]", "Specify optional destination file path or folder")], True)
        else:
            print_cisco_help([], True)
        return

    # --- Search ---
    if resolved == "search":
        if not subcmd:
            print_cisco_help([("<query_text>", "Specify text or regex pattern to search for in files")], False)
        elif len(words) == 2:
            print_cisco_help([("[path]", "Specify optional directory path to search inside")], True)
        else:
            print_cisco_help([], True)
        return

    # --- Killport ---
    if resolved == "killport":
        if not subcmd:
            print_cisco_help([("<port_number>", "Specify port number to terminate associated process")], False)
        else:
            print_cisco_help([], True)
        return

    # --- Enter ---
    if resolved == "enter":
        if not subcmd:
            print_cisco_help([("venv", "Enter (activate) a Python virtual environment")], False)
        elif subcmd == "venv":
            if not rest:
                print_cisco_help([("<path>", "Specify the path of the virtual environment to enter/activate")], False)
            else:
                print_cisco_help([], True)
        return

    # --- Crontab ---
    if resolved == "crontab":
        if not subcmd:
            print_cisco_help([
                ("-l", "List your scheduled cron jobs"),
                ("-e", "Edit your cron jobs interactively"),
                ("-r", "Remove all of your cron jobs"),
            ], False)
        else:
            print_cisco_help([], True)
        return

    # --- Schtasks ---
    if resolved == "schtasks":
        if not subcmd:
            print_cisco_help([
                ("/query", "List all scheduled tasks"),
                ("/create", "Create a new scheduled task"),
                ("/delete", "Delete an existing scheduled task"),
            ], False)
        else:
            print_cisco_help([], True)
        return

    # --- Exit ---
    if resolved == "exit":
        if not subcmd:
            print_cisco_help([("venv", "Exit (deactivate) the current Python virtual environment")], True)
        elif subcmd == "venv":
            print_cisco_help([], True)
        return

    # --- Sessions ---
    if resolved == "sessions":
        if not subcmd:
            print_cisco_help([
                ("list", "List all active logon and multiplexer sessions"),
                ("kill", "Terminate/disconnect an active session"),
            ], True)
        elif subcmd == "list":
            print_cisco_help([], True)
        elif subcmd == "kill":
            if not rest:
                print_cisco_help([("<session_id/tty>", "Specify the active session ID or TTY to terminate")], False)
            else:
                print_cisco_help([], True)
        return

    # --- W, Who, Quser, Qwinsta ---
    if resolved in ("w", "who", "quser", "qwinsta"):
        if not subcmd:
            print_cisco_help([("[username]", "Filter active sessions by username (optional)")], True)
        else:
            print_cisco_help([], True)
        return

    # --- Query ---
    if resolved == "query":
        if not subcmd:
            print_cisco_help([
                ("user", "Query logged on users"),
                ("session", "Query logon sessions"),
            ], False)
        elif subcmd == "user":
            if not rest:
                print_cisco_help([("[username]", "Query a specific logged on user (optional)")], True)
            else:
                print_cisco_help([], True)
        elif subcmd == "session":
            if not rest:
                print_cisco_help([("[session_name/id]", "Query a specific logon session (optional)")], True)
            else:
                print_cisco_help([], True)
        return

    # --- Logoff ---
    if resolved == "logoff":
        if not subcmd:
            print_cisco_help([("<session_id/tty/name>", "Specify the active session ID, TTY, or username to log off")], False)
        else:
            print_cisco_help([], True)
        return

    # --- Loginctl ---
    if resolved == "loginctl":
        if not subcmd:
            print_cisco_help([
                ("list-sessions", "List all active sessions"),
                ("terminate-session", "Terminate a session"),
                ("kill-session", "Send a signal to processes of a session"),
            ], True)
        elif subcmd == "list-sessions":
            print_cisco_help([], True)
        elif subcmd in ("terminate-session", "kill-session"):
            if not rest:
                print_cisco_help([("<session_id>", "Specify the active session ID to terminate/kill")], False)
            else:
                print_cisco_help([], True)
        return

    # --- Kubernetes / k8s ---
    if resolved in ("k8s", "kubernetes"):
        if not subcmd:
            table = Table(show_header=False, box=None, padding=(0, 2))
            # Pods
            table.add_row("[bold magenta]# Pods[/bold magenta]", "")
            table.add_row("[green]pods[/green]",             "List pods in the current namespace")
            table.add_row("[green]pods all[/green]",         "List pods across ALL namespaces")
            table.add_row("[green]pod info <name>[/green]",  "Detailed pod description")
            table.add_row("[green]logs <pod>[/green]",       "Show pod logs (last 100 lines)")
            table.add_row("[green]follow <pod>[/green]",     "Follow (tail -f) live pod logs")
            table.add_row("[green]exec <pod>[/green]",       "Open /bin/bash shell inside pod")
            table.add_row("[green]sh <pod>[/green]",         "Open /bin/sh shell inside pod")
            # Deployments
            table.add_row("[bold magenta]# Deployments[/bold magenta]", "")
            table.add_row("[green]deployments[/green]",                       "List all deployments")
            table.add_row("[green]deployment info <name>[/green]",            "Describe a deployment")
            table.add_row("[green]scale <deploy> <N>[/green]",                "Scale deployment to N replicas")
            table.add_row("[green]restart <deploy>[/green]",                  "Rolling restart of a deployment")
            table.add_row("[green]rollout status <deploy>[/green]",            "Check rollout progress")
            table.add_row("[green]rollout history <deploy>[/green]",           "View rollout revision history")
            table.add_row("[green]rollout undo <deploy>[/green]",              "Roll back to previous revision")
            # Services
            table.add_row("[bold magenta]# Services[/bold magenta]", "")
            table.add_row("[green]services[/green]",             "List all services")
            table.add_row("[green]service info <name>[/green]",   "Describe a service")
            # Nodes
            table.add_row("[bold magenta]# Nodes[/bold magenta]", "")
            table.add_row("[green]nodes[/green]",               "List cluster nodes")
            table.add_row("[green]node info <name>[/green]",     "Describe a specific node")
            table.add_row("[green]drain <node>[/green]",         "Safely drain a node for maintenance")
            table.add_row("[green]cordon <node>[/green]",        "Mark node as unschedulable")
            table.add_row("[green]uncordon <node>[/green]",      "Mark node as schedulable")
            # Namespaces
            table.add_row("[bold magenta]# Namespaces[/bold magenta]", "")
            table.add_row("[green]namespaces[/green]",                    "List all namespaces")
            table.add_row("[green]create namespace <name>[/green]",       "Create a new namespace")
            table.add_row("[green]delete namespace <name>[/green]",       "Delete a namespace")
            # Apply / Delete
            table.add_row("[bold magenta]# Manifests[/bold magenta]", "")
            table.add_row("[green]apply <file.yaml>[/green]",   "Apply a Kubernetes manifest")
            table.add_row("[green]delete <file.yaml>[/green]",  "Delete resources from a manifest")
            table.add_row("[green]delete pod <name>[/green]",   "Force-delete a specific pod")
            # Monitoring
            table.add_row("[bold magenta]# Monitoring[/bold magenta]", "")
            table.add_row("[green]top pods[/green]",     "Show pod CPU/Memory usage")
            table.add_row("[green]top nodes[/green]",    "Show node CPU/Memory usage")
            table.add_row("[green]events[/green]",       "Show recent cluster events")
            table.add_row("[green]cluster info[/green]", "Display cluster API endpoint")
            # Context
            table.add_row("[bold magenta]# Context / Config[/bold magenta]", "")
            table.add_row("[green]contexts[/green]",                  "List all kubectl contexts")
            table.add_row("[green]use context <name>[/green]",         "Switch active cluster context")
            table.add_row("[green]current context[/green]",            "Show current active context")
            # Port Forward
            table.add_row("[bold magenta]# Port Forwarding[/bold magenta]", "")
            table.add_row("[green]forward <pod> <local>:<remote>[/green]", "Forward local port to pod port")
            console.print(Panel(
                table,
                title=f"[bold green]⎈ k8s — Simple-English Kubernetes Commands[/bold green]",
                subtitle="[dim]Also accepts: kubernetes <verb>  |  Use kubectl for raw commands[/dim]",
                border_style="cyan", expand=False
            ))
            return
            
        pod_verbs = ("logs", "follow", "tail", "exec", "sh")
        deploy_verbs = ("restart", "scale")
        node_verbs = ("drain", "cordon", "uncordon")
        if subcmd in pod_verbs:
            print_cisco_help([("<pod>", f"Specify the pod name to {subcmd}")], False)
        elif subcmd in deploy_verbs:
            print_cisco_help([("<deployment>", f"Specify the deployment name to {subcmd}")], False)
        elif subcmd in node_verbs:
            print_cisco_help([("<node>", f"Specify the node name to {subcmd}")], False)
        elif subcmd in ("apply", "delete"):
            print_cisco_help([("<file.yaml>", "Specify the manifest file path or URL")], False)
        elif subcmd in ("scale",):
            print_cisco_help([("<deployment> <N>", "Specify deployment name and desired replica count")], False)
        elif subcmd in ("forward", "portforward"):
            print_cisco_help([("<pod> <local>:<remote>", "Specify pod name and port mapping (e.g. 8080:8080)")], False)
        elif subcmd == "get":
            print_cisco_help([("<resource>", "Specify resource type (pods, deployments, services, nodes, ...)")], False)
        else:
            print_cisco_help([], True)
        return

    # --- Kubectl ---
    if resolved == "kubectl":
        if not subcmd:
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_row("[bold magenta]# Resource Queries[/bold magenta]", "")
            table.add_row("[green]get <resource>[/green]",                      "List resources (pods, deployments, services, nodes ...)")
            table.add_row("[green]get <resource> -n <namespace>[/green]",        "List resources in a specific namespace")
            table.add_row("[green]get <resource> --all-namespaces[/green]",      "List resources across all namespaces")
            table.add_row("[green]describe <resource> <name>[/green]",           "Detailed resource description")
            table.add_row("[bold magenta]# Pod Management[/bold magenta]", "")
            table.add_row("[green]logs <pod>[/green]",                           "Show pod logs")
            table.add_row("[green]logs -f <pod>[/green]",                        "Follow (tail) pod logs live")
            table.add_row("[green]logs <pod> -c <container>[/green]",            "Logs from a specific container in a pod")
            table.add_row("[green]exec -it <pod> -- bash[/green]",               "Open interactive shell inside a pod")
            table.add_row("[green]delete pod <name>[/green]",                    "Force delete a pod")
            table.add_row("[bold magenta]# Deployments[/bold magenta]", "")
            table.add_row("[green]scale deployment <name> --replicas=N[/green]",  "Scale a deployment")
            table.add_row("[green]rollout status deployment/<name>[/green]",       "Check rollout progress")
            table.add_row("[green]rollout undo deployment/<name>[/green]",         "Rollback to previous revision")
            table.add_row("[green]rollout restart deployment/<name>[/green]",      "Rolling restart of all pods")
            table.add_row("[bold magenta]# Apply / Delete[/bold magenta]", "")
            table.add_row("[green]apply -f <file.yaml>[/green]",                  "Apply a manifest (create or update)")
            table.add_row("[green]delete -f <file.yaml>[/green]",                 "Delete resources from manifest")
            table.add_row("[green]create namespace <name>[/green]",               "Create a new namespace")
            table.add_row("[bold magenta]# Port & Monitoring[/bold magenta]", "")
            table.add_row("[green]port-forward pod/<pod> 8080:8080[/green]",       "Forward local port to pod")
            table.add_row("[green]top pods[/green]",                               "Show pod CPU/Memory (needs metrics-server)")
            table.add_row("[green]top nodes[/green]",                              "Show node CPU/Memory")
            table.add_row("[green]get events --sort-by=.lastTimestamp[/green]",    "Show cluster events by time")
            table.add_row("[bold magenta]# Context & Config[/bold magenta]", "")
            table.add_row("[green]config get-contexts[/green]",                    "List all kubectl contexts")
            table.add_row("[green]config use-context <name>[/green]",              "Switch to a context")
            table.add_row("[green]config current-context[/green]",                 "Show current context")
            table.add_row("[bold magenta]# Nodes[/bold magenta]", "")
            table.add_row("[green]drain <node> --ignore-daemonsets[/green]",       "Drain node for maintenance")
            table.add_row("[green]cordon <node>[/green]",                          "Mark node as unschedulable")
            table.add_row("[green]uncordon <node>[/green]",                        "Mark node as schedulable")
            table.add_row("[bold magenta]# Discovery[/bold magenta]", "")
            table.add_row("[green]api-resources[/green]",                          "List all supported API resource types")
            table.add_row("[green]explain <resource>[/green]",                     "Get field documentation for a resource")
            console.print(Panel(
                table,
                title="[bold green]⎈ kubectl — Raw Kubernetes CLI Commands[/bold green]",
                subtitle="[dim]Tip: Use 'k8s' for simple-English alternatives[/dim]",
                border_style="cyan", expand=False
            ))
            return
        # Hint for next arg
        if subcmd in ("get", "describe", "delete", "edit"):
            print_cisco_help([("<resource>", "Specify resource type: pods, deployments, services, nodes, namespaces ...")], False)
        elif subcmd == "logs":
            print_cisco_help([("<pod_name>", "Specify the pod name (add '-f' for live follow, '-c <container>' for specific container)")], True)
        elif subcmd == "exec":
            print_cisco_help([("-it <pod> -- <cmd>", "e.g. kubectl exec -it mypod -- bash")], False)
        elif subcmd in ("apply", "create", "replace"):
            print_cisco_help([("-f <file.yaml>", "Specify the manifest file or directory path")], False)
        elif subcmd == "scale":
            print_cisco_help([("deployment/<name> --replicas=N", "Specify deployment name and desired replica count")], False)
        elif subcmd == "rollout":
            print_cisco_help([("status|history|undo|restart deployment/<name>", "Specify rollout action and deployment name")], False)
        elif subcmd == "port-forward":
            print_cisco_help([("pod/<pod_name> <local>:<remote>", "Specify pod and port mapping (e.g. 8080:8080)")], False)
        elif subcmd == "config":
            print_cisco_help([("get-contexts | use-context <name> | current-context | view", "Specify config action")], False)
        elif subcmd == "top":
            print_cisco_help([("pods | nodes", "Specify the resource type to show metrics for")], False)
        elif subcmd in ("drain", "cordon", "uncordon"):
            print_cisco_help([("<node_name>", "Specify the node to perform the operation on")], False)
        else:
            print_cisco_help([("[args...]", "Specify sub-arguments or options for the kubectl subcommand")], True)
    # --- Docker & Docker Compose ---
    if resolved == "docker":
        if not subcmd:
            from hopit.docker import print_docker_help
            print_docker_help(title=title)
            return
        if subcmd in ("start", "stop", "restart", "remove", "rm", "logs", "follow", "tail", "exec", "shell"):
            print_cisco_help([("<container>", f"Specify container name to {subcmd}")], False)
        elif subcmd in ("delete-image", "rmi"):
            print_cisco_help([("<image>", "Specify image name/ID to delete")], False)
        elif subcmd == "run":
            print_cisco_help([("<image>", "Specify image name to run")], False)
        else:
            print_cisco_help([], True)
        return

    if resolved in ("compose", "docker-compose"):
        if not subcmd:
            from hopit.docker import print_compose_help
            print_compose_help(title=title)
            return
        if subcmd in ("logs", "restart", "build", "stop", "start", "rm", "up"):
            print_cisco_help([("<service>", f"Specify compose service name to {subcmd} (optional)")], True)
        else:
            print_cisco_help([], True)
        return

    # --- General Fallback ---
    if resolved in commands:
        cmd = commands[resolved]
        desc = cmd.desc
        sudo_req = cmd.needs_sudo
        
        if cmd.needs_arg:
            kind = cmd.arg_completion_kind or "args"
            print_cisco_help([
                (f"<{kind}>", desc)
            ], can_execute=False)
        else:
            print_cisco_help([
                (resolved, desc)
            ], can_execute=True)
            
        if sudo_req:
            console.print("[bold red]Note:[/bold red]        Requires root/sudo privileges.")
        return


def execute_line(
    line: str,
    shell: str,
    aliases: dict,
    all_names: list[str],
    commands: dict,
    manager: str | None,
    session=None,
) -> bool:
    """Executes a single command line. Returns True to continue prompt loop, False to exit."""
    line_strip = line.strip()
    if not line_strip:
        return True

    # Check for "?" helper command
    if line_strip.endswith("?"):
        query = line_strip[:-1].strip()
        if not query:
            # Just "?" was typed: list all commands
            console.print("\n[bold cyan]Available Commands:[/bold cyan]")
            aliases_to_hide = {
                "permissions", "drive", "compress", "ps", "where", "findcommand",
                "adduser", "deluser", "addgroup", "delgroup", "viewstart", "viewend",
                "scrollfile", "findfile", "findtext"
            }
            for name in sorted(all_names):
                if name in aliases_to_hide:
                    continue
                desc = commands[name].desc if name in commands else BUILTIN_DESCRIPTIONS.get(name, "")
                console.print(f"  [green]{name:15}[/green] : {desc}")
            console.print()
            return True

        words = query.split()
        if len(words) == 1:
            candidate = words[0].lower()
            resolved, matches = resolve_command(all_names, candidate)
            if resolved:
                show_context_help([resolved], commands)
            elif matches:
                console.print(f"\n[bold cyan]Commands starting with '{candidate}':[/bold cyan]")
                for m in matches:
                    desc = commands[m].desc if m in commands else BUILTIN_DESCRIPTIONS.get(m, "")
                    console.print(f"  [green]{m:15}[/green] : {desc}")
                console.print()
            else:
                console.print(f"[red]No commands match the prefix '{candidate}'.[/red]")
            return True
        else:
            show_context_help(words, commands)
            return True

    show_cmd = False
    try:
        tokens = shlex.split(line, posix=not IS_WINDOWS)
    except ValueError as e:
        console.print(f"[red]Parse error: {e}[/red]")
        return True

    if not tokens:
        return True

    if "--show" in tokens:
        show_cmd = True
        tokens = [t for t in tokens if t != "--show"]
        line = " ".join(shlex.quote(t) for t in tokens)

    head, *rest = tokens

    name, ambiguous = resolve_command(all_names, head)
    if head.lower() in ("exit", "quit"):
        name = head.lower()

    if name is not None:
        intended_words = [name]
        if name == "show" and rest:
            sub_token = rest[0].lower()
            valid_subs = ["file", "start", "end", "tree", "env", "history", "arp", "mac", "gateway", "ip", "route", "hostname"]
            subcmd, _ = resolve_subcommand(sub_token, valid_subs)
            if subcmd:
                intended_words.append(subcmd)
                intended_words.extend(rest[1:])
            else:
                intended_words.extend(rest)
        elif name == "lookup" and rest:
            sub_token = rest[0].lower()
            valid_subs = ["all", "a", "aaaa", "cname", "mx", "txt", "ns"]
            subcmd, _ = resolve_subcommand(sub_token, valid_subs)
            if subcmd:
                intended_words.append(subcmd)
                intended_words.extend(rest[1:])
            else:
                intended_words.extend(rest)
        elif name == "find" and rest and rest[0].lower() not in ("-name", "-type", "-path", "-print"):
            valid_subs = ["file", "text"]
            subcmd, _ = resolve_subcommand(rest[0], valid_subs)
            if subcmd:
                intended_words.append(subcmd)
                intended_words.extend(rest[1:])
            else:
                intended_words.extend(rest)
        elif name == "create" and rest:
            sub_token = rest[0].lower()
            valid_subs = ["folder", "file", "shortcut", "venv"]
            subcmd, _ = resolve_subcommand(sub_token, valid_subs)
            if subcmd:
                intended_words.append(subcmd)
                intended_words.extend(rest[1:])
            else:
                intended_words.extend(rest)
        elif name == "enter" and rest:
            sub_token = rest[0].lower()
            valid_subs = ["venv"]
            subcmd, _ = resolve_subcommand(sub_token, valid_subs)
            if subcmd:
                intended_words.append(subcmd)
                intended_words.extend(rest[1:])
            else:
                intended_words.extend(rest)
        elif name == "exit" and rest:
            sub_token = rest[0].lower()
            valid_subs = ["venv"]
            subcmd, _ = resolve_subcommand(sub_token, valid_subs)
            if subcmd:
                intended_words.append(subcmd)
                intended_words.extend(rest[1:])
            else:
                intended_words.extend(rest)
        else:
            intended_words.extend(rest)

        if show_cmd:
            intended_words.append("--show")

        intended_line = shlex.join(intended_words)

        if session and hasattr(session, "history"):
            hist = session.history
            if hasattr(hist, "_loaded_strings") and hist._loaded_strings:
                if hist._loaded_strings[0] == line:
                    hist._loaded_strings[0] = intended_line
            if hasattr(hist, "_storage") and hist._storage:
                if hist._storage[-1] == line:
                    hist._storage[-1] = intended_line

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
                    if show_cmd:
                        console.print(f"[dim]Running command: {translated}[/dim]")
                    run_shell_line(translated, shell)
                except Exception as e:
                    console.print(f"[red]Command failed: {e}[/red]")
            else:
                # Fallback: expand aliases then run as a raw shell command
                expanded = expand_aliases(line, aliases)
                try:
                    if show_cmd:
                        console.print(f"[dim]Running command: {expanded}[/dim]")
                    run_shell_line(expanded, shell)
                except Exception as e:
                    console.print(f"[red]Command failed: {e}[/red]")
        return True

    if name in ("alias", "doskey"):
        arg_str = " ".join(rest)
        if "=" in arg_str:
            alias_name, alias_val = arg_str.split("=", 1)
            alias_name = alias_name.strip()
            alias_val = alias_val.strip()
            if alias_val and alias_val[0] in ("'", '"') and alias_val[-1] == alias_val[0]:
                alias_val = alias_val[1:-1]
            if alias_val.endswith(" $*"):
                alias_val = alias_val[:-3]
            aliases[alias_name] = alias_val
            user_session_shortcuts.add(alias_name)
            console.print(f"[green]Shortcut added (Temporary)![/green] {alias_name} → {alias_val}")
            return True
        elif not arg_str or arg_str.lower() == "/macros":
            if aliases:
                console.print("\n[bold cyan]Active Shortcuts:[/bold cyan]")
                for k, v in sorted(aliases.items()):
                    console.print(f"  [green]{k}[/green]=" + (f"'{v}'" if " " in v else v))
            else:
                console.print("[yellow]No shortcuts are currently active.[/yellow]")
            return True
        else:
            translated = translate_cross_platform(tokens)
            if translated is not None:
                try:
                    run_shell_line(translated, shell)
                except Exception as e:
                    console.print(f"[red]Command failed: {e}[/red]")
            else:
                try:
                    run_shell_line(" ".join(tokens), shell)
                except Exception as e:
                    console.print(f"[red]Command failed: {e}[/red]")
            return True

    if name == "help":
        print_help(commands, manager)
        return True
    if name == "clear":
        console.clear()
        return True
    if name in ("exit", "quit"):
        if name == "exit" and rest:
            # Cisco-IOS style: resolve prefix — "v", "ve", "ven", "venv" all match
            matched_sub, _ = resolve_subcommand(rest[0].lower(), ["venv"])
            if matched_sub == "venv":
                if "VIRTUAL_ENV" not in os.environ:
                    console.print("[yellow]No virtual environment is currently active.[/yellow]")
                    return True
                env_path = os.environ.pop("VIRTUAL_ENV")
                bin_dir = os.path.join(env_path, "Scripts" if IS_WINDOWS else "bin")
                path_parts = os.environ.get("PATH", "").split(os.pathsep)
                if bin_dir in path_parts:
                    path_parts.remove(bin_dir)
                    os.environ["PATH"] = os.pathsep.join(path_parts)
                console.print(f"[bold yellow]Deactivated virtual environment:[/bold yellow] {env_path}")
                return True
            # Unknown argument — treat as exit hopit-cli
        return False


    if name in ("pwd", "whereami"):
        console.print(f"[green]📁 {os.getcwd()}[/green]")
        return True

    if name == "history":
        if session and hasattr(session, "history"):
            history_entries = list(session.history.get_strings())
            for i, cmd in enumerate(history_entries, 1):
                console.print(f"  [cyan]{i:5}[/cyan]  {cmd}")
        else:
            console.print("[yellow]No command history available in this session.[/yellow]")
        return True

    if name == "show":
        if not rest:
            console.print("[yellow]Usage: show [file|start|end|tree|env|history|arp|mac|gateway|ip|route|hostname|shortcut] [arguments][/yellow]")
            return True
        sub_token = rest[0].lower()
        subargs = rest[1:]
        
        valid_subs = ["file", "start", "end", "tree", "env", "history", "arp", "mac", "gateway", "ip", "route", "hostname", "shortcut"]
        subcmd, matches = resolve_subcommand(sub_token, valid_subs)
        
        if not subcmd:
            if len(matches) > 1:
                console.print(f"[red]Ambiguous show subcommand '{sub_token}'. Candidates: {', '.join(matches)}[/red]")
            else:
                console.print(f"[red]Unknown show subcommand '{sub_token}'. Supported: {', '.join(valid_subs)}[/red]")
            return True
        
        if subcmd == "file":
            if not subargs:
                console.print("[yellow]Usage: show file <file_path>[/yellow]")
                return True
            real_cmd = [sys.executable, "-m", "hopit.cat"] + subargs
            try:
                proc = subprocess.run(real_cmd, capture_output=True, text=True)
                render_result(proc, label=" ".join(real_cmd), cmd_name="show file", cmd_arg=" ".join(subargs), show_cmd=show_cmd)
            except Exception as e:
                console.print(f"[red]Error running show file: {e}[/red]")
        elif subcmd == "start":
            if not subargs:
                console.print("[yellow]Usage: show start <file_path>[/yellow]")
                return True
            real_cmd = [sys.executable, "-m", "hopit.head"] + subargs
            try:
                proc = subprocess.run(real_cmd, capture_output=True, text=True)
                render_result(proc, label=" ".join(real_cmd), cmd_name="show start", cmd_arg=" ".join(subargs), show_cmd=show_cmd)
            except Exception as e:
                console.print(f"[red]Error running show start: {e}[/red]")
        elif subcmd == "end":
            if not subargs:
                console.print("[yellow]Usage: show end <file_path>[/yellow]")
                return True
            real_cmd = [sys.executable, "-m", "hopit.tail"] + subargs
            try:
                proc = subprocess.run(real_cmd, capture_output=True, text=True)
                render_result(proc, label=" ".join(real_cmd), cmd_name="show end", cmd_arg=" ".join(subargs), show_cmd=show_cmd)
            except Exception as e:
                console.print(f"[red]Error running show end: {e}[/red]")
        elif subcmd == "tree":
            real_cmd = [sys.executable, "-m", "hopit.tree"] + subargs
            try:
                proc = subprocess.run(real_cmd, capture_output=True, text=True)
                render_result(proc, label=" ".join(real_cmd), cmd_name="show tree", cmd_arg=" ".join(subargs), show_cmd=show_cmd)
            except Exception as e:
                console.print(f"[red]Error running show tree: {e}[/red]")
        elif subcmd == "env":
            real_cmd = [sys.executable, "-m", "hopit.env"] + subargs
            try:
                proc = subprocess.run(real_cmd, capture_output=True, text=True)
                render_result(proc, label=" ".join(real_cmd), cmd_name="show env", cmd_arg=" ".join(subargs), show_cmd=show_cmd)
            except Exception as e:
                console.print(f"[red]Error running show env: {e}[/red]")
        elif subcmd == "history":
            if session and hasattr(session, "history"):
                history_entries = list(session.history.get_strings())
                for i, cmd_entry in enumerate(history_entries, 1):
                    console.print(f"  [cyan]{i:5}[/cyan]  {cmd_entry}")
            else:
                console.print("[yellow]No command history available in this session.[/yellow]")
        elif subcmd == "shortcut":
            from rich.table import Table
            table = Table(title="CLI Shortcuts (Aliases)", show_header=True, header_style="bold cyan")
            table.add_column("Shortcut Name", style="cyan")
            table.add_column("Command", style="yellow")
            table.add_column("Origin", style="blue")
            table.add_column("Persistence", style="magenta")

            rc = shell_rc_file(shell)
            permanent_aliases = {}
            if os.path.isfile(rc):
                try:
                    with open(rc, "r") as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("alias "):
                                parts = line[6:].split("=", 1)
                                if len(parts) == 2:
                                    permanent_aliases[parts[0]] = parts[1].strip("'\"")
                            elif line.startswith("doskey "):
                                parts = line[7:].split("=", 1)
                                if len(parts) == 2:
                                    permanent_aliases[parts[0]] = parts[1].replace(" $*", "")
                            elif line.startswith("abbr --add "):
                                parts = line[11:].split(" ", 1)
                                if len(parts) == 2:
                                    permanent_aliases[parts[0]] = parts[1].strip("'\"")
                except Exception:
                    pass
            
            all_keys = set(aliases.keys()).union(permanent_aliases.keys())
            if not all_keys:
                console.print("[yellow]No shortcuts found.[/yellow]")
                return True
                
            for k in sorted(all_keys):
                val = aliases.get(k, permanent_aliases.get(k))
                
                is_permanent = k in permanent_aliases
                is_user_session = k in user_session_shortcuts
                
                if is_permanent or is_user_session:
                    origin = "[bold blue]User Created[/bold blue]"
                else:
                    origin = "[dim]System Default[/dim]"
                    
                persistence = "[bold green]Permanent[/bold green]" if is_permanent else "[bold yellow]Temporary[/bold yellow]"
                table.add_row(k, val, origin, persistence)
                
            console.print(table)
        elif subcmd == "arp":
            real_cmd = (["arp", "-a"] + subargs) if IS_WINDOWS else ((["arp", "-an"] + subargs) if IS_MACOS else (["ip", "neigh"] + subargs))
            try:
                proc = subprocess.run(real_cmd, capture_output=True, text=True)
                render_result(proc, label=" ".join(real_cmd), cmd_name="show arp", cmd_arg=" ".join(subargs), show_cmd=show_cmd)
            except Exception as e:
                console.print(f"[red]Error running show arp: {e}[/red]")
        elif subcmd == "mac":
            real_cmd = [sys.executable, "-m", "hopit.mac"] + subargs
            try:
                proc = subprocess.run(real_cmd, capture_output=True, text=True)
                render_result(proc, label=" ".join(real_cmd), cmd_name="show mac", cmd_arg=" ".join(subargs), show_cmd=show_cmd)
            except Exception as e:
                console.print(f"[red]Error running show mac: {e}[/red]")
        elif subcmd == "gateway":
            real_cmd = [sys.executable, "-m", "hopit.gateway"] + subargs
            try:
                proc = subprocess.run(real_cmd, capture_output=True, text=True)
                render_result(proc, label=" ".join(real_cmd), cmd_name="show gateway", cmd_arg=" ".join(subargs), show_cmd=show_cmd)
            except Exception as e:
                console.print(f"[red]Error running show gateway: {e}[/red]")
        elif subcmd == "ip":
            real_cmd = ip_cmd() + subargs
            try:
                proc = subprocess.run(real_cmd, capture_output=True, text=True)
                render_result(proc, label=" ".join(real_cmd), cmd_name="show ip", cmd_arg=" ".join(subargs), show_cmd=show_cmd)
            except Exception as e:
                console.print(f"[red]Error running show ip: {e}[/red]")
        elif subcmd == "route":
            real_cmd = (["route", "print"] + subargs) if IS_WINDOWS else ((["netstat", "-rn"] + subargs) if IS_MACOS else (["ip", "route"] + subargs))
            try:
                proc = subprocess.run(real_cmd, capture_output=True, text=True)
                render_result(proc, label=" ".join(real_cmd), cmd_name="show route", cmd_arg=" ".join(subargs), show_cmd=show_cmd)
            except Exception as e:
                console.print(f"[red]Error running show route: {e}[/red]")
        elif subcmd == "hostname":
            arg = " ".join(subargs) if subargs else ""
            real_cmd = (["powershell", "-Command", f"Rename-Computer -NewName '{arg}'"] if IS_WINDOWS else ["hostname", arg]) if arg else ["hostname"]
            try:
                proc = subprocess.run(real_cmd, capture_output=True, text=True)
                render_result(proc, label=" ".join(real_cmd), cmd_name="show hostname", cmd_arg=arg, show_cmd=show_cmd)
            except Exception as e:
                console.print(f"[red]Error running show hostname: {e}[/red]")
        return True

    if name == "find":
        if rest and rest[0].lower() not in ("-name", "-type", "-path", "-print"):
            valid_subs = ["file", "text"]
            subcmd, matches = resolve_subcommand(rest[0], valid_subs)
            if subcmd:
                subargs = rest[1:]
                if subcmd == "file":
                    if not subargs:
                        console.print("[yellow]Usage: find file <pattern> [path][/yellow]")
                        return True
                    real_cmd = [sys.executable, "-m", "hopit.find"] + subargs
                else:
                    if not subargs:
                        console.print("[yellow]Usage: find text <pattern> [path][/yellow]")
                        return True
                    real_cmd = [sys.executable, "-m", "hopit.grep"] + subargs
                
                try:
                    proc = subprocess.run(real_cmd, capture_output=True, text=True)
                    render_result(proc, label=" ".join(real_cmd), cmd_name=f"find {subcmd}", cmd_arg=" ".join(subargs), show_cmd=show_cmd)
                except Exception as e:
                    console.print(f"[red]Error running find {subcmd}: {e}[/red]")
                return True

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
                            return True
                    if not editor:
                        editor = detect_editor()
                    if not editor:
                        console.print(f"[red]No text editor found (tried nano, vim, vi, micro).[/red]")
                        return True
                    try:
                        subprocess.run([editor, target])
                    except FileNotFoundError:
                        console.print(f"[red]'{editor}' not found.[/red]")
            else:
                console.print(f"[red]'{rest[0]}' — no such file or directory.[/red]")
        return True

    if name == "back":
        try:
            os.chdir("..")
            console.print(f"[green]→ {os.getcwd()}[/green]")
        except OSError as e:
            console.print(f"[red]{e}[/red]")
        return True


    # ── Universal file-system operations (Python shutil) ─────────────────
    if name == "copy":
        if not rest:
            console.print("[yellow]Usage: copy <src> <dest>[/yellow]")
            return True
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
        return True

    if name == "move":
        if len(rest) < 2:
            console.print("[yellow]Usage: move <src> <dest>[/yellow]")
            return True
        src  = os.path.expanduser(rest[0])
        dest = os.path.expanduser(rest[1])
        try:
            shutil.move(src, dest)
            console.print(f"[green]Moved[/green] {src} → {dest}")
        except Exception as e:
            console.print(f"[red]move: {e}[/red]")
        return True

    if name == "remove":
        if not rest:
            console.print("[yellow]Usage: remove [shortcut] <path_or_name>[/yellow]")
            return True
            
        sub_token = rest[0].lower()
        if sub_token == "shortcut":
            if len(rest) < 2:
                console.print("[yellow]Usage: remove shortcut <name>[/yellow]")
                return True
            alias_name = rest[1]
            if alias_name in aliases:
                del aliases[alias_name]
            rc_path = remove_alias_from_rc(shell, alias_name)
            console.print(f"[green]Removed shortcut:[/green] {alias_name}")
            return True

        target = os.path.expanduser(rest[0])
        try:
            if os.path.isdir(target):
                if os.listdir(target):  # non-empty dir — ask first
                    ans = prompt(
                        [("class:prompt", f"Remove '{target}' and all its contents? [y/N]: ")],
                        style=create_prompt_style(get_active_theme()),
                    ).strip().lower()
                    if ans != "y":
                        console.print("[dim]Cancelled.[/dim]")
                        return True
                shutil.rmtree(target)
            else:
                os.remove(target)
            console.print(f"[green]Removed[/green] {target}")
        except Exception as e:
            console.print(f"[red]remove: {e}[/red]")
        return True



    if name == "create":
        if not rest:
            console.print("[cyan]Enter name to create here or full path[/cyan]")
            console.print("[yellow]Usage: create [folder|file|venv] <path>[/yellow]")
            return True
        sub_token = rest[0].lower()
        valid_subs = ["folder", "file", "shortcut", "venv"]
        sub, matches = resolve_subcommand(sub_token, valid_subs)
        if not sub:
            if len(matches) > 1:
                console.print(f"[red]Ambiguous option '{sub_token}'. Candidates: {', '.join(matches)}[/red]")
            else:
                console.print(f"[red]Unknown option '{sub_token}'. Expected 'folder', 'file', 'shortcut', or 'venv'.[/red]")
            return True
            
        if sub == "shortcut":
            shell_name = os.path.basename(shell)
            rc = shell_rc_file(shell)
            try:
                console.print(f"\n[bold cyan]Shortcut Wizard[/bold cyan]  (shell: [green]{shell_name}[/green])")
                alias_name = prompt(
                    [("class:prompt", "Shortcut name: ")],
                    completer=DummyCompleter(), style=create_prompt_style(get_active_theme())
                ).strip()
                if not alias_name:
                    console.print("[red]Shortcut name cannot be empty. Aborting.[/red]")
                    return True
                if " " in alias_name:
                    console.print("[red]Shortcut name must not contain spaces. Aborting.[/red]")
                    return True
                alias_val = prompt(
                    [("class:prompt", f"Command for '{alias_name}': ")],
                    completer=DummyCompleter(), style=create_prompt_style(get_active_theme())
                ).strip()
                if not alias_val:
                    console.print("[red]Command cannot be empty. Aborting.[/red]")
                    return True
                
                ans_completer = WordCompleter(["Permanent", "Temporary"], ignore_case=True)
                ans = prompt(
                    [("class:prompt", "Type (Permanent/Temporary): ")],
                    completer=ans_completer, style=create_prompt_style(get_active_theme())
                ).strip().lower()
                
                aliases[alias_name] = alias_val
                
                if ans == "permanent":
                    rc_path = write_alias_to_rc(shell, alias_name, alias_val)
                    console.print(f"[bold green]Shortcut added (Permanent)![/bold green] [cyan]{alias_name}[/cyan] → [yellow]{alias_val}[/yellow]")
                    if IS_WINDOWS:
                        console.print(f"[dim]Saved to {rc_path} — run it in a new Command Prompt to apply globally.[/dim]")
                    else:
                        console.print(f"[dim]Saved to {rc_path} — it will apply to new terminal sessions automatically.[/dim]")
                else:
                    console.print(f"[bold green]Shortcut added (Temporary)![/bold green] [cyan]{alias_name}[/cyan] → [yellow]{alias_val}[/yellow]")
                    console.print("[dim]This shortcut will only work during this Hopit session.[/dim]")
            except KeyboardInterrupt:
                console.print("\n[dim]Cancelled.[/dim]")
            return True

        if len(rest) < 2:
            console.print("[cyan]Enter name to create here or full path[/cyan]")
            console.print(f"[yellow]Usage: create {sub} <path>[/yellow]")
            return True
        target_path = os.path.expanduser(rest[1])
        if sub == "folder":
            try:
                os.makedirs(target_path, exist_ok=True)
                console.print(f"[green]Created folder:[/green] {target_path}")
            except Exception as e:
                console.print(f"[red]create folder: {e}[/red]")
        elif sub == "file":
            try:
                if os.path.exists(target_path):
                    console.print(f"[yellow]File already exists:[/yellow] {target_path}")
                else:
                    parent = os.path.dirname(target_path)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    with open(target_path, 'w') as f:
                        pass
                    console.print(f"[green]Created file:[/green] {target_path}")
            except Exception as e:
                console.print(f"[red]create file: {e}[/red]")
        elif sub == "venv":
            env_path = os.path.abspath(target_path)
            parent_dir = os.path.dirname(env_path) or "."

            # --- Pre-flight checks ---
            if os.path.isfile(env_path):
                console.print(
                    f"[red]Cannot create virtual environment:[/red] "
                    f"[yellow]'{env_path}'[/yellow] already exists as a [bold]file[/bold]. "
                    f"Remove or rename it first."
                )
                return True
            if os.path.isdir(env_path) and os.path.isfile(os.path.join(env_path, "pyvenv.cfg")):
                console.print(
                    f"[yellow]'{env_path}' is already a virtual environment.[/yellow] "
                    f"Use [green]enter venv {env_path}[/green] to activate it."
                )
                return True
            if not os.access(parent_dir, os.W_OK):
                console.print(
                    f"[red]Cannot create virtual environment:[/red] "
                    f"No write permission to [yellow]'{parent_dir}'[/yellow]."
                )
                return True

            # --- Create ---
            real_cmd = [sys.executable, "-m", "venv", env_path]
            console.print(f"[cyan]Creating virtual environment at '{env_path}'...[/cyan]")
            try:
                proc = subprocess.run(real_cmd, capture_output=True, text=True)
                if proc.returncode == 0:
                    console.print(f"[bold green]✓ Virtual environment created:[/bold green] {env_path}")
                    console.print(f"  Run [green]enter venv {env_path}[/green] to activate it.")
                else:
                    err = (proc.stderr or proc.stdout or "").strip()
                    console.print(f"[red]Failed to create virtual environment.[/red]")
                    if err:
                        console.print(f"[dim]{err}[/dim]")
                    if "ensurepip" in err or "pip" in err.lower():
                        console.print("[yellow]Tip:[/yellow] Try installing python3-venv: [green]sudo dnf install python3-venv[/green]")
            except Exception as e:
                console.print(f"[red]Error creating virtual environment: {e}[/red]")
        return True

    if name == "enter":
        if not rest:
            console.print("[yellow]Usage: enter venv <path>[/yellow]")
            return True
        sub_token = rest[0].lower()
        subargs = rest[1:]
        valid_subs = ["venv"]
        subcmd, matches = resolve_subcommand(sub_token, valid_subs)
        if not subcmd:
            console.print(f"[red]Unknown enter subcommand '{sub_token}'. Supported: venv[/red]")
            return True
        if subcmd == "venv":
            if not subargs:
                console.print("[yellow]Usage: enter venv <env_path>[/yellow]")
                return True
            env_path = os.path.abspath(os.path.expanduser(subargs[0]))
            if not os.path.isdir(env_path):
                console.print(f"[red]Virtual environment directory '{env_path}' does not exist.[/red]")
                return True
            bin_dir = os.path.join(env_path, "Scripts" if IS_WINDOWS else "bin")
            if not os.path.isdir(bin_dir):
                console.print(f"[red]Invalid virtual environment (bin/Scripts directory not found under '{env_path}').[/red]")
                return True
            # Prepend bin_dir to PATH and set VIRTUAL_ENV
            os.environ["VIRTUAL_ENV"] = env_path
            # To avoid duplicates in PATH:
            path_parts = os.environ.get("PATH", "").split(os.pathsep)
            if bin_dir in path_parts:
                path_parts.remove(bin_dir)
            os.environ["PATH"] = bin_dir + os.pathsep + os.pathsep.join(path_parts)
            console.print(f"[bold green]Activated virtual environment:[/bold green] {env_path}")
        return True

    # ── Disk interactive wizard ───────────────────────────────────────────
    if name in ("disk", "drive"):
        if not rest:
            console.print("[yellow]Usage: disk [list|usage|mount|unmount|check][/yellow]")
            return True

        sub_token = rest[0].lower()
        valid_subs = ["list", "usage", "mount", "unmount", "check", "health", "format"]
        subcmd, matches = resolve_subcommand(sub_token, valid_subs)
        if not subcmd:
            if len(matches) > 1:
                console.print(f"[red]Ambiguous disk subcommand '{sub_token}'. Candidates: {', '.join(matches)}[/red]")
            else:
                console.print(f"[red]Unknown disk subcommand '{sub_token}'. Supported: {', '.join(valid_subs)}[/red]")
            return True

        subargs = rest[1:]
        current_style = create_prompt_style(get_active_theme())

        if subcmd == "list":
            # No argument needed — fall through to normal dispatch
            pass

        elif subcmd == "usage":
            if not subargs:
                # No path given — run for whole system (df -h)
                subargs = []
            # Execute directly
            real_cmd = with_privilege(disk_cmd("usage " + " ".join(subargs)), False)
            if not real_cmd:
                console.print("[red]Could not build disk usage command.[/red]")
                return True
            try:
                proc = subprocess.run(real_cmd, capture_output=True, text=True)
                render_result(proc, label=" ".join(real_cmd), cmd_name="disk", cmd_arg="usage", show_cmd=show_cmd)
            except Exception as e:
                console.print(f"[red]disk usage: {e}[/red]")
            return True

        elif subcmd == "check":
            # Require a target device or mount point
            if not subargs:
                console.print("[bold cyan]\n💾 Disk Check — Select Target[/bold cyan]")
                # Build dropdown: mounted filesystems + raw block devices
                candidates = []
                for mnt, desc in load_mount_points():
                    candidates.append((mnt, f"[mounted]  {desc}"))
                for dev, desc in load_block_devices():
                    candidates.append((dev, f"[device]   {desc}"))
                if not candidates:
                    console.print("[yellow]No block devices or mount points found. Enter device path manually:[/yellow]")
                    try:
                        target = prompt(
                            [("class:prompt", "Target (e.g. /dev/sda1 or /mnt): ")],
                            completer=DummyCompleter(), style=current_style
                        ).strip()
                    except KeyboardInterrupt:
                        console.print("\n[dim]Cancelled.[/dim]")
                        return True
                    if not target:
                        console.print("[red]No target specified. Aborting.[/red]")
                        return True
                    subargs = [target]
                else:
                    dev_completer = WordCompleter([c[0] for c in candidates], ignore_case=True)
                    console.print("[dim]Tab to browse devices/mount points, Enter to select[/dim]")
                    for dev, desc in candidates:
                        console.print(f"  [cyan]{dev:<20}[/cyan]  [dim]{desc}[/dim]")
                    console.print()
                    try:
                        target = prompt(
                            [("class:prompt", "Target device or mount point: ")],
                            completer=dev_completer, style=current_style
                        ).strip()
                    except KeyboardInterrupt:
                        console.print("\n[dim]Cancelled.[/dim]")
                        return True
                    if not target:
                        console.print("[red]No target selected. Aborting.[/red]")
                        return True
                    subargs = [target]
            real_cmd = with_privilege(disk_cmd("check " + " ".join(subargs)), True)
            if not real_cmd:
                console.print("[red]Could not build fsck command for this platform.[/red]")
                return True
            try:
                subprocess.run(real_cmd)
            except FileNotFoundError:
                console.print(f"[red]'{real_cmd[0]}' not found on this system.[/red]")
            except KeyboardInterrupt:
                console.print("\n[dim]Stopped.[/dim]")
            return True

        elif subcmd == "health":
            # For Windows, no target required as Get-PhysicalDisk returns all
            if IS_WINDOWS:
                subargs = []
            elif not subargs:
                console.print("[bold cyan]\n🩺 Disk Health — Select Target[/bold cyan]")
                candidates = []
                for dev, desc in load_block_devices():
                    candidates.append((dev, f"[device]   {desc}"))
                if not candidates:
                    console.print("[yellow]No block devices found. Enter device path manually:[/yellow]")
                    try:
                        target = prompt(
                            [("class:prompt", "Target device (e.g. /dev/sda): ")],
                            completer=DummyCompleter(), style=current_style
                        ).strip()
                    except KeyboardInterrupt:
                        console.print("\n[dim]Cancelled.[/dim]")
                        return True
                    if not target:
                        console.print("[red]No target specified. Aborting.[/red]")
                        return True
                    subargs = [target]
                else:
                    dev_completer = WordCompleter([c[0] for c in candidates], ignore_case=True)
                    console.print("[dim]Tab to browse devices, Enter to select[/dim]")
                    for dev, desc in candidates:
                        console.print(f"  [cyan]{dev:<20}[/cyan]  [dim]{desc}[/dim]")
                    console.print()
                    try:
                        target = prompt(
                            [("class:prompt", "Target device: ")],
                            completer=dev_completer, style=current_style
                        ).strip()
                    except KeyboardInterrupt:
                        console.print("\n[dim]Cancelled.[/dim]")
                        return True
                    if not target:
                        console.print("[red]No target selected. Aborting.[/red]")
                        return True
                    subargs = [target]

            real_cmd = with_privilege(disk_cmd("health " + " ".join(subargs)), True)
            if not real_cmd:
                console.print("[red]Could not build health command for this platform.[/red]")
                return True
            try:
                proc = subprocess.run(real_cmd, capture_output=True, text=True)
                render_result(proc, label=" ".join(real_cmd), cmd_name="disk", cmd_arg="health", show_cmd=show_cmd)
            except Exception as e:
                console.print(f"[red]disk health: {e}[/red]")
            return True

        elif subcmd == "format":
            if not subargs:
                console.print("[bold red]\n⚠️  Format — Destructive Operation[/bold red]")
                candidates = []
                for dev, desc in load_block_devices():
                    candidates.append((dev, f"[device]   {desc}"))
                if not candidates:
                    console.print("[yellow]No block devices found. Enter device path manually:[/yellow]")
                    try:
                        target = prompt(
                            [("class:prompt", "Device to format (e.g. /dev/sdb1): ")],
                            completer=DummyCompleter(), style=current_style
                        ).strip()
                    except KeyboardInterrupt:
                        console.print("\n[dim]Cancelled.[/dim]")
                        return True
                else:
                    dev_completer = WordCompleter([c[0] for c in candidates], ignore_case=True)
                    console.print("[dim]Tab to browse devices, Enter to select[/dim]")
                    for dev, desc in candidates:
                        console.print(f"  [cyan]{dev:<20}[/cyan]  [dim]{desc}[/dim]")
                    console.print()
                    try:
                        target = prompt(
                            [("class:prompt", "Device to format: ")],
                            completer=dev_completer, style=current_style
                        ).strip()
                    except KeyboardInterrupt:
                        console.print("\n[dim]Cancelled.[/dim]")
                        return True
                
                if not target:
                    console.print("[red]No device selected. Aborting.[/red]")
                    return True

                fs_options = ["ext4", "ntfs", "vfat", "exfat", "btrfs"] if not IS_MACOS else ["APFS", "ExFAT", "MS-DOS", "HFS+"]
                fs_completer = WordCompleter(fs_options, ignore_case=True)
                console.print(f"[dim]Available filesystems: {', '.join(fs_options)}[/dim]")
                try:
                    fs = prompt(
                        [("class:prompt", "Filesystem type: ")],
                        completer=fs_completer, style=current_style
                    ).strip()
                except KeyboardInterrupt:
                    console.print("\n[dim]Cancelled.[/dim]")
                    return True

                if not fs:
                    console.print("[red]No filesystem specified. Aborting.[/red]")
                    return True
                
                console.print(f"\n[bold red]WARNING: This will permanently erase ALL data on {target} and format it as {fs}.[/bold red]")
                try:
                    confirm = prompt(
                        [("class:prompt", "Type 'YES' to confirm destruction of data: ")],
                        completer=DummyCompleter(), style=current_style
                    ).strip()
                except KeyboardInterrupt:
                    console.print("\n[dim]Cancelled.[/dim]")
                    return True

                if confirm != "YES":
                    console.print("[green]Format cancelled. Your data is safe.[/green]")
                    return True
                
                subargs = [target, fs]
                
            real_cmd = with_privilege(disk_cmd("format " + " ".join(subargs)), True)
            if not real_cmd:
                console.print("[red]Could not build format command for this platform.[/red]")
                return True
            try:
                console.print(f"[cyan]Formatting {subargs[0]} as {subargs[1]}...[/cyan]")
                proc = subprocess.run(real_cmd, capture_output=True, text=True)
                render_result(proc, label=" ".join(real_cmd), cmd_name="disk", cmd_arg="format", show_cmd=show_cmd)
            except Exception as e:
                console.print(f"[red]disk format: {e}[/red]")
            return True

        elif subcmd == "unmount":
            if not subargs:
                console.print("[bold cyan]\n💿 Unmount — Select Volume[/bold cyan]")
                candidates = load_mount_points()
                if not candidates:
                    console.print("[yellow]No mounted volumes found. Enter mount point manually:[/yellow]")
                    try:
                        target = prompt(
                            [("class:prompt", "Mount point to unmount: ")],
                            completer=DummyCompleter(), style=current_style
                        ).strip()
                    except KeyboardInterrupt:
                        console.print("\n[dim]Cancelled.[/dim]")
                        return True
                    if not target:
                        console.print("[red]No target specified. Aborting.[/red]")
                        return True
                    subargs = [target]
                else:
                    mnt_completer = WordCompleter([m for m, _ in candidates], ignore_case=True)
                    console.print("[dim]Tab to browse, Enter to select[/dim]")
                    for mnt, desc in candidates:
                        console.print(f"  [cyan]{mnt:<30}[/cyan]  [dim]{desc}[/dim]")
                    console.print()
                    try:
                        target = prompt(
                            [("class:prompt", "Mount point to unmount: ")],
                            completer=mnt_completer, style=current_style
                        ).strip()
                    except KeyboardInterrupt:
                        console.print("\n[dim]Cancelled.[/dim]")
                        return True
                    if not target:
                        console.print("[red]No target selected. Aborting.[/red]")
                        return True
                    subargs = [target]
            real_cmd = with_privilege(disk_cmd("unmount " + " ".join(subargs)), True)
            if not real_cmd:
                console.print("[red]Could not build unmount command.[/red]")
                return True
            try:
                proc = subprocess.run(real_cmd, capture_output=True, text=True)
                render_result(proc, label=" ".join(real_cmd), cmd_name="disk", cmd_arg="unmount", show_cmd=show_cmd)
            except Exception as e:
                console.print(f"[red]disk unmount: {e}[/red]")
            return True

        elif subcmd == "mount":
            if len(subargs) < 2:
                # Need device and mount point — prompt interactively
                console.print("[bold cyan]\n💾 Mount — Step 1: Select Device[/bold cyan]")
                devs = load_block_devices()
                if not devs:
                    console.print("[yellow]No block devices found. Enter device path manually:[/yellow]")
                    try:
                        device = prompt(
                            [("class:prompt", "Device (e.g. /dev/sdb1): ")],
                            completer=DummyCompleter(), style=current_style
                        ).strip()
                    except KeyboardInterrupt:
                        console.print("\n[dim]Cancelled.[/dim]")
                        return True
                else:
                    dev_completer = WordCompleter([d for d, _ in devs], ignore_case=True)
                    console.print("[dim]Tab to browse block devices, Enter to select[/dim]")
                    for dev, desc in devs:
                        console.print(f"  [cyan]{dev:<20}[/cyan]  [dim]{desc}[/dim]")
                    console.print()
                    # Use the first subarg if already provided, else prompt
                    if subargs:
                        device = subargs[0]
                    else:
                        try:
                            device = prompt(
                                [("class:prompt", "Device to mount: ")],
                                completer=dev_completer, style=current_style
                            ).strip()
                        except KeyboardInterrupt:
                            console.print("\n[dim]Cancelled.[/dim]")
                            return True
                if not device:
                    console.print("[red]No device selected. Aborting.[/red]")
                    return True

                console.print("[bold cyan]\n📁 Mount — Step 2: Select Mount Point Directory[/bold cyan]")
                console.print("[dim]Common locations: /mnt, /media, /run/media[/dim]")
                path_completer = WordCompleter(["/mnt/", "/media/", "/run/media/", "/tmp/"], ignore_case=True)
                try:
                    mount_point = prompt(
                        [("class:prompt", "Mount point directory: ")],
                        completer=path_completer, style=current_style
                    ).strip()
                except KeyboardInterrupt:
                    console.print("\n[dim]Cancelled.[/dim]")
                    return True
                if not mount_point:
                    console.print("[red]No mount point specified. Aborting.[/red]")
                    return True
                subargs = [device, mount_point]

            real_cmd = with_privilege(disk_cmd("mount " + " ".join(subargs)), True)
            if not real_cmd:
                console.print("[red]Could not build mount command for this platform.[/red]")
                return True
            try:
                proc = subprocess.run(real_cmd, capture_output=True, text=True)
                render_result(proc, label=" ".join(real_cmd), cmd_name="disk", cmd_arg="mount", show_cmd=show_cmd)
            except Exception as e:
                console.print(f"[red]disk mount: {e}[/red]")
            return True

        # subcmd == 'list': fall through to generic dispatch below
        # (disk list needs no args and uses the commands dict runner)
        rest = [subcmd] + subargs

    if name == "netconfig":
        if not rest:
            console.print("[yellow]Please specify an adapter or subcommand, e.g., 'netconfig eth0', 'netconfig dhcp release eth0' or 'netconfig reset eth0'[/yellow]")
            return True

        if len(rest) >= 2 and rest[0].lower() == "reset":
            adapter = rest[1]
            if IS_WINDOWS:
                console.print(f"[cyan]Resetting adapter {adapter} to DHCP...[/cyan]")
                subprocess.run(["netsh", "interface", "ip", "set", "address", f"name={adapter}", "source=dhcp"])
                subprocess.run(["netsh", "interface", "ip", "set", "dns", f"name={adapter}", "source=dhcp"])
                console.print("[bold green]Reset complete.[/bold green]")
                return True
                
            if IS_MACOS:
                console.print(f"[cyan]Resetting adapter {adapter} to DHCP...[/cyan]")
                subprocess.run(with_privilege(["networksetup", "-setdhcp", adapter], True))
                subprocess.run(with_privilege(["networksetup", "-setdnsservers", adapter, "Empty"], True))
                console.print("[bold green]Reset complete.[/bold green]")
                return True
                
            # Linux
            if not os.path.exists(f"/sys/class/net/{adapter}"):
                console.print(f"[red]Adapter '{adapter}' not found on this system.[/red]")
                return True
                
            if shutil.which("nmcli"):
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
                    
                if not conn_name:
                    conn_name = adapter
                    
                console.print(f"[cyan]Resetting adapter {adapter} to DHCP and clearing manual settings...[/cyan]")
                try:
                    subprocess.run(with_privilege(["nmcli", "con", "mod", conn_name, "ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", ""], True), stderr=subprocess.DEVNULL)
                    subprocess.run(with_privilege(["nmcli", "con", "up", conn_name], True), stderr=subprocess.DEVNULL)
                except Exception:
                    pass
                console.print("[bold green]Reset complete.[/bold green]")
            else:
                console.print("[red]'nmcli' is required on Linux to perform a full adapter reset.[/red]")
            return True

        if len(rest) >= 2 and rest[0].lower() == "dhcp" and rest[1].lower() in ("release", "renew"):
            action = rest[1].lower()
            adapter = rest[2] if len(rest) > 2 else None
            if not adapter:
                console.print(f"[yellow]Please specify an adapter for dhcp {action}, e.g., 'netconfig dhcp {action} eth0'[/yellow]")
                return True
                
            if IS_WINDOWS:
                console.print(f"[cyan]Running IPConfig /{action} for {adapter}...[/cyan]")
                subprocess.run(["ipconfig", f"/{action}", adapter])
                return True
            
            if IS_MACOS:
                if action == "renew":
                    console.print(f"[cyan]Renewing DHCP lease for {adapter}...[/cyan]")
                    subprocess.run(with_privilege(["ipconfig", "set", adapter, "DHCP"], True))
                else:
                    console.print(f"[yellow]DHCP release is not directly supported via CLI on macOS without bringing down the interface. Use 'netconfig {adapter}' instead.[/yellow]")
                return True
                
            # Linux
            if not os.path.exists(f"/sys/class/net/{adapter}"):
                console.print(f"[red]Adapter '{adapter}' not found on this system.[/red]")
                return True
                
            if shutil.which("dhclient"):
                if action == "release":
                    console.print(f"[cyan]Releasing DHCP lease for {adapter}...[/cyan]")
                    subprocess.run(with_privilege(["dhclient", "-r", adapter], True))
                else:
                    console.print(f"[cyan]Renewing DHCP lease for {adapter}...[/cyan]")
                    subprocess.run(with_privilege(["dhclient", adapter], True))
            else:
                if shutil.which("nmcli"):
                    if action == "release":
                        console.print(f"[cyan]Releasing DHCP lease (bringing down) for {adapter}...[/cyan]")
                        subprocess.run(with_privilege(["nmcli", "dev", "disconnect", adapter], True))
                    else:
                        console.print(f"[cyan]Renewing DHCP lease (bringing up) for {adapter}...[/cyan]")
                        subprocess.run(with_privilege(["nmcli", "dev", "connect", adapter], True))
                else:
                    console.print("[red]Neither 'dhclient' nor 'nmcli' found to manage DHCP leases.[/red]")
            return True

        if IS_WINDOWS:
            configure_windows_network(rest[0], create_prompt_style(get_active_theme()))
            return True
        if IS_MACOS:
            configure_macos_network(rest[0], create_prompt_style(get_active_theme()))
            return True
        adapter = rest[0]
        if not os.path.exists(f"/sys/class/net/{adapter}"):
            console.print(f"[red]Adapter '{adapter}' not found on this system.[/red]")
            return True

        if not shutil.which("nmcli"):
            console.print("[red]NetworkManager (nmcli) is not installed. Currently, only NetworkManager is supported for this feature.[/red]")
            return True

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
            mode = prompt([("class:prompt", "Action [dhcp/static/up/down]: ")], completer=mode_completer, style=create_prompt_style(get_active_theme())).strip().lower()
            
            if mode not in ("dhcp", "static", "up", "down"):
                console.print("[red]Invalid action. Aborting.[/red]")
                return True
            
            if not conn_name:
                if mode in ("up", "down"):
                    console.print(f"[red]Cannot bring {mode} a non-existent connection. Please use dhcp or static first.[/red]")
                    return True
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
                ip_addr = prompt([("class:prompt", "IP Address with subnet (e.g. 192.168.1.50/24): ")], completer=empty, style=create_prompt_style(get_active_theme())).strip()
                gw = prompt([("class:prompt", "Gateway (e.g. 192.168.1.1): ")], completer=empty, style=create_prompt_style(get_active_theme())).strip()
                dns = prompt([("class:prompt", "DNS (e.g. 8.8.8.8): ")], completer=empty, style=create_prompt_style(get_active_theme())).strip()
                
                if not ip_addr:
                    console.print("[red]IP address is required. Aborting.[/red]")
                    return True
                    
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
        return True

    cmd = commands[name]

    if cmd.needs_arg and not rest:
        console.print(f"[yellow]'{name}' needs an argument, e.g.:[/yellow] {name} <name>")
        return True

    arg = shlex.join(rest) if rest else ""
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
        return True

    try:
        proc = subprocess.run(real_cmd, capture_output=True, text=True)
    except FileNotFoundError:
        console.print(f"[red]'{real_cmd[0]}' not found on this system.[/red]")
        return True
    except subprocess.TimeoutExpired:
        console.print("[red]Command timed out.[/red]")
        return True

    render_result(proc, label=" ".join(real_cmd), cmd_name=name, cmd_arg=arg, show_cmd=show_cmd)
    return True


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

    users_holder = BackgroundNames(load_users, start_immediately=False)
    groups_holder = BackgroundNames(load_groups, start_immediately=False)

    names = {
        "service": lambda: services,
        "installed_pkg": lambda: installed_pkgs,
        "available_pkg": available_pkg_getter,
        "path": load_path_entries,
        "adapter": load_adapters,
        "user": users_holder.get,
        "group": groups_holder.get,
    }

    commands = build_commands(manager, names)
    all_names = list(commands.keys()) + list(BUILTIN_DESCRIPTIONS.keys())
    completer = LazyCompleter(commands, aliases)

    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.filters import has_completions

    kb = KeyBindings()

    @kb.add("right", filter=has_completions)
    def _(event):
        buffer = event.current_buffer
        if buffer.complete_state and buffer.complete_state.current_completion:
            completion = buffer.complete_state.current_completion
            buffer.apply_completion(completion)
            if completion.text.endswith("/") or completion.text.endswith("\\"):
                buffer.start_completion()
        else:
            buffer.cursor_right()

    @kb.add("space", filter=has_completions)
    def _(event):
        buffer = event.current_buffer
        if buffer.complete_state and buffer.complete_state.current_completion:
            completion = buffer.complete_state.current_completion
            buffer.apply_completion(completion)
            if completion.text.endswith("/") or completion.text.endswith("\\"):
                buffer.start_completion()
            else:
                buffer.insert_text(" ")
        else:
            buffer.insert_text(" ")

    def bottom_toolbar():
        return HTML(" <b>Tab</b> complete  •  <b>→ / Space</b> drill into folder  •  <b>Enter</b> run  •  <b>Ctrl-D</b> quit")

    session = PromptSession(
        history=InMemoryHistory(),
        completer=completer,
        complete_while_typing=True,
        bottom_toolbar=bottom_toolbar,
        key_bindings=kb,
    )

    theme = get_active_theme()
    distro = read_os_pretty_name()
    mgr_label = MANAGER_DISPLAY_NAME.get(manager, "none detected")
    console.print(Panel.fit(
        Text("Hopit-CLI Shell", style="bold cyan") + Text(f"  •  {distro}  •  Pkg Manager: {mgr_label}"),
        border_style=theme.get("border", "cyan"),
    ))
    console.print("[dim]Type 'help' or append '?' to any command for interactive assistance.[/dim]\n")

    while True:
        try:
            current_theme = get_active_theme()
            current_style = create_prompt_style(current_theme)

            cwd = os.getcwd()
            home = os.path.expanduser("~")
            display_cwd = cwd.replace(home, "~", 1) if cwd.startswith(home) else cwd
            
            user = getpass.getuser()
            now = datetime.now().strftime("%H:%M")
            branch = get_git_branch()
            
            # Powerline arrow glyph and git icon need a Nerd Font.
            # Controlled by the nerd_fonts configuration setting.
            use_powerline = is_nerd_fonts_enabled()
            sep      = "\ue0b0" if use_powerline else "|"
            end_sep  = sep if use_powerline else ""
            git_icon = " " if use_powerline else ""

            venv_path = os.environ.get("VIRTUAL_ENV")
            venv_suffix = f" ({os.path.basename(venv_path)})" if venv_path else ""
            prompt_fragments = [
                ("class:hopit", f" hopit-cli{venv_suffix} "),
                ("class:hopit_sep", sep),
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
                ("class:time_sep", end_sep),
                ("", " "),
            ])
            
            try:
                line = session.prompt(prompt_fragments, style=current_style).strip()
            except EOFError:
                break
            except KeyboardInterrupt:
                continue

            if not line:
                continue

            try:
                keep_running = execute_line(line, shell, aliases, all_names, commands, manager, session)
                if not keep_running:
                    break
            except KeyboardInterrupt:
                console.print("\n[dim]stopped.[/dim]")
                continue

        except KeyboardInterrupt:
            # Outer fallback catch-all for the prompt generation code
            continue

    console.print("[dim]bye[/dim]")
