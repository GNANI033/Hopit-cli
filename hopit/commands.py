import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from hopit.config import IS_WINDOWS, IS_MACOS
from hopit.kubernetes import k8s_cmd, load_pods, load_namespaces, load_deployments, load_services, load_nodes, load_contexts, kubectl_available

# Wrap shlex.split to support Windows paths by default
_orig_split = shlex.split
def _custom_split(s, *args, **kwargs):
    if "posix" not in kwargs and len(args) < 2:
        kwargs["posix"] = not IS_WINDOWS
    return _orig_split(s, *args, **kwargs)
shlex.split = _custom_split

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
    "exit": "Leave hopit-cli, or deactivate environment: exit [venv]",
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
        safe_svc = shlex.quote(svc)
        return ["bash", "-c",
                f"sudo launchctl unload /Library/LaunchDaemons/{safe_svc}.plist 2>/dev/null; "
                f"sudo launchctl load /Library/LaunchDaemons/{safe_svc}.plist"]
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


def system_enable_cmd(svc: str) -> list[str]:
    if IS_WINDOWS:
        return ps_command(f"Set-Service -Name {ps_quote(svc)} -StartupType Automatic")
    if IS_MACOS:
        return ["sudo", "launchctl", "load", "-w", f"/Library/LaunchDaemons/{svc}.plist"]
    return ["systemctl", "enable", svc]


def system_disable_cmd(svc: str) -> list[str]:
    if IS_WINDOWS:
        return ps_command(f"Set-Service -Name {ps_quote(svc)} -StartupType Disabled")
    if IS_MACOS:
        return ["sudo", "launchctl", "unload", "-w", f"/Library/LaunchDaemons/{svc}.plist"]
    return ["systemctl", "disable", svc]


def firewall_cmd(arg: str) -> list[str]:
    return [sys.executable, "-m", "hopit.firewall"] + (shlex.split(arg) if arg else [])


def disk_cmd(arg: str) -> list[str]:
    args = shlex.split(arg)
    if not args:
        return []
    sub = args[0].lower()
    rest = args[1:]
    target = rest[0] if rest else ""
    
    if sub == "list":
        if IS_WINDOWS:
            return ps_command("Get-Volume | Select-Object DriveLetter, FileSystemLabel, FileSystem, SizeRemaining, Size | Format-Table -AutoSize")
        if IS_MACOS:
            return ["diskutil", "list"]
        if shutil.which("lsblk"):
            return ["lsblk", "-o", "NAME,FSTYPE,SIZE,MOUNTPOINT,MODEL"]
        return ["df", "-h"]
    elif sub == "usage":
        if IS_WINDOWS:
            return ps_command("Get-PSDrive -PSProvider FileSystem | Format-Table -AutoSize")
        return ["df", "-h"] + ([target] if target else [])
    elif sub == "mount":
        if len(rest) >= 2:
            dev, mnt = rest[0], rest[1]
            if IS_WINDOWS:
                return ["mountvol", mnt, dev]
            if IS_MACOS:
                return ["diskutil", "mount", dev]
            return ["mount", dev, mnt]
        return []
    elif sub == "unmount":
        if not target:
            return []
        if IS_WINDOWS:
            return ["mountvol", target, "/d"]
        if IS_MACOS:
            return ["diskutil", "unmount", target]
        return ["umount", target]
    elif sub == "check":
        if not target:
            return []
        if IS_WINDOWS:
            return ["chkdsk", target]
        if IS_MACOS:
            return ["fsck_apfs", target]
        if shutil.which("fsck"):
            return ["fsck", "-y", target]
        if shutil.which("e2fsck"):
            return ["e2fsck", "-p", target]
        return ["fsck", target]
    elif sub == "health":
        if IS_WINDOWS:
            return ps_command("Get-PhysicalDisk | Select-Object DeviceId, MediaType, OperationalStatus, HealthStatus | Format-Table -AutoSize")
        if not target:
            return []
        if IS_MACOS:
            return ["diskutil", "info", target]
        if shutil.which("smartctl"):
            return ["smartctl", "-H", target]
        return ["echo", "smartctl is required on Linux for disk health checks. Please install smartmontools."]
    elif sub == "format":
        if len(rest) >= 2:
            dev, fs = rest[0], rest[1]
            if IS_WINDOWS:
                return ["format", dev, f"/FS:{fs.upper()}", "/Q", "/Y"]
            if IS_MACOS:
                return ["diskutil", "eraseVolume", fs, "Untitled", dev]
            return [f"mkfs.{fs}", dev]
        return []
    return []


def archive_cmd(arg: str) -> list[str]:
    return [sys.executable, "-m", "hopit.archive"] + (shlex.split(arg) if arg else [])


