import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from hopit.config import IS_WINDOWS, IS_MACOS
from hopit.loaders import MANAGER_PKG, MANAGER_DISPLAY_NAME, MANAGER_UPDATE_CMDS

@dataclass
class Command:
    run: Callable[[str], list[str]]                    # builds the real argv to execute
    desc: str                                            # shown in help / completion menu
    needs_arg: bool = True                               # whether an argument is required
    needs_sudo: bool = False                             # auto-prepend sudo if not already root
    mode: str = "capture"                                # "capture" (render nicely) or "stream" (live passthrough)
    arg_completions: Callable[[], list[str]] | None = None  # candidates for arg tab-completion
    arg_completion_kind: str | None = None             # service / installed_pkg / available_pkg


BUILTIN_DESCRIPTIONS = {
    "help": "Show this help",
    "clear": "Clear the screen",
    "exit": "Leave hopit-cli",
    "quit": "Leave hopit-cli",
}

_CLOCK_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def shell_command(line: str) -> list[str]:
    if IS_WINDOWS:
        return ["cmd", "/c", line]
    return ["bash", "-c", line]


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


def ps_command(script: str) -> list[str]:
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def system_status_cmd(svc: str) -> list[str]:
    if IS_WINDOWS:
        return ["sc", "query", svc]
    if IS_MACOS:
        if shutil.which("brew"):
            return ["brew", "services", "info", svc]
        return ["launchctl", "print", f"system/{svc}"]
    return ["systemctl", "status", svc]


def system_start_cmd(svc: str) -> list[str]:
    if IS_WINDOWS:
        return ["sc", "start", svc]
    if IS_MACOS:
        if shutil.which("brew"):
            return ["brew", "services", "start", svc]
        return ["sudo", "launchctl", "load", f"/Library/LaunchDaemons/{svc}.plist"]
    return ["systemctl", "start", svc]


def system_stop_cmd(svc: str) -> list[str]:
    if IS_WINDOWS:
        return ["sc", "stop", svc]
    if IS_MACOS:
        if shutil.which("brew"):
            return ["brew", "services", "stop", svc]
        return ["sudo", "launchctl", "unload", f"/Library/LaunchDaemons/{svc}.plist"]
    return ["systemctl", "stop", svc]


def system_restart_cmd(svc: str) -> list[str]:
    if IS_WINDOWS:
        return ps_command(f"Restart-Service -Name {ps_quote(svc)}")
    if IS_MACOS:
        if shutil.which("brew"):
            return ["brew", "services", "restart", svc]
        return ["bash", "-c",
                f"sudo launchctl unload /Library/LaunchDaemons/{svc}.plist 2>/dev/null; "
                f"sudo launchctl load /Library/LaunchDaemons/{svc}.plist"]
    return ["systemctl", "restart", svc]


def system_logs_cmd(svc: str) -> list[str]:
    if IS_WINDOWS:
        service = ps_quote(svc)
        return ps_command(
            "$svc = Get-Service -Name " + service + " -ErrorAction SilentlyContinue; "
            "if (-not $svc) { Write-Error 'Service not found'; exit 1 }; "
            "Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Service Control Manager'} "
            "-MaxEvents 50 | Where-Object { $_.Message -match [regex]::Escape($svc.DisplayName) -or $_.Message -match [regex]::Escape($svc.Name) } "
            "| Format-Table TimeCreated, Id, LevelDisplayName, Message -Wrap"
        )
    if IS_MACOS:
        return ["log", "show", "--last", "1h",
                "--predicate", f'process == "{svc}" OR subsystem == "{svc}"',
                "--info"]
    return ["journalctl", "-u", svc, "-n", "50", "--no-pager"]


def system_live_logs_cmd(svc: str) -> list[str]:
    if IS_WINDOWS:
        service = ps_quote(svc)
        return ps_command(
            "$svc = Get-Service -Name " + service + " -ErrorAction SilentlyContinue; "
            "if (-not $svc) { Write-Error 'Service not found'; exit 1 }; "
            "$last = Get-Date; "
            "while ($true) { "
            "$events = Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=$last; ProviderName='Service Control Manager'} "
            "-ErrorAction SilentlyContinue | Where-Object { $_.Message -match [regex]::Escape($svc.DisplayName) -or $_.Message -match [regex]::Escape($svc.Name) }; "
            "$events | Sort-Object TimeCreated | Format-Table TimeCreated, Id, LevelDisplayName, Message -Wrap; "
            "$last = Get-Date; Start-Sleep -Seconds 2 }"
        )
    if IS_MACOS:
        return ["log", "stream",
                "--predicate", f'process == "{svc}" OR subsystem == "{svc}"']
    return ["journalctl", "-u", svc, "-f"]


