import os
import re
import shutil
import subprocess
import threading
from typing import Callable
from hopit.config import IS_WINDOWS, IS_MACOS

# Distro / package-manager detection & mapping constants
MANAGER_DISPLAY_NAME = {
    "apt-get": "apt", "dnf": "dnf", "yum": "yum",
    "pacman": "pacman", "zypper": "zypper", "apk": "apk",
    "brew": "brew",
    "winget": "winget", "choco": "choco", "scoop": "scoop",
}

MANAGER_UPDATE_CMDS = {
    "apt-get": "apt-get update && apt-get upgrade -y",
    "dnf":     "dnf upgrade -y",
    "yum":     "yum update -y",
    "pacman":  "pacman -Syu --noconfirm",
    "zypper":  "zypper --non-interactive update",
    "apk":     "apk update && apk upgrade",
    "brew":    "brew update && brew upgrade",
    "winget":  "winget upgrade --all --accept-package-agreements --accept-source-agreements",
    "choco":   "choco upgrade all -y",
    "scoop":   "scoop update *",
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
        "available_cmd": ["dnf", "-q", "repoquery", "--available", "--cacheonly", "--qf", "%{NAME}\n"],
        "available_parse": None,
    },
    "yum": {
        "install": lambda pkg: ["yum", "install", "-y", pkg],
        "remove": lambda pkg: ["yum", "remove", "-y", pkg],
        "installed_cmd": ["rpm", "-qa", "--qf", "%{NAME}\n"],
        "available_cmd": ["yum", "-q", "list", "available", "--cacheonly"],
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
        "available_cmd": None,  # no fast, reliable generic listing -- skip completion
        "available_parse": None,
    },
    "apk": {
        "install": lambda pkg: ["apk", "add", pkg],
        "remove": lambda pkg: ["apk", "del", pkg],
        "installed_cmd": ["apk", "info"],
        "available_cmd": ["apk", "search", "-q"],
        "available_parse": None,
    },
    "brew": {
        "install": lambda pkg: ["brew", "install", pkg],
        "remove":  lambda pkg: ["brew", "uninstall", pkg],
        "installed_cmd": ["brew", "list", "--formula"],
        "available_cmd": None,   # brew search is slow; skip background load
        "available_parse": None,
    },
    "winget": {
        "install": lambda pkg: ["winget", "install", "--id", pkg, "--accept-package-agreements", "--accept-source-agreements"],
        "remove": lambda pkg: ["winget", "uninstall", "--id", pkg],
        "installed_cmd": ["winget", "list", "--accept-source-agreements"],
        # available_cmd uses a prefix-aware search -- see load_available_packages() for the winget branch
        "available_cmd": "winget_search",
        "installed_parse": "winget",
        "available_parse": "winget",
    },
    "choco": {
        "install": lambda pkg: ["choco", "install", pkg, "-y"],
        "remove": lambda pkg: ["choco", "uninstall", pkg, "-y"],
        "installed_cmd": ["choco", "list", "--local-only", "--limit-output"],
        "available_cmd": None,
        "installed_parse": "choco",
        "available_parse": None,
    },
    "scoop": {
        "install": lambda pkg: ["scoop", "install", pkg],
        "remove": lambda pkg: ["scoop", "uninstall", pkg],
        "installed_cmd": ["scoop", "list"],
        "available_cmd": None,
        "installed_parse": "scoop",
        "available_parse": None,
    },
}


def _run_lines(argv: list[str], timeout: int) -> list[str]:
    try:
        out = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, errors="ignore", timeout=timeout
        )
        return out.stdout.splitlines()
    except Exception:
        return []


def load_service_names() -> list[str]:
    if IS_WINDOWS:
        lines = _run_lines(
            ["powershell", "-NoProfile", "-Command", "Get-Service | Select-Object -ExpandProperty Name"],
            timeout=5,
        )
        return sorted(set(l.strip() for l in lines if l.strip()))

    if IS_MACOS:
        names: list[str] = []
        # brew services (most common for dev tools)
        if shutil.which("brew"):
            lines = _run_lines(["brew", "services", "list"], timeout=5)
            for line in lines[1:]:   # skip header
                parts = line.split()
                if parts:
                    names.append(parts[0])
        # user launchd agents
        lctl = _run_lines(["launchctl", "list"], timeout=3)
        for line in lctl[1:]:
            parts = line.split()
            if len(parts) >= 3 and parts[2] != "-":
                names.append(parts[2])
        return sorted(set(names))

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