def download_cmd(arg: str) -> list[str]:
    return [sys.executable, "-m", "hopit.download"] + (shlex.split(arg) if arg else [])


def search_cmd(arg: str) -> list[str]:
    return [sys.executable, "-m", "hopit.search"] + (shlex.split(arg) if arg else [])


def killport_cmd(arg: str) -> list[str]:
    return [sys.executable, "-m", "hopit.killport"] + (shlex.split(arg) if arg else [])


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
        "enable": Command(
            run=system_enable_cmd,
            desc="Enable a service to start automatically on boot",
            needs_sudo=True,
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "disable": Command(
            run=system_disable_cmd,
            desc="Disable a service from starting automatically on boot",
            needs_sudo=True,
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
        "netconfig": Command(
            run=lambda adapter: [],  # handled specially in main loop
            desc="Interactively configure DHCP/Static IP for an adapter, reset, or release/renew DHCP",
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
        "create": Command(
            run=lambda _: [],  # handled specially in main loop
            desc="Create a new folder, file, or virtual environment: create [folder|file|venv] <path>",
            needs_arg=True,
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "enter": Command(
            run=lambda _: [],  # handled specially in main loop
            desc="Enter (activate) a context or virtual environment: enter venv <path>",
            needs_arg=False,
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
            run=lambda arg: [sys.executable, "-m", "hopit.processes"] + (shlex.split(arg) if arg else []),
            desc="List running processes on the system: processes [cpu|mem|name|pid]",
            needs_arg=False,
            mode="capture",
        ),
        "ps": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.processes"] + (shlex.split(arg) if arg else []),
            desc="List running processes: ps [cpu|mem|name|pid]",
            needs_arg=False,
            mode="capture",
        ),
        "process": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.process"] + (shlex.split(arg) if arg else []),
            desc="Inspect a specific process by PID or name: process <pid_or_name>",
            needs_arg=True,
            mode="capture",
        ),
        "kill": Command(
            run=lambda arg: (["taskkill", "/PID", arg, "/F"] if IS_WINDOWS else ["kill", "-9", arg]) if arg.isdigit() else (["taskkill", "/IM", arg, "/F"] if IS_WINDOWS else ["killall", "-9", arg]),
            desc="Terminate a process by PID or name: kill <PID_or_name>",
            needs_arg=True,
            needs_sudo=True,
            mode="stream",
        ),
        "pkill": Command(
            run=lambda arg: ["taskkill", "/IM", arg, "/F"] if IS_WINDOWS else ["pkill", "-f", arg],
            desc="Terminate processes by name pattern: pkill <name>",
            needs_arg=True,
            needs_sudo=True,
            mode="stream",
        ),
        "top": Command(
            run=lambda _: [sys.executable, "-m", "hopit.top"],
            desc="Display top processes dynamically in real-time (Ctrl-C to exit)",
            needs_arg=False,
            mode="stream",
        ),
        "resources": Command(
            run=lambda _: [sys.executable, "-m", "hopit.resources"],
            desc="Display system resource dashboard (CPU, Memory, Disk, Network)",
            needs_arg=False,
            mode="capture",
        ),
        "ping": Command(
            run=lambda arg: ["ping"] + shlex.split(arg) if arg else [],
            desc="Ping a remote host to check network connectivity: ping <host>",
            needs_arg=True,
            mode="stream",
        ),
        "traceroute": Command(
            run=lambda arg: (["tracert"] + shlex.split(arg)) if IS_WINDOWS else (["traceroute"] + shlex.split(arg)),
            desc="Trace the route packets take to a host: traceroute <host>",
            needs_arg=True,
            mode="stream",
        ),
        "dns": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.dns"] + (shlex.split(arg) if arg else []),
            desc="Perform DNS resolution lookup for a host: dns <host>",
            needs_arg=True,
            mode="stream",
        ),
        "lookup": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.lookup"] + (shlex.split(arg) if arg else []),
            desc="Perform consolidated diagnostics (DNS, Ping, HTTP, Traceroute): lookup <host_or_ip>",
            needs_arg=True,
            mode="stream",
        ),
        "nslookup": Command(
            run=lambda arg: ["nslookup"] + shlex.split(arg) if arg else [],
            desc="Query Internet name servers: nslookup <host>",
            needs_arg=True,
            mode="stream",
        ),
        "route": Command(
            run=lambda arg: (["route", "print"] + shlex.split(arg)) if IS_WINDOWS else ((["netstat", "-rn"] + shlex.split(arg)) if IS_MACOS else (["ip", "route"] + shlex.split(arg))),
            desc="View or configure the system network routing table: route [args]",
            needs_arg=False,
            mode="capture",
        ),
        "arp": Command(
            run=lambda arg: (["arp", "-a"] + shlex.split(arg)) if IS_WINDOWS else ((["arp", "-an"] + shlex.split(arg)) if IS_MACOS else (["ip", "neigh"] + shlex.split(arg))),
            desc="View and manage the system Address Resolution Protocol (ARP) table: arp [args]",
            needs_arg=False,
            mode="capture",
        ),
        "netstat": Command(
            run=lambda arg: ["netstat"] + shlex.split(arg) if arg else (["netstat", "-ano"] if IS_WINDOWS else ["netstat", "-an"]),
            desc="Display network connections and protocol statistics: netstat [args]",
            needs_arg=False,
            mode="stream",
        ),
        "connections": Command(
            run=lambda _: [sys.executable, "-m", "hopit.connections"],
            desc="Display active network connections in a beautiful table",
            needs_arg=False,
            mode="capture",
        ),
        "hostname": Command(
            run=lambda arg: (["powershell", "-Command", f"Rename-Computer -NewName '{arg}'"] if IS_WINDOWS else ["hostname", arg]) if arg else ["hostname"],
            desc="Show or change the system hostname: hostname [new_name]",
            needs_arg=False,
            mode="capture",
        ),
        "gateway": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.gateway"] + shlex.split(arg),
            desc="Display system default gateway IP address",
            needs_arg=False,
            mode="capture",
        ),
        "mac": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.mac"] + shlex.split(arg),
            desc="Display network MAC addresses",
            needs_arg=False,
            mode="capture",
        ),
        "curl": Command(
            run=lambda arg: ["curl"] + shlex.split(arg),
            desc="Transfer data from or to a server: curl <url> [args]",
            needs_arg=True,
            mode="stream",
        ),
        "wget": Command(
            run=lambda arg: (["curl", "-L", "-O"] + shlex.split(arg)) if IS_WINDOWS else (["wget"] + shlex.split(arg)),
            desc="Download a file from a URL using wget or curl: wget <url> [args]",
            needs_arg=True,
            mode="stream",
        ),
        "ssh": Command(
            run=lambda arg: ["ssh"] + shlex.split(arg),
            desc="OpenSSH SSH client (remote login): ssh <user@host>",
            needs_arg=True,
            mode="stream",
        ),
        "scp": Command(
            run=lambda arg: ["scp"] + shlex.split(arg),
            desc="Secure copy files: scp <src> <dest>",
            needs_arg=True,
            mode="stream",
        ),
        "sftp": Command(
            run=lambda arg: ["sftp"] + shlex.split(arg),
            desc="Secure file transfer program: sftp <user@host>",
            needs_arg=True,
            mode="stream",
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
        "firewall": Command(
            run=firewall_cmd,
            desc="Manage network firewall rules & active status (interactive or single-line)",
            needs_arg=False,
            needs_sudo=True,
            mode="stream",
        ),
        "disk": Command(
            run=disk_cmd,
            desc="Manage storage disks and drives: disk [list|usage|mount|unmount|check]",
            needs_arg=True,
            mode="capture",
        ),
        "drive": Command(
            run=disk_cmd,
            desc="Manage storage disks and drives: drive [list|usage|mount|unmount|check]",
            needs_arg=True,
            mode="capture",
        ),
        "archive": Command(
            run=archive_cmd,
            desc="Create or extract compressed archives: archive [create|extract] [args]",
            needs_arg=True,
            mode="capture",
        ),
        "compress": Command(
            run=archive_cmd,
            desc="Create or extract compressed archives: compress [create|extract] [args]",
            needs_arg=True,
            mode="capture",
        ),
        "download": Command(
            run=download_cmd,
            desc="Download a file from a URL with progress: download <url> [destination]",
            needs_arg=True,
            mode="stream",
        ),
        "search": Command(
            run=search_cmd,
            desc="Search text inside files or search filenames: search <query> [path]",
            needs_arg=True,
            mode="capture",
        ),
        "killport": Command(
            run=killport_cmd,
            desc="Terminate any process listening on a specific port: killport <port>",
            needs_arg=True,
            needs_sudo=True,
            mode="stream",
        ),
        "pwd": Command(
            run=lambda _: [],
            desc="Print the current working directory path",
            needs_arg=False,
        ),
        "whereami": Command(
            run=lambda _: [],
            desc="Print the current working directory path (whereami)",
            needs_arg=False,
        ),
        "history": Command(
            run=lambda _: [],
            desc="Show the session command history",
            needs_arg=False,
        ),
        "env": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.env"] + (shlex.split(arg) if arg else []),
            desc="View or filter environment variables: env [filter]",
            needs_arg=False,
            mode="capture",
        ),
        "show": Command(
            run=lambda _: [],
            desc="Show details (file, start, end, tree, env, history): show <subcommand> [args]",
            needs_arg=True,
        ),
        "which": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.which"] + (shlex.split(arg) if arg else []),
            desc="Locate an executable in the system PATH: which <command>",
            needs_arg=True,
            mode="capture",
        ),
        "where": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.which"] + (shlex.split(arg) if arg else []),
            desc="Locate an executable in the system PATH: where <command>",
            needs_arg=True,
            mode="capture",
        ),
        "findcommand": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.which"] + (shlex.split(arg) if arg else []),
            desc="Locate an executable in the system PATH: findcommand <command>",
            needs_arg=True,
            mode="capture",
        ),
        "touch": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.touch"] + (shlex.split(arg) if arg else []),
            desc="Create an empty file or update timestamps: touch <file_path>",
            needs_arg=True,
            mode="capture",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "cat": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.cat"] + (shlex.split(arg) if arg else []),
            desc="Show contents of files: cat <file_path>",
            needs_arg=True,
            mode="capture",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "head": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.head"] + (shlex.split(arg) if arg else []),
            desc="Show first N lines of a file: head [-n lines] <file_path>",
            needs_arg=True,
            mode="capture",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "viewstart": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.head"] + (shlex.split(arg) if arg else []),
            desc="Show first N lines of a file: viewstart [-n lines] <file_path>",
            needs_arg=True,
            mode="capture",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "tail": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.tail"] + (shlex.split(arg) if arg else []),
            desc="Show last N lines of a file: tail [-n lines] <file_path>",
            needs_arg=True,
            mode="capture",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "viewend": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.tail"] + (shlex.split(arg) if arg else []),
            desc="Show last N lines of a file: viewend [-n lines] <file_path>",
            needs_arg=True,
            mode="capture",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "less": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.less"] + (shlex.split(arg) if arg else []),
            desc="Page through file content interactively: less <file_path>",
            needs_arg=True,
            mode="stream",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "scrollfile": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.less"] + (shlex.split(arg) if arg else []),
            desc="Page through file content interactively: scrollfile <file_path>",
            needs_arg=True,
            mode="stream",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "tree": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.tree"] + (shlex.split(arg) if arg else []),
            desc="Show directory structure in a tree: tree [path] [depth]",
            needs_arg=False,
            mode="capture",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "find": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.find"] + (shlex.split(arg) if arg else []),
            desc="Search files by name pattern: find <pattern> [path]",
            needs_arg=True,
            mode="capture",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "findfile": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.find"] + (shlex.split(arg) if arg else []),
            desc="Search files by name pattern: findfile <pattern> [path]",
            needs_arg=True,
            mode="capture",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "grep": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.grep"] + (shlex.split(arg) if arg else []),
            desc="Search text pattern inside files: grep <pattern> [path]",
            needs_arg=True,
            mode="capture",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        "findtext": Command(
            run=lambda arg: [sys.executable, "-m", "hopit.grep"] + (shlex.split(arg) if arg else []),
            desc="Search text pattern inside files: findtext <pattern> [path]",
            needs_arg=True,
            mode="capture",
            arg_completions=names["path"],
            arg_completion_kind="path",
        ),
        # ── Kubernetes ──────────────────────────────────────────────────────
        "k8s": Command(
            run=k8s_cmd,
            desc="Kubernetes: simple-English or raw kubectl — k8s [pods|logs|deploy|scale|apply|...]",
            needs_arg=False,
            mode="stream",
            arg_completions=lambda: [t for t, _ in __import__('hopit.kubernetes', fromlist=['K8S_TOP_COMPLETIONS']).K8S_TOP_COMPLETIONS],
            arg_completion_kind="k8s_subcommand",
        ),
        "kubernetes": Command(
            run=k8s_cmd,
            desc="Kubernetes manager (alias for k8s): kubernetes [pods|deploy|logs|...]",
            needs_arg=False,
            mode="stream",
            arg_completion_kind="k8s_subcommand",
        ),
        "kubectl": Command(
            run=lambda arg: ["kubectl"] + (shlex.split(arg) if arg else ["help"]),
            desc="Raw kubectl pass-through: kubectl <subcommand> [args...]",
            needs_arg=False,
            mode="stream",
            arg_completions=lambda: [t for t, _ in __import__('hopit.kubernetes', fromlist=['KUBECTL_SUBCOMMANDS']).KUBECTL_SUBCOMMANDS],
            arg_completion_kind="kubectl_subcommand",
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