def shutdown_delay_seconds(arg: str) -> int:
    arg = (arg or "").strip()
    if not arg or arg.lower() == "now":
        return 0
    if arg.isdigit():
        return int(arg) * 60
    if _CLOCK_TIME_RE.match(arg):
        now = datetime.now()
        hour, minute = [int(part) for part in arg.split(":", 1)]
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target < now:
            target += timedelta(days=1)
        return max(0, int((target - now).total_seconds()))
    return 0


def reboot_cmd(arg: str) -> list[str]:
    if IS_WINDOWS:
        delay = shutdown_delay_seconds(arg)
        return ["shutdown", "/r", "/t", str(delay)]
    return ["shutdown", "-r", shutdown_time_arg(arg)]


def poweroff_cmd(arg: str) -> list[str]:
    if IS_WINDOWS:
        delay = shutdown_delay_seconds(arg)
        return ["shutdown", "/s", "/t", str(delay)]
    return ["shutdown", "-h", shutdown_time_arg(arg)]


def cancel_shutdown_cmd() -> list[str]:
    if IS_WINDOWS:
        return ["shutdown", "/a"]
    return ["shutdown", "-c"]


def list_cmd(arg: str) -> list[str]:
    if IS_WINDOWS:
        if arg.lower() == "all":
            return ["cmd", "/c", "dir", "/a"]
        return ["cmd", "/c", "dir", arg] if arg else ["cmd", "/c", "dir"]
    return ["ls", "-la", "--color=always"] if arg.lower() == "all" else (["ls", "--color=always", arg] if arg else ["ls", "--color=always"])


def ip_cmd() -> list[str]:
    if IS_WINDOWS:
        return ["ipconfig", "/all"]
    if IS_MACOS:
        return ["ifconfig"]
    return ["ip", "-c=always", "a"]


def port_cmd(arg: str) -> list[str]:
    if IS_WINDOWS:
        if arg.isdigit():
            script = (
                f"$port = {int(arg)}; "
                "Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue "
                "| Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort,State,OwningProcess "
                "| Format-Table -AutoSize"
            )
        else:
            pattern = ps_quote(f"*{arg}*")
            script = (
                "$procs = Get-Process | Where-Object { $_.ProcessName -like " + pattern + " }; "
                "$ids = $procs.Id; "
                "Get-NetTCPConnection -ErrorAction SilentlyContinue | Where-Object { $ids -contains $_.OwningProcess } "
                "| Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort,State,OwningProcess "
                "| Format-Table -AutoSize"
            )
        return ps_command(script)
    if IS_MACOS:
        if arg.isdigit():
            return ["lsof", "-i", f":{arg}", "-n", "-P"]
        return ["bash", "-c", f"lsof -i -n -P | grep -i {shlex.quote(arg)}"]
    return [
        "bash", "-c",
        "ss -tulnp | awk -v port=" + shlex.quote(arg) + " "
        + shlex.quote(
            'NR==1 || $0 ~ (":" port "[[:space:]]")'
            if arg.isdigit() else
            'NR==1 || tolower($0) ~ tolower(port)'
        )
    ]