def _parse_winget_installed(lines: list[str]) -> list[str]:
    """Parse 'winget list' output -- extract the package ID column (col 1)."""
    names = []
    header_found = False
    for line in lines:
        line = line.rstrip()
        if not line:
            continue
        # Skip until we hit the dashed separator line (---...---)
        if re.match(r"^[-\s]+$", line):
            header_found = True
            continue
        if not header_found:
            continue
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) >= 2:
            names.append(parts[1])  # ID column
    return names


def _parse_winget_available(lines: list[str]) -> list[str]:
    """Parse 'winget search <prefix>' output -- extract the package ID column."""
    names = []
    header_found = False
    for line in lines:
        line = line.rstrip()
        if not line:
            continue
        if re.match(r"^[-\s]+$", line):
            header_found = True
            continue
        if not header_found:
            continue
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) >= 2:
            names.append(parts[1])  # ID column
    return names


def _parse_choco_installed(lines: list[str]) -> list[str]:
    names = []
    for line in lines:
        line = line.strip()
        if "|" in line:
            names.append(line.split("|", 1)[0])
    return names


def _parse_scoop_installed(lines: list[str]) -> list[str]:
    names = []
    for line in lines:
        line = line.strip()
        if not line or line.lower().startswith(("installed", "name ")):
            continue
        names.append(line.split()[0])
    return names


def load_installed_packages(manager: str | None) -> list[str]:
    if not manager:
        return []
    cfg = MANAGER_PKG[manager]
    cmd = cfg["installed_cmd"]
    lines = _run_lines(cmd, timeout=5)
    parser = cfg.get("installed_parse")
    if parser == "winget":
        return sorted(set(_parse_winget_installed(lines)))
    if parser == "choco":
        return sorted(set(_parse_choco_installed(lines)))
    if parser == "scoop":
        return sorted(set(_parse_scoop_installed(lines)))
    return sorted(set(l.strip() for l in lines if l.strip()))


def _parse_yum_available(lines: list[str]) -> list[str]:
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


def load_available_packages(manager: str | None, prefix: str = "") -> list[str]:
    """Load available packages for tab-completion.

    For winget, we run 'winget search <prefix>' on demand (fast, bounded results).
    For other managers we run the full listing command once at startup.
    """
    if not manager:
        return []
    cfg = MANAGER_PKG[manager]
    cmd = cfg.get("available_cmd")
    if not cmd:
        return []

    # winget: do a live prefix search rather than listing everything
    if cmd == "winget_search":
        if len(prefix) < 2:
            return []  # require at least 2 chars before hitting the network
        lines = _run_lines(
            ["winget", "search", prefix, "--accept-source-agreements", "--limit", "40"],
            timeout=10,
        )
        return _parse_winget_available(lines)

    lines = _run_lines(cmd, timeout=20)
    if cfg.get("available_parse") == "yum":
        return sorted(set(_parse_yum_available(lines)))
    return sorted(set(l.strip() for l in lines if l.strip()))


def load_path_entries(prefix: str = "") -> list[str]:
    """Smart path tab-completion that follows directory prefixes.

    With no prefix: lists cwd entries.
    With 'src/fo':  lists src/ and returns 'src/foo', 'src/bar/' etc.
    With '/etc/h':  lists /etc/ and returns '/etc/hosts' etc.
    Trailing / is appended to directories so Tab-again drills in.
    """
    try:
        sep = "\\" if (IS_WINDOWS and "\\" in (prefix or "")) else "/"
        if prefix:
            # Handle Windows drive letters (e.g. C: or d:)
            if len(prefix) == 2 and prefix[1] == ':':
                dir_part = prefix + sep
                last_part = ""
            else:
                # Find the last separator
                last_sep_idx = -1
                for i, char in enumerate(prefix):
                    if char in ('/', '\\'):
                        last_sep_idx = i
                
                if last_sep_idx != -1:
                    dir_part = prefix[:last_sep_idx + 1]
                    last_part = prefix[last_sep_idx + 1:]
                else:
                    dir_part = ""
                    last_part = prefix
            
            dir_to_list = os.path.expanduser(dir_part) if dir_part else "."
        else:
            dir_part = ""
            last_part = ""
            dir_to_list = "."

        if not dir_to_list:
            dir_to_list = "."

        if not os.path.isdir(dir_to_list):
            return []

        entries = []
        is_case_sensitive = not (IS_WINDOWS or IS_MACOS)
        last_part_lower = last_part.lower()

        for name in sorted(os.listdir(dir_to_list)):
            # Skip hidden files unless we explicitly typed a leading dot
            if name.startswith('.') and not last_part.startswith('.'):
                continue

            # Check matching
            if not is_case_sensitive:
                matched = name.lower().startswith(last_part_lower)
            else:
                matched = name.startswith(last_part)

            if matched:
                # Construct path
                if dir_part:
                    if dir_part.endswith('/') or dir_part.endswith('\\'):
                        full = dir_part + name
                    else:
                        full = dir_part + sep + name
                else:
                    full = name

                if os.path.isdir(os.path.join(dir_to_list, name)):
                    full += sep
                entries.append(full)
        return entries
    except OSError:
        return []


