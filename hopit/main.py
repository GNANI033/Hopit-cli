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
)
from hopit.loaders import (
    load_service_names,
    load_installed_packages,
    load_available_packages,
    load_path_entries,
    load_adapters,
    load_users,
    load_groups,
    BackgroundNames,
    MANAGER_PKG,
    MANAGER_DISPLAY_NAME,
)
from hopit.commands import build_commands, BUILTIN_DESCRIPTIONS
from hopit.ui import (
    LazyCompleter,
    resolve_command,
    print_help,
    render_result,
    configure_macos_network,
    configure_windows_network,
)
from hopit.translation import translate_cross_platform

PROMPT_STYLE = Style.from_dict({
    "hopit": "bg:#f38ba8 fg:#1e1e2e bold",
    "hopit_sep": "fg:#f38ba8 bg:#fab387",
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


def run_shell_line(line: str, shell: str):
    if IS_WINDOWS:
        subprocess.run(line, shell=True)
    else:
        subprocess.run(line, shell=True, executable=shell)


def show_command_help(cmd_name: str, commands: dict):
    from rich.panel import Panel
    desc = ""
    usage = ""
    sudo_req = False

    if cmd_name in BUILTIN_DESCRIPTIONS:
        desc = BUILTIN_DESCRIPTIONS[cmd_name]
        usage = cmd_name
    elif cmd_name in commands:
        cmd = commands[cmd_name]
        desc = cmd.desc
        sudo_req = cmd.needs_sudo
        
        # Build usage syntax based on properties
        if cmd_name == "git":
            usage = "git <subcommand> [args...]"
        elif cmd_name == "sqlite":
            usage = "sqlite <database_file> [SQL query]"
        elif cmd_name == "config":
            usage = "config [set <setting> <value> | reset]"
        elif cmd_name == "copy":
            usage = "copy <source> <destination>"
        elif cmd_name == "move":
            usage = "move <source> <destination>"
        elif cmd_name == "reboot":
            usage = "reboot [minutes | HH:MM]"
        elif cmd_name == "shutdown":
            usage = "shutdown [minutes | HH:MM]"
        elif cmd_name == "chmod":
            usage = "chmod <permissions> <path>"
        elif cmd_name == "chown":
            usage = "chown <owner>[:group] <path>"
        elif cmd_name == "chgrp":
            usage = "chgrp <group> <path>"
        elif cmd_name in ("useradd", "adduser"):
            usage = f"{cmd_name} <username> [password]"
        elif cmd_name in ("userdel", "deluser"):
            usage = f"{cmd_name} <username>"
        elif cmd_name == "usermod":
            usage = "usermod -aG <group> <username>"
        elif cmd_name == "passwd":
            usage = f"{cmd_name} [username]"
        elif cmd_name in ("groupadd", "addgroup"):
            usage = f"{cmd_name} <groupname>"
        elif cmd_name in ("groupdel", "delgroup"):
            usage = f"{cmd_name} <groupname>"
        elif cmd_name == "user":
            usage = "user [add|remove|passwd|join|list] [args...]"
        elif cmd_name == "group":
            usage = "group [add|remove|list] [args...]"
        elif cmd_name in ("permission", "permissions"):
            usage = f"{cmd_name} [set|owner|group] [args...]"
        else:
            if cmd.needs_arg:
                kind = cmd.arg_completion_kind or "arg"
                usage = f"{cmd_name} <{kind}>"
            else:
                usage = cmd_name

    help_text = f"[bold cyan]Description:[/bold cyan] {desc}\n"
    help_text += f"[bold cyan]Usage:[/bold cyan]       [yellow]{usage}[/yellow]\n"
    if sudo_req:
        help_text += "[bold red]Note:[/bold red]        Requires root/sudo privileges.\n"

    panel = Panel(
        help_text.strip(),
        title=f"[bold green]Help: {cmd_name}[/bold green]",
        border_style="cyan",
        expand=False
    )
    console.print(panel)


def show_context_help(words: list[str], commands: dict):
    from rich.panel import Panel
    from rich.table import Table
    
    first_word = words[0].lower()
    resolved, _ = resolve_command(list(commands.keys()) + list(BUILTIN_DESCRIPTIONS.keys()), first_word)
    if not resolved:
        console.print(f"[red]Unknown command: {first_word}[/red]")
        return
        
    subcmd = words[1].lower() if len(words) > 1 else ""
    rest = words[2:]
    
    title = f"[bold green]Help: {' '.join(words)} ?[/bold green]"
    
    # --- USER command context help ---
    if resolved == "user":
        if not subcmd:
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_row("[green]add[/green]", "Add a new system user account")
            table.add_row("[green]remove[/green]", "Delete an existing system user account")
            table.add_row("[green]delete[/green]", "Delete an existing system user account")
            table.add_row("[green]passwd[/green]", "Change a user's password")
            table.add_row("[green]password[/green]", "Change a user's password")
            table.add_row("[green]join[/green]", "Add a user to a group")
            table.add_row("[green]list[/green]", "List all local users")
            console.print(Panel(table, title=title, border_style="cyan", expand=False))
            return
            
        if subcmd == "add":
            if not rest:
                console.print(Panel("[yellow]<username>[/yellow]  Specify the name of the new user", title=title, border_style="cyan", expand=False))
            elif len(rest) == 1:
                console.print(Panel("[yellow][password][/yellow]  Specify the password for the new user (optional)", title=title, border_style="cyan", expand=False))
            else:
                console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
            return
            
        if subcmd in ("remove", "delete"):
            if not rest:
                console.print(Panel("[yellow]<username>[/yellow]  Specify the user account to delete", title=title, border_style="cyan", expand=False))
            else:
                console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
            return
            
        if subcmd in ("passwd", "password"):
            if not rest:
                console.print(Panel("[yellow]<username>[/yellow]  Specify the user account to change password", title=title, border_style="cyan", expand=False))
            elif len(rest) == 1:
                console.print(Panel("[yellow][password][/yellow]  Specify the new password (optional)", title=title, border_style="cyan", expand=False))
            else:
                console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
            return
            
        if subcmd == "join":
            if not rest:
                console.print(Panel("[yellow]<group>[/yellow]     Specify the group name", title=title, border_style="cyan", expand=False))
            elif len(rest) == 1:
                console.print(Panel("[yellow]<username>[/yellow]  Specify the user to add to the group", title=title, border_style="cyan", expand=False))
            else:
                console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
            return
            
        if subcmd == "list":
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
            return

    # --- GROUP command context help ---
    if resolved == "group":
        if not subcmd:
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_row("[green]add[/green]", "Add a new system group")
            table.add_row("[green]remove[/green]", "Delete an existing system group")
            table.add_row("[green]delete[/green]", "Delete an existing system group")
            table.add_row("[green]list[/green]", "List all local groups")
            console.print(Panel(table, title=title, border_style="cyan", expand=False))
            return
            
        if subcmd == "add":
            if not rest:
                console.print(Panel("[yellow]<groupname>[/yellow]  Specify the name of the new group", title=title, border_style="cyan", expand=False))
            else:
                console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
            return
            
        if subcmd in ("remove", "delete"):
            if not rest:
                console.print(Panel("[yellow]<groupname>[/yellow]  Specify the group to delete", title=title, border_style="cyan", expand=False))
            else:
                console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
            return
            
        if subcmd == "list":
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
            return

    # --- PERMISSION / PERMISSIONS command context help ---
    if resolved in ("permission", "permissions"):
        if not subcmd:
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_row("[green]set[/green]", "Set read/write/execute permissions (chmod)")
            table.add_row("[green]owner[/green]", "Change owner of file or folder (chown)")
            table.add_row("[green]group[/green]", "Change group of file or folder (chgrp)")
            console.print(Panel(table, title=title, border_style="cyan", expand=False))
            return
            
        if subcmd == "set":
            if not rest:
                console.print(Panel("[yellow]<permissions>[/yellow]  Specify octal (e.g. 755, 644) or symbolic (e.g. +x, g+w) permissions", title=title, border_style="cyan", expand=False))
            elif len(rest) == 1:
                console.print(Panel("[yellow]<path>[/yellow]         Specify the file or folder path", title=title, border_style="cyan", expand=False))
            else:
                console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
            return
            
        if subcmd == "owner":
            if not rest:
                console.print(Panel("[yellow]<owner>[/yellow]  Specify the username to assign as owner", title=title, border_style="cyan", expand=False))
            elif len(rest) == 1:
                console.print(Panel("[yellow]<path>[/yellow]   Specify the file or folder path", title=title, border_style="cyan", expand=False))
            else:
                console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
            return
            
        if subcmd == "group":
            if not rest:
                console.print(Panel("[yellow]<group>[/yellow]  Specify the group to assign", title=title, border_style="cyan", expand=False))
            elif len(rest) == 1:
                console.print(Panel("[yellow]<path>[/yellow]  Specify the file or folder path", title=title, border_style="cyan", expand=False))
            else:
                console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
            return

    # --- Fallback / traditional command context help ---
    if resolved == "chmod":
        if not subcmd:
            console.print(Panel("[yellow]<permissions>[/yellow]  Specify octal (e.g. 755, 644) or symbolic (e.g. +x) permissions", title=title, border_style="cyan", expand=False))
        elif len(words) == 2:
            console.print(Panel("[yellow]<path>[/yellow]         Specify the file or folder path", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return
        
    if resolved == "chown":
        if not subcmd:
            console.print(Panel("[yellow]<owner>[/yellow]  Specify the owner username", title=title, border_style="cyan", expand=False))
        elif len(words) == 2:
            console.print(Panel("[yellow]<path>[/yellow]   Specify the file or folder path", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return
        
    if resolved == "chgrp":
        if not subcmd:
            console.print(Panel("[yellow]<group>[/yellow]  Specify the group name", title=title, border_style="cyan", expand=False))
        elif len(words) == 2:
            console.print(Panel("[yellow]<path>[/yellow]  Specify the file or folder path", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return
        
    if resolved in ("useradd", "adduser"):
        if not subcmd:
            console.print(Panel("[yellow]<username>[/yellow]  Specify the name of the new user", title=title, border_style="cyan", expand=False))
        elif len(words) == 2:
            console.print(Panel("[yellow][password][/yellow]  Specify the password (optional)", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return
        
    if resolved in ("userdel", "deluser"):
        if not subcmd:
            console.print(Panel("[yellow]<username>[/yellow]  Specify the user account to delete", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return
        
    if resolved == "passwd":
        if not subcmd:
            console.print(Panel("[yellow][username][/yellow]  Specify the user account (defaults to current user)", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return
        
    if resolved in ("groupadd", "addgroup"):
        if not subcmd:
            console.print(Panel("[yellow]<groupname>[/yellow]  Specify the name of the new group", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return
        
    if resolved in ("groupdel", "delgroup"):
        if not subcmd:
            console.print(Panel("[yellow]<groupname>[/yellow]  Specify the group to delete", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    # --- Service control commands ---
    if resolved in ("status", "start", "stop", "restart", "logs", "live"):
        if not subcmd:
            console.print(Panel(f"[yellow]<service>[/yellow]  Specify the name of the service to {resolved}", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    # --- Power commands ---
    if resolved in ("reboot", "shutdown"):
        if not subcmd:
            console.print(Panel("[yellow][time][/yellow]  Specify time delay/target (e.g. '10' for 10 minutes, '23:30', or 'now') (optional)", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    # --- Simple zero-arg commands ---
    if resolved in ("cancel", "sysinfo", "processes", "containers", "back", "alias", "ip", "update"):
        console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    # --- Path/directory commands ---
    if resolved in ("list", "cd", "open"):
        if not subcmd:
            console.print(Panel(f"[yellow][path][/yellow]  Specify the target path to {resolved if resolved != 'list' else 'list directory contents'} (optional)", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    # --- File operations ---
    if resolved in ("copy", "move"):
        if not subcmd:
            console.print(Panel("[yellow]<source>[/yellow]       Specify the file or folder to copy/move", title=title, border_style="cyan", expand=False))
        elif len(words) == 2:
            console.print(Panel("[yellow]<destination>[/yellow]  Specify the target destination path", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    if resolved in ("remove", "mkdir"):
        if not subcmd:
            console.print(Panel(f"[yellow]<path>[/yellow]  Specify the file or folder to {resolved if resolved == 'remove' else 'create'}", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    # --- SQL / databases ---
    if resolved == "sqlite":
        if not subcmd:
            console.print(Panel("[yellow]<database_path>[/yellow]  Specify the path to the SQLite database file", title=title, border_style="cyan", expand=False))
        elif len(words) == 2:
            console.print(Panel("[yellow][SQL query][/yellow]      Specify the SQL query to run against the database (optional)", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    # --- Configuration ---
    if resolved == "config":
        if not subcmd:
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_row("[green]set <setting> <value>[/green]", "Change a configuration setting")
            table.add_row("[green]reset[/green]", "Reset all configurations to defaults")
            console.print(Panel(table, title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    # --- Git commands ---
    if resolved == "git":
        if not subcmd:
            console.print(Panel("[yellow]<subcommand>[/yellow]  Specify the Git action (e.g. status, log, diff, branch, add, commit, push, pull)", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("Specify optional sub-arguments or options for the Git subcommand.", title=title, border_style="cyan", expand=False))
        return

    if resolved == "gitsave":
        if not subcmd:
            console.print(Panel("[yellow]<message>[/yellow]  Specify the commit message for the changes", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    # --- Package management ---
    if resolved in ("install", "uninstall"):
        if not subcmd:
            console.print(Panel(f"[yellow]<package>[/yellow]  Specify the package name to {resolved}", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    # --- Port and Network ---
    if resolved == "port":
        if not subcmd:
            console.print(Panel("[yellow]<port_number | program_name>[/yellow]  Specify a port number or program name to lookup", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    if resolved == "netconfig":
        if not subcmd:
            console.print(Panel("[yellow]<adapter>[/yellow]  Specify the network interface/adapter to configure", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    # --- Structured universal commands ---
    if resolved == "service":
        if not subcmd:
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_row("[green]status[/green]", "Check service status")
            table.add_row("[green]start[/green]", "Start a service")
            table.add_row("[green]stop[/green]", "Stop a service")
            table.add_row("[green]restart[/green]", "Restart a service")
            table.add_row("[green]logs[/green]", "View recent service logs")
            table.add_row("[green]enable[/green]", "Enable service auto-start on boot")
            table.add_row("[green]disable[/green]", "Disable service auto-start on boot")
            console.print(Panel(table, title=title, border_style="cyan", expand=False))
        elif len(rest) == 0:
            console.print(Panel(f"[yellow]<service_name>[/yellow]  Specify the target service to {subcmd}", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    if resolved == "firewall":
        if not subcmd:
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_row("[green]status[/green]", "Check firewall rules and profile status")
            table.add_row("[green]allow <port>[/green]", "Allow incoming traffic on port")
            table.add_row("[green]block <port>[/green]", "Block incoming traffic on port")
            console.print(Panel(table, title=title, border_style="cyan", expand=False))
        elif subcmd in ("allow", "block") and len(rest) == 0:
            console.print(Panel("[yellow]<port>[/yellow]  Specify the port number to allow/block", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    if resolved in ("disk", "drive"):
        if not subcmd:
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_row("[green]list[/green]", "List physical disks, drives, and volume partitions")
            table.add_row("[green]usage [path][/green]", "Show disk space usage for path or system")
            table.add_row("[green]mount <dev> <target>[/green]", "Mount a drive or partition")
            table.add_row("[green]unmount <target>[/green]", "Unmount a mounted drive volume")
            table.add_row("[green]check <target>[/green]", "Perform filesystem integrity check (fsck/chkdsk)")
            console.print(Panel(table, title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("Specify required device or path arguments.", title=title, border_style="cyan", expand=False))
        return

    if resolved in ("archive", "compress"):
        if not subcmd:
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_row("[green]create <out.zip> <path>[/green]", "Compress file/folder into archive")
            table.add_row("[green]extract <archive> [dest][/green]", "Extract compressed archive into folder")
            console.print(Panel(table, title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("Specify archive path and target directory.", title=title, border_style="cyan", expand=False))
        return

    if resolved == "download":
        if not subcmd:
            console.print(Panel("[yellow]<url>[/yellow]  Specify the download URL (HTTP/HTTPS/FTP)", title=title, border_style="cyan", expand=False))
        elif len(words) == 2:
            console.print(Panel("[yellow][destination][/yellow]  Specify optional destination file path or folder", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    if resolved == "search":
        if not subcmd:
            console.print(Panel("[yellow]<query_text>[/yellow]  Specify text or regex pattern to search for in files", title=title, border_style="cyan", expand=False))
        elif len(words) == 2:
            console.print(Panel("[yellow][path][/yellow]  Specify optional directory path to search inside", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    if resolved == "killport":
        if not subcmd:
            console.print(Panel("[yellow]<port_number>[/yellow]  Specify port number to terminate associated process", title=title, border_style="cyan", expand=False))
        else:
            console.print(Panel("No further arguments expected.", title=title, border_style="cyan", expand=False))
        return

    show_command_help(resolved, commands)


def execute_line(
    line: str,
    shell: str,
    aliases: dict,
    all_names: list[str],
    commands: dict,
    manager: str | None,
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
            for name in sorted(all_names):
                desc = commands[name].desc if name in commands else BUILTIN_DESCRIPTIONS.get(name, "")
                console.print(f"  [green]{name:15}[/green] : {desc}")
            console.print()
            return True

        words = query.split()
        if len(words) == 1:
            candidate = words[0].lower()
            resolved, matches = resolve_command(all_names, candidate)
            if resolved:
                show_command_help(resolved, commands)
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

    if name == "help":
        print_help(commands, manager)
        return True
    if name == "clear":
        console.clear()
        return True
    if name in ("exit", "quit"):
        return False

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

    if name == "alias":
        shell_name = os.path.basename(shell)
        rc = shell_rc_file(shell)
        try:
            console.print(f"\n[bold cyan]Alias Wizard[/bold cyan]  (shell: [green]{shell_name}[/green]  •  rc: [dim]{rc}[/dim])")
            alias_name = prompt(
                [("class:prompt", "Alias name (shortcut): ")],
                completer=DummyCompleter(), style=PROMPT_STYLE
            ).strip()
            if not alias_name:
                console.print("[red]Alias name cannot be empty. Aborting.[/red]")
                return True
            if " " in alias_name:
                console.print("[red]Alias name must not contain spaces. Aborting.[/red]")
                return True
            alias_val = prompt(
                [("class:prompt", f"Command for '{alias_name}': ")],
                completer=DummyCompleter(), style=PROMPT_STYLE
            ).strip()
            if not alias_val:
                console.print("[red]Command cannot be empty. Aborting.[/red]")
                return True

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
            console.print("[yellow]Usage: remove <path>[/yellow]")
            return True
        target = os.path.expanduser(rest[0])
        try:
            if os.path.isdir(target):
                if os.listdir(target):  # non-empty dir — ask first
                    ans = prompt(
                        [("class:prompt", f"Remove '{target}' and all its contents? [y/N]: ")],
                        style=PROMPT_STYLE,
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

    if name == "mkdir":
        if not rest:
            console.print("[yellow]Usage: mkdir <path>[/yellow]")
            return True
        try:
            os.makedirs(os.path.expanduser(rest[0]), exist_ok=True)
            console.print(f"[green]Created[/green] {rest[0]}")
        except Exception as e:
            console.print(f"[red]mkdir: {e}[/red]")
        return True

    if name == "netconfig":
        if not rest:
            console.print("[yellow]Please specify an adapter, e.g., 'netconfig eth0'[/yellow]")
            return True
        if IS_WINDOWS:
            configure_windows_network(rest[0], PROMPT_STYLE)
            return True
        if IS_MACOS:
            configure_macos_network(rest[0], PROMPT_STYLE)
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
            mode = prompt([("class:prompt", "Action [dhcp/static/up/down]: ")], completer=mode_completer, style=PROMPT_STYLE).strip().lower()
            
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
                ip_addr = prompt([("class:prompt", "IP Address with subnet (e.g. 192.168.1.50/24): ")], completer=empty, style=PROMPT_STYLE).strip()
                gw = prompt([("class:prompt", "Gateway (e.g. 192.168.1.1): ")], completer=empty, style=PROMPT_STYLE).strip()
                dns = prompt([("class:prompt", "DNS (e.g. 8.8.8.8): ")], completer=empty, style=PROMPT_STYLE).strip()
                
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

    arg = " ".join(rest) if rest else ""
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
        proc = subprocess.run(real_cmd, capture_output=True, text=True, timeout=15)
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
    completer = LazyCompleter(commands)

    def bottom_toolbar():
        return HTML(" <b>Tab</b> complete  •  <b>Enter</b> run  •  <b>Ctrl-D</b> quit  •  type 'help'")

    session = PromptSession(
        history=InMemoryHistory(),
        completer=completer,
        complete_while_typing=True,
        style=PROMPT_STYLE,
        bottom_toolbar=bottom_toolbar,
    )

    distro = read_os_pretty_name()
    mgr_label = MANAGER_DISPLAY_NAME.get(manager, "none detected")
    console.print(Panel.fit(
        Text("hopit-cli", style="bold green") + Text(f"  —  {distro}  •  package manager: {mgr_label}"),
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
                ("class:hopit", " hopit-cli "),
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
                ("class:time_sep", sep),
                ("", " "),
            ])
            
            try:
                line = session.prompt(prompt_fragments).strip()
            except EOFError:
                break
            except KeyboardInterrupt:
                continue

            if not line:
                continue

            try:
                keep_running = execute_line(line, shell, aliases, all_names, commands, manager)
                if not keep_running:
                    break
            except KeyboardInterrupt:
                console.print("\n[dim]stopped.[/dim]")
                continue

        except KeyboardInterrupt:
            # Outer fallback catch-all for the prompt generation code
            continue

    console.print("[dim]bye[/dim]")