def containers_cmd() -> list[str]:
    if IS_WINDOWS:
        return ps_command(
            "$found = $false; "
            "if (Get-Command docker -ErrorAction SilentlyContinue) { "
            "$found = $true; Write-Host '== DOCKER CONTAINERS =='; docker ps -a }; "
            "if (Get-Command wsl -ErrorAction SilentlyContinue) { "
            "$found = $true; Write-Host ''; Write-Host '== WSL DISTROS =='; wsl --list --verbose }; "
            "if (-not $found) { Write-Host 'No supported platform detected (docker, wsl).' }"
        )
    return [
        "bash", "-c",
        r'''
found=0
if command -v docker >/dev/null 2>&1; then
    found=1
    printf '\033[1;36m== DOCKER CONTAINERS ==\033[0m\n'
    docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' \
        | awk -F'\t' 'BEGIN{printf "\033[1;4m%-20s %-25s %-20s %s\033[0m\n","NAME","IMAGE","STATUS","PORTS"}
                      {c=($3~/^Up/)?"32":"31"; printf "\033[%sm%-20s\033[0m %-25s %-20s %s\n",c,$1,$2,$3,$4}'
    echo
fi
if command -v pct >/dev/null 2>&1; then
    found=1
    printf '\033[1;36m== PROXMOX LXC CONTAINERS ==\033[0m\n'
    pct list
    echo
fi
if command -v qm >/dev/null 2>&1; then
    found=1
    printf '\033[1;36m== PROXMOX VMs ==\033[0m\n'
    qm list
    echo
fi
if command -v vim-cmd >/dev/null 2>&1; then
    found=1
    printf '\033[1;36m== ESXI VMs ==\033[0m\n'
    vim-cmd vmsvc/getallvms
    echo
fi
if [ "$found" -eq 0 ]; then
    printf '\033[31mNo supported platform detected (docker, proxmox pct/qm, esxi vim-cmd).\033[0m\n'
fi
'''
    ]


def chmod_cmd(arg: str) -> list[str]:
    from hopit.translation import translate_chmod_to_windows
    args = shlex.split(arg)
    if IS_WINDOWS:
        win_cmd = translate_chmod_to_windows(args)
        return shlex.split(win_cmd) if win_cmd else []
    return ["chmod"] + args


def chown_cmd(arg: str) -> list[str]:
    from hopit.translation import translate_chown_to_windows
    args = shlex.split(arg)
    if IS_WINDOWS:
        win_cmd = translate_chown_to_windows(args)
        return shlex.split(win_cmd) if win_cmd else []
    return ["chown"] + args


def chgrp_cmd(arg: str) -> list[str]:
    from hopit.translation import translate_chgrp_to_windows
    args = shlex.split(arg)
    if IS_WINDOWS:
        win_cmd = translate_chgrp_to_windows(args)
        return shlex.split(win_cmd) if win_cmd else []
    return ["chgrp"] + args


def useradd_cmd(arg: str) -> list[str]:
    from hopit.translation import translate_useradd_to_windows
    args = shlex.split(arg)
    if IS_WINDOWS:
        win_cmd = translate_useradd_to_windows(args)
        return shlex.split(win_cmd) if win_cmd else []
    if IS_MACOS:
        username = next((a for a in args if not a.startswith('-')), "")
        if not username:
            return []
        password = ""
        if len(args) >= 2 and args[1] != username and not args[1].startswith('-'):
            password = args[1]
        pw_part = ["-password", password] if password else []
        return ["sysadminctl", "-addUser", username] + pw_part
    return ["useradd"] + args


def userdel_cmd(arg: str) -> list[str]:
    from hopit.translation import translate_userdel_to_windows
    args = shlex.split(arg)
    if IS_WINDOWS:
        win_cmd = translate_userdel_to_windows(args)
        return shlex.split(win_cmd) if win_cmd else []
    if IS_MACOS:
        username = next((a for a in args if not a.startswith('-')), "")
        if not username:
            return []
        return ["sysadminctl", "-deleteUser", username]
    return ["userdel"] + args


def usermod_cmd(arg: str) -> list[str]:
    from hopit.translation import translate_usermod_to_windows, translate_usermod_to_mac
    args = shlex.split(arg)
    if IS_WINDOWS:
        win_cmd = translate_usermod_to_windows(args)
        return shlex.split(win_cmd) if win_cmd else []
    if IS_MACOS:
        mac_cmd = translate_usermod_to_mac(args)
        return shlex.split(mac_cmd) if mac_cmd else []
    return ["usermod"] + args


def passwd_cmd(arg: str) -> list[str]:
    from hopit.translation import translate_passwd_to_windows
    args = shlex.split(arg)
    if IS_WINDOWS:
        win_cmd = translate_passwd_to_windows(args)
        return shlex.split(win_cmd) if win_cmd else []
    return ["passwd"] + args