def load_block_devices() -> list[tuple[str, str]]:
    """Return a list of (device_path, description) tuples for block devices/partitions.

    Returns entries like ('/dev/sda', 'disk 500G') and ('/dev/sda1', 'part 50G / ext4').
    On Windows returns drive letter volumes. On macOS returns diskutil identifiers.
    """
    if IS_WINDOWS:
        lines = _run_lines(
            ["powershell", "-NoProfile", "-Command",
             "Get-Volume | Where-Object {$_.DriveLetter} | "
             "Select-Object @{N='Dev';E={$_.DriveLetter+':'}}, FileSystem, Size | "
             "Format-Table -HideTableHeaders -AutoSize"],
            timeout=5,
        )
        result = []
        for line in lines:
            line = line.strip()
            if line:
                parts = line.split()
                dev = parts[0] if parts else line
                meta = " ".join(parts[1:]) if len(parts) > 1 else "volume"
                result.append((dev, meta))
        return result

    if IS_MACOS:
        lines = _run_lines(["diskutil", "list", "-plist"], timeout=5)
        # Simpler approach: just list /dev/disk* identifiers
        try:
            import glob
            devs = sorted(glob.glob("/dev/disk*"))
        except Exception:
            devs = []
        return [(d, "disk/partition") for d in devs if not d.endswith("s0")]

    # Linux: use lsblk for rich info
    result = []
    if shutil.which("lsblk"):
        lines = _run_lines(
            ["lsblk", "-o", "NAME,TYPE,SIZE,MOUNTPOINT,FSTYPE", "-rn", "--noheadings"],
            timeout=4,
        )
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            name, typ, size = parts[0], parts[1], parts[2]
            mount = parts[3] if len(parts) > 3 else ""
            fstype = parts[4] if len(parts) > 4 else ""
            dev = f"/dev/{name}"
            meta_parts = [typ, size]
            if fstype:
                meta_parts.append(fstype)
            if mount:
                meta_parts.append(f"→ {mount}")
            result.append((dev, "  ".join(meta_parts)))
    else:
        # Fallback: /proc/partitions
        try:
            with open("/proc/partitions") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) == 4 and parts[3] != "name":
                        name = parts[3]
                        size_kb = int(parts[2]) if parts[2].isdigit() else 0
                        size_str = f"{size_kb // 1024}M" if size_kb < 1024 * 1024 else f"{size_kb // 1024 // 1024}G"
                        result.append((f"/dev/{name}", f"part {size_str}"))
        except Exception:
            pass
    return result


def load_mount_points() -> list[tuple[str, str]]:
    """Return currently mounted filesystems as (mountpoint, description) tuples.

    Used for unmount/check dropdown completions.
    """
    if IS_WINDOWS:
        lines = _run_lines(
            ["powershell", "-NoProfile", "-Command",
             "Get-PSDrive -PSProvider FileSystem | "
             "Select-Object Name,Root | Format-Table -HideTableHeaders"],
            timeout=5,
        )
        result = []
        for line in lines:
            line = line.strip()
            if line:
                parts = line.split()
                if parts:
                    root = parts[1] if len(parts) > 1 else parts[0] + ":\\"
                    result.append((root, "mounted drive"))
        return result

    if IS_MACOS:
        lines = _run_lines(["mount"], timeout=4)
        result = []
        for line in lines:
            parts = line.split(" on ")
            if len(parts) == 2:
                dev = parts[0].strip()
                rest = parts[1].strip()
                mnt = rest.split(" (")[0].strip()
                result.append((mnt, f"from {dev}"))
        return result

    # Linux: parse /proc/mounts
    result = []
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3:
                    dev, mnt, fstype = parts[0], parts[1], parts[2]
                    if fstype in ("tmpfs", "devtmpfs", "sysfs", "proc", "cgroup",
                                  "cgroup2", "devpts", "mqueue", "hugetlbfs",
                                  "debugfs", "securityfs", "fusectl", "bpf",
                                  "tracefs", "pstore", "efivarfs", "configfs",
                                  "ramfs", "overlay", "selinuxfs", "autofs",
                                  "rpc_pipefs", "nsfs", "sunrpc", "nfsd",
                                  "fuse.gvfsd-fuse", "fuse.portal"):
                        continue
                    result.append((mnt, f"from {dev}  [{fstype}]"))
    except Exception:
        pass
    return result


