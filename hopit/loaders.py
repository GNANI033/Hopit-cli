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
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
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
            exp = os.path.expanduser(prefix)
            has_sep = "/" in prefix or (IS_WINDOWS and "\\" in prefix)
            dir_exp  = os.path.dirname(exp)  if has_sep else "."
            dir_orig = os.path.dirname(prefix) if has_sep else ""
            if not dir_exp:
                dir_exp = "."
        else:
            dir_exp = "."
            dir_orig = ""
        entries = []
        for name in sorted(os.listdir(dir_exp)):
            full = (dir_orig.rstrip("/\\") + sep + name) if dir_orig else name
            if os.path.isdir(os.path.join(dir_exp, name)):
                full += sep
            entries.append(full)
        return entries
    except OSError:
        return []


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


class BackgroundNames:
    """Loads a (possibly slow) name list in the background so startup and
    the prompt never block on it. Reads of `.names` are safe without a lock
    because CPython list-reference assignment is atomic."""

    def __init__(self, loader: Callable[[], list[str]], start_immediately: bool = True):
        self.names: list[str] = []
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

    def get(self) -> list[str]:
        self.start()
        return self.names

    def _load(self):
        try:
            self.names = self._loader()
        except Exception:
            self.names = []