def groupadd_cmd(arg: str) -> list[str]:
    from hopit.translation import translate_groupadd_to_windows
    args = shlex.split(arg)
    if IS_WINDOWS:
        win_cmd = translate_groupadd_to_windows(args)
        return shlex.split(win_cmd) if win_cmd else []
    if IS_MACOS:
        group = next((a for a in args if not a.startswith('-')), "")
        if not group:
            return []
        return ["dseditgroup", "-o", "create", group]
    return ["groupadd"] + args


def groupdel_cmd(arg: str) -> list[str]:
    from hopit.translation import translate_groupdel_to_windows
    args = shlex.split(arg)
    if IS_WINDOWS:
        win_cmd = translate_groupdel_to_windows(args)
        return shlex.split(win_cmd) if win_cmd else []
    if IS_MACOS:
        group = next((a for a in args if not a.startswith('-')), "")
        if not group:
            return []
        return ["dseditgroup", "-o", "delete", group]
    return ["groupdel"] + args


def user_cmd(arg: str) -> list[str]:
    args = shlex.split(arg)
    if not args:
        return []
    sub = args[0].lower()
    rest = args[1:]
    
    if sub == "add":
        return useradd_cmd(" ".join(rest))
    elif sub in ("remove", "delete"):
        return userdel_cmd(" ".join(rest))
    elif sub in ("passwd", "password"):
        return passwd_cmd(" ".join(rest))
    elif sub == "join":
        if len(rest) >= 2:
            return usermod_cmd(f"-aG {rest[0]} {rest[1]}")
        return []
    elif sub == "list":
        if IS_WINDOWS:
            return ["net", "user"]
        if IS_MACOS:
            return ["dscl", ".", "list", "/Users"]
        return ["cut", "-d:", "-f1", "/etc/passwd"]
    return []


def group_cmd(arg: str) -> list[str]:
    args = shlex.split(arg)
    if not args:
        return []
    sub = args[0].lower()
    rest = args[1:]
    
    if sub == "add":
        return groupadd_cmd(" ".join(rest))
    elif sub in ("remove", "delete"):
        return groupdel_cmd(" ".join(rest))
    elif sub == "list":
        if IS_WINDOWS:
            return ["net", "localgroup"]
        if IS_MACOS:
            return ["dscl", ".", "list", "/Groups"]
        return ["cut", "-d:", "-f1", "/etc/group"]
    return []


def permission_cmd(arg: str) -> list[str]:
    args = shlex.split(arg)
    if not args:
        return []
    sub = args[0].lower()
    rest = args[1:]
    
    if sub == "set":
        return chmod_cmd(" ".join(rest))
    elif sub == "owner":
        return chown_cmd(" ".join(rest))
    elif sub == "group":
        return chgrp_cmd(" ".join(rest))
    return []