def load_adapters() -> list[str]:
    """List network adapters on the system."""
    if IS_WINDOWS:
        lines = _run_lines(
            ["powershell", "-NoProfile", "-Command", "Get-NetAdapter | Select-Object -ExpandProperty Name"],
            timeout=5,
        )
        return sorted(set(l.strip() for l in lines if l.strip()))
    if IS_MACOS:
        lines = _run_lines(["networksetup", "-listallnetworkservices"], timeout=3)
        # First line is a notice; lines starting with * are disabled
        return sorted(
            l.strip().lstrip("*").strip()
            for l in lines[1:] if l.strip()
        )
    try:
        return sorted(os.listdir('/sys/class/net/'))
    except OSError:
        return []


def load_users() -> list[str]:
    """List local user accounts on the system."""
    if IS_WINDOWS:
        lines = _run_lines(
            ["powershell", "-NoProfile", "-Command", "Get-LocalUser | Select-Object -ExpandProperty Name"],
            timeout=5,
        )
        if not lines:
            raw_lines = _run_lines(["net", "user"], timeout=5)
            started = False
            for line in raw_lines:
                if "-------------------" in line:
                    started = True
                    continue
                if started:
                    if "The command completed successfully" in line:
                        break
                    for part in line.split():
                        if part.strip():
                            lines.append(part.strip())
        return sorted(set(l.strip() for l in lines if l.strip()))
        
    if IS_MACOS:
        lines = _run_lines(["dscl", ".", "list", "/Users"], timeout=5)
        return sorted(set(l.strip() for l in lines if l.strip() and not l.strip().startswith("_")))

    try:
        users = []
        with open("/etc/passwd", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) >= 3:
                    username = parts[0].strip()
                    try:
                        uid = int(parts[2].strip())
                        # Keep root (0) and standard users (>= 1000)
                        if uid == 0 or uid >= 1000:
                            users.append(username)
                    except ValueError:
                        users.append(username)
        return sorted(users)
    except OSError:
        return []


def load_groups() -> list[str]:
    """List local groups on the system."""
    if IS_WINDOWS:
        lines = _run_lines(
            ["powershell", "-NoProfile", "-Command", "Get-LocalGroup | Select-Object -ExpandProperty Name"],
            timeout=5,
        )
        if not lines:
            raw_lines = _run_lines(["net", "localgroup"], timeout=5)
            started = False
            for line in raw_lines:
                if "-------------------" in line:
                    started = True
                    continue
                if started:
                    if "The command completed successfully" in line:
                        break
                    if line.startswith("*"):
                        lines.append(line.lstrip("*").strip())
                    elif line.strip():
                        lines.append(line.strip())
        return sorted(set(l.strip() for l in lines if l.strip()))

    if IS_MACOS:
        lines = _run_lines(["dscl", ".", "list", "/Groups"], timeout=5)
        return sorted(set(l.strip() for l in lines if l.strip() and not l.strip().startswith("_")))

    try:
        groups = []
        with open("/etc/group", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) >= 3:
                    groupname = parts[0].strip()
                    try:
                        gid = int(parts[2].strip())
                        if gid == 0 or gid >= 1000 or groupname in ("wheel", "sudo", "docker", "admin", "adm", "staff"):
                            groups.append(groupname)
                    except ValueError:
                        groups.append(groupname)
        return sorted(groups)
    except OSError:
        return []


class BackgroundNames:
    """Loads a (possibly slow) name list in the background so startup and
    the prompt never block on it. Reads of `.names` are safe without a lock
    because CPython list-reference assignment is atomic."""

    def __init__(self, loader: Callable[[], list[str]], start_immediately: bool = True):
        self.names: list[str] = []
        self._loader = loader
        self._started = False
        self._lock = threading.Lock()
        self._thread = None
        if start_immediately:
            self.start()

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
            self._thread = threading.Thread(target=self._load, daemon=True)
            self._thread.start()

    def get(self) -> list[str]:
        self.start()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        return self.names

    def _load(self):
        try:
            self.names = self._loader()
        except Exception:
            self.names = []

def load_system_commands() -> dict[str, str]:
    """Scan PATH for all executables and enrich with descriptions."""
    cmds = {}
    if IS_WINDOWS:
        path_dirs = os.environ.get("PATH", "").split(os.pathsep)
        exts = (".exe", ".bat", ".cmd", ".ps1")
        for d in path_dirs:
            if os.path.isdir(d):
                try:
                    for f in os.listdir(d):
                        if f.lower().endswith(exts):
                            name = os.path.splitext(f)[0].lower()
                            if name not in cmds:
                                cmds[name] = "System command"
                except OSError:
                    pass
        # Add common PowerShell cmdlets
        ps_cmdlets = {
            "get-process": "Get list of active processes",
            "stop-process": "Terminate one or more running processes",
            "start-process": "Start one or more processes",
            "get-service": "Get status of services on a computer",
            "start-service": "Start one or more stopped services",
            "stop-service": "Stop one or more running services",
            "restart-service": "Stop and then start one or more services",
            "get-content": "Get the content of a file",
            "set-content": "Write or replace content in a file",
            "add-content": "Append content to a specified file",
            "clear-content": "Delete the contents of a file",
            "get-command": "Get all commands/cmdlets",
            "get-help": "Get help/documentation for a command",
            "get-item": "Get the item at a specified location",
            "get-childitem": "Get items and child items in folder",
            "copy-item": "Copy an item from one location to another",
            "move-item": "Move an item from one location to another",
            "remove-item": "Delete specified items/files",
            "new-item": "Create a new item (file, folder, etc.)",
            "rename-item": "Rename an item",
            "get-location": "Get information about current working directory",
            "set-location": "Set the current working directory",
            "get-history": "Get a list of commands entered in session",
            "clear-history": "Delete commands from command history",
            "invoke-webrequest": "Get content from a web page",
            "invoke-restmethod": "Send HTTP request and get structured data",
            "get-netipaddress": "Get IP address configuration",
            "get-netipinterface": "Get IP interface properties",
            "get-netroute": "Get IP routing table",
            "get-netadapter": "Get basic network adapter properties",
            "test-netconnection": "Diagnose connection to a remote host",
            "get-localuser": "Get local user accounts",
            "get-localgroup": "Get local groups",
            "new-localuser": "Create a local user account",
            "remove-localuser": "Delete local user accounts",
            "enable-localuser": "Enable local user account",
            "disable-localuser": "Disable local user account",
            "add-localgroupmember": "Add user to a local group",
            "remove-localgroupmember": "Remove user from local group",
            "get-eventlog": "Get events in event log",
            "get-winevent": "Get events from event logs",
            "out-file": "Send output to a file",
            "select-object": "Select properties of an object",
            "where-object": "Filter objects based on property values",
            "sort-object": "Sort objects by property values",
            "foreach-object": "Perform operation against each item",
            "get-date": "Get the current date and time",
            "get-host": "Get current host program",
            "get-member": "Get members, properties, and methods of object",
            "new-object": "Create instance of a .NET object",
        }
        for k, v in ps_cmdlets.items():
            if k not in cmds:
                cmds[k] = v
        return cmds

    # Linux / macOS
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    for d in path_dirs:
        if os.path.isdir(d):
            try:
                for f in os.listdir(d):
                    if not f.startswith(".") and os.access(os.path.join(d, f), os.X_OK):
                        if f not in cmds:
                            cmds[f] = "System command"
            except OSError:
                pass
    
    try:
        proc = subprocess.run(["apropos", "-s", "1,8", "."], capture_output=True, text=True, timeout=2)
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                parts = line.split(" - ", 1)
                if len(parts) == 2:
                    name_part = parts[0].strip()
                    desc = parts[1].strip()
                    name = name_part.split()[0].strip()
                    if name in cmds:
                        if len(desc) > 60:
                            desc = desc[:57] + "..."
                        cmds[name] = desc
    except Exception:
        pass
        
    return cmds