def build_commands(manager: str | None, names: dict) -> dict:
    """`names` is a dict of zero-arg/one-arg callables returning current candidate
    lists: {"service": ..., "installed_pkg": ..., "available_pkg": ...}"""

    commands = {
        "status": Command(
            run=system_status_cmd,
            desc="Show the status of a service",
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "start": Command(
            run=system_start_cmd,
            desc="Start a service",
            needs_sudo=True,
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "stop": Command(
            run=system_stop_cmd,
            desc="Stop a service",
            needs_sudo=True,
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "restart": Command(
            run=system_restart_cmd,
            desc="Restart a service",
            needs_sudo=True,
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "logs": Command(
            run=system_logs_cmd,
            desc="Show recent logs for a service",
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "live": Command(
            run=system_live_logs_cmd,
            desc="Follow a service's logs live (Ctrl-C to stop)",
            mode="stream",
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "reboot": Command(
            run=reboot_cmd,
            desc="Reboot now, in N minutes, or at HH:MM ('reboot 10', 'reboot 23:30')",
            needs_arg=False,
            needs_sudo=True,
            mode="stream",
        ),
        "shutdown": Command(
            run=poweroff_cmd,
            desc="Power off now, in N minutes, or at HH:MM ('shutdown 10')",
            needs_arg=False,
            needs_sudo=True,
            mode="stream",
        ),
        "cancel": Command(
            run=lambda _: cancel_shutdown_cmd(),
            desc="Cancel a pending scheduled shutdown/reboot",
            needs_arg=False,
            needs_sudo=True,
            mode="stream",
        ),
        "list": Command(
            run=list_cmd,
            desc="List directory contents ('list all' for detailed view)",
            needs_arg=False,
            mode="stream",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "cd": Command(
            run=lambda path: [],  # handled specially in main loop
            desc="Change directory",
            needs_arg=False,
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "alias": Command(
            run=lambda _: [],  # handled specially in main loop
            desc="Add a shell alias interactively (auto-detects your shell)",
            needs_arg=False,
        ),
        "ip": Command(
            run=lambda _: ip_cmd(),
            desc="Show IP addresses and network interfaces",
            needs_arg=False,
            mode="capture",
        ),
        "netconfig": Command(
            run=lambda adapter: [],  # handled specially in main loop
            desc="Interactively configure DHCP/Static IP for an adapter",
            needs_arg=True,
            arg_completions=names["adapter"],
            arg_completion_kind="adapter",
        ),
        "port": Command(
            run=port_cmd,
            desc="Show which program is using a port (by port number or program name)",
            needs_sudo=True,
            mode="capture",
        ),
        "containers": Command(
            run=lambda _: containers_cmd(),
            desc="Auto-detect and list containers/VMs (Docker, Proxmox LXC/VM, ESXi)",
            needs_arg=False,
            needs_sudo=True,
            mode="capture",
        ),
        "open": Command(
            run=lambda path: [],  # handled specially in main loop
            desc="Open a folder (cd) or file (nano); no arg shows cwd",
            needs_arg=False,
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "back": Command(
            run=lambda _: [],  # handled specially in main loop
            desc="Go back to parent directory (cd ..)",
            needs_arg=False,
        ),
        # -- Universal file-system commands (Python shutil, same on all OSes) --
        "copy": Command(
            run=lambda _: [],  # handled specially in main loop
            desc="Copy a file or folder: copy <src> <dest>",
            needs_arg=True,
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "move": Command(
            run=lambda _: [],  # handled specially in main loop
            desc="Move or rename a file or folder: move <src> <dest>",
            needs_arg=True,
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "remove": Command(
            run=lambda _: [],  # handled specially in main loop
            desc="Delete a file or folder (confirms for non-empty dirs)",
            needs_arg=True,
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "mkdir": Command(
            run=lambda _: [],  # handled specially in main loop
            desc="Create a directory (including parents): mkdir <path>",
            needs_arg=True,
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "sysinfo": Command(
            run=lambda _: [sys.executable, "-m", "hopit.sysinfo"],
            desc="Show detailed system information (OS, CPU, Memory, Disk, Uptime)",
            needs_arg=False,
            mode="capture",
        ),
        "processes": Command(
            run=lambda _: [sys.executable, "-m", "hopit.processes"],
            desc="List running processes on the system",
            needs_arg=False,
            mode="capture",
        ),
        "sqlite": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.sqlite"] + (shlex.split(arg) if arg else []),
            desc="Query or inspect SQLite databases: sqlite <db_path> [SQL query]",
            needs_arg=True,
            mode="capture",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "config": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.config_cmd"] + (shlex.split(arg) if arg else []),
            desc="View or modify hopit-cli configuration options: config [set <setting> <value> | reset]",
            needs_arg=False,
            mode="capture",
        ),
        "git": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.git"] + (shlex.split(arg) if arg else []),
            desc="Run git commands with colorized log, branch, status, and diff rendering: git <subcommand> [args]",
            needs_arg=True,
            mode="capture",
            arg_completions=lambda: ["status", "log", "branch", "diff", "add", "commit", "push", "pull", "checkout", "clone"],
            arg_completion_kind="git_subcommand",
        ),
        "gitsave": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.gitsave"] + (shlex.split(arg) if arg else []),
            desc="Stage all changes, commit, and push in one shot: gitsave <commit message>",
            needs_arg=True,
            mode="capture",
        ),
        "chmod": Command(
            run=chmod_cmd,
            desc="Change file/folder permissions cross-platform: chmod <perms> <path>",
            needs_arg=True,
            mode="stream",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "chown": Command(
            run=chown_cmd,
            desc="Change file/folder ownership cross-platform: chown <owner> <path>",
            needs_arg=True,
            mode="stream",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "chgrp": Command(
            run=chgrp_cmd,
            desc="Change file/folder group ownership cross-platform: chgrp <group> <path>",
            needs_arg=True,
            mode="stream",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "useradd": Command(
            run=useradd_cmd,
            desc="Add a new system user cross-platform: useradd <username> [password]",
            needs_arg=True,
            needs_sudo=True,
            mode="stream",
        ),
        "adduser": Command(
            run=useradd_cmd,
            desc="Add a new system user cross-platform: adduser <username> [password]",
            needs_arg=True,
            needs_sudo=True,
            mode="stream",
        ),
        "userdel": Command(
            run=userdel_cmd,
            desc="Remove a system user cross-platform: userdel <username>",
            needs_arg=True,
            needs_sudo=True,
            mode="stream",
            arg_completions=names["user"],
            arg_completion_kind="user",
        ),
        "deluser": Command(
            run=userdel_cmd,
            desc="Remove a system user cross-platform: deluser <username>",
            needs_arg=True,
            needs_sudo=True,
            mode="stream",
            arg_completions=names["user"],
            arg_completion_kind="user",
        ),
        "usermod": Command(
            run=usermod_cmd,
            desc="Modify a system user (e.g. add to groups): usermod -aG <group> <username>",
            needs_arg=True,
            needs_sudo=True,
            mode="stream",
            arg_completions=names["user"],
            arg_completion_kind="user",
        ),
        "passwd": Command(
            run=passwd_cmd,
            desc="Change a user's password cross-platform: passwd <username>",
            needs_arg=False,
            needs_sudo=True,
            mode="stream",
            arg_completions=names["user"],
            arg_completion_kind="user",
        ),
        "groupadd": Command(
            run=groupadd_cmd,
            desc="Add a new system group cross-platform: groupadd <groupname>",
            needs_arg=True,
            needs_sudo=True,
            mode="stream",
        ),
        "addgroup": Command(
            run=groupadd_cmd,
            desc="Add a new system group cross-platform: addgroup <groupname>",
            needs_arg=True,
            needs_sudo=True,
            mode="stream",
        ),
        "groupdel": Command(
            run=groupdel_cmd,
            desc="Remove a system group cross-platform: groupdel <groupname>",
            needs_arg=True,
            needs_sudo=True,
            mode="stream",
            arg_completions=names["group"],
            arg_completion_kind="group",
        ),
        "delgroup": Command(
            run=groupdel_cmd,
            desc="Remove a system group cross-platform: delgroup <groupname>",
            needs_arg=True,
            needs_sudo=True,
            mode="stream",
            arg_completions=names["group"],
            arg_completion_kind="group",
        ),
        "user": Command(
            run=user_cmd,
            desc="Manage system user accounts: user [add|remove|passwd|join|list]",
            needs_arg=True,
            needs_sudo=True,
            mode="stream",
        ),
        "group": Command(
            run=group_cmd,
            desc="Manage system groups: group [add|remove|list]",
            needs_arg=True,
            needs_sudo=True,
            mode="stream",
        ),
        "permission": Command(
            run=permission_cmd,
            desc="Manage file and folder permissions: permission [set|owner|group]",
            needs_arg=True,
            needs_sudo=True,
            mode="stream",
        ),
        "permissions": Command(
            run=permission_cmd,
            desc="Manage file and folder permissions: permissions [set|owner|group]",
            needs_arg=True,
            needs_sudo=True,
            mode="stream",
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
        commands["uninstall"] = Command(
            run=lambda pkg: MANAGER_PKG[manager]["remove"](pkg),
            desc=f"Uninstall a package (via {MANAGER_DISPLAY_NAME[manager]})",
            needs_sudo=True,
            mode="stream",
            arg_completions=names["installed_pkg"],
            arg_completion_kind="installed_pkg",
        )
        commands["update"] = Command(
            run=lambda _: shell_command(MANAGER_UPDATE_CMDS[manager]),
            desc=f"Update the system (via {MANAGER_DISPLAY_NAME[manager]})",
            needs_arg=False,
            needs_sudo=True,
            mode="stream",
        )

    return commands
