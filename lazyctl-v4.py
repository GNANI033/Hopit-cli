#!/usr/bin/env python3
"""
lazyctl — a fast TUI overlay that makes common admin commands short and
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
import getpass
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Callable, List, Optional

from prompt_toolkit import PromptSession, prompt
from prompt_toolkit.completion import Completer, Completion, WordCompleter, DummyCompleter
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

IS_WINDOWS = os.name == "nt"
IS_MACOS   = not IS_WINDOWS and __import__("sys").platform == "darwin"

# Windows Terminal sets WT_SESSION; plain cmd.exe / PowerShell ISE do not.
# This controls whether we can use truecolor ANSI and Nerd Font glyphs.
IS_WINDOWS_TERMINAL = IS_WINDOWS and bool(os.environ.get("WT_SESSION"))

if IS_WINDOWS:
    # colorama translates ANSI escape codes to Win32 calls for plain cmd.exe.
    # In Windows Terminal it's a no-op (VT processing is built-in), but
    # initializing it is always safe.
    try:
        import colorama
        colorama.init()
    except ImportError:
        pass  # optional; install via requirements.txt

    if IS_WINDOWS_TERMINAL:
        # Tell prompt_toolkit to emit 24-bit color sequences.
        os.environ.setdefault("PROMPT_TOOLKIT_COLOR_DEPTH", "DEPTH_24_BIT")

# Build the Rich Console with the right color profile.
if IS_WINDOWS_TERMINAL:
    # Force truecolor Rich output inside Windows Terminal.
    console = Console(color_system="truecolor", force_terminal=True)
else:
    # Let Rich auto-detect (works on Linux; degrades gracefully on plain cmd).
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
        # available_cmd uses a prefix-aware search — see load_available_packages() for the winget branch
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


def detect_package_manager() -> Optional[str]:
    """Detect by checking which manager binary is actually on PATH."""
    if IS_WINDOWS:
        for mgr in ("winget", "choco", "scoop"):
            if shutil.which(mgr):
                return mgr
        return None
    if IS_MACOS:
        return "brew" if shutil.which("brew") else None
    for mgr in ("apt-get", "dnf", "yum", "pacman", "zypper", "apk"):
        if shutil.which(mgr):
            return mgr
    return None


def read_os_pretty_name() -> str:
    if IS_WINDOWS:
        try:
            proc = subprocess.run(["cmd", "/c", "ver"], capture_output=True, text=True, timeout=2)
            return proc.stdout.strip() or "Windows"
        except Exception:
            return "Windows"
    if IS_MACOS:
        try:
            name = subprocess.run(["sw_vers", "-productName"],    capture_output=True, text=True, timeout=2).stdout.strip()
            ver  = subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True, timeout=2).stdout.strip()
            return f"{name} {ver}" if name else "macOS"
        except Exception:
            return "macOS"
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except FileNotFoundError:
        pass
    return "unknown distro"


def detect_editor() -> Optional[str]:
    """Return the first available text editor, preferring nano."""
    if IS_WINDOWS:
        for editor in ("notepad.exe", "code.cmd", "code.exe"):
            if shutil.which(editor):
                return editor
        return "notepad.exe"
    for editor in ("nano", "vim", "vi", "micro", "ne", "joe", "emacs"):
        if shutil.which(editor):
            return editor
    return None


def get_git_branch() -> Optional[str]:
    """Return the current git branch name if we are inside a git repository."""
    try:
        proc = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, timeout=0.1)
        if proc.returncode == 0:
            branch = proc.stdout.strip()
            if branch:
                return branch
    except Exception:
        pass
    return None


def with_privilege(argv: List[str], needs_sudo: bool) -> List[str]:
    """On Linux, prepend sudo when needed. On Windows, privileges must be
    requested via UAC at launch time — we can only warn here, not escalate."""
    if IS_WINDOWS:
        # Cannot programmatically elevate mid-session on Windows without
        # spawning a new elevated process. We return argv as-is and rely on
        # the user having launched the terminal as Administrator when needed.
        return argv
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
    if IS_WINDOWS:
        lines = _run_lines(
            ["powershell", "-NoProfile", "-Command", "Get-Service | Select-Object -ExpandProperty Name"],
            timeout=5,
        )
        return sorted(set(l.strip() for l in lines if l.strip()))

    if IS_MACOS:
        names: List[str] = []
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


def _parse_winget_installed(lines: List[str]) -> List[str]:
    """Parse 'winget list' output — extract the package ID column (col 1)."""
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


def _parse_winget_available(lines: List[str]) -> List[str]:
    """Parse 'winget search <prefix>' output — extract the package ID column."""
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


def _parse_choco_installed(lines: List[str]) -> List[str]:
    names = []
    for line in lines:
        line = line.strip()
        if "|" in line:
            names.append(line.split("|", 1)[0])
    return names


def _parse_scoop_installed(lines: List[str]) -> List[str]:
    names = []
    for line in lines:
        line = line.strip()
        if not line or line.lower().startswith(("installed", "name ")):
            continue
        names.append(line.split()[0])
    return names


def load_installed_packages(manager: Optional[str]) -> List[str]:
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


def load_available_packages(manager: Optional[str], prefix: str = "") -> List[str]:
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


def load_path_entries(prefix: str = "") -> List[str]:
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


# ─── helpers used inside lambdas ────────────────────────────────────────────
def _q(s: str) -> str:
    """Shell-quote a single token (minimal, good enough for paths)."""
    if IS_WINDOWS:
        return f'"{s}"'
    return "'" + s.replace("'", "'\\''") + "'"

def _join(args): return ' '.join(_q(a) for a in args)
def _files(args): return ' '.join(_q(a) for a in args if not a.startswith('-'))
def _nval(args, flag, default="10"):
    """Extract value after -n / --lines flag."""
    for i, a in enumerate(args):
        if a in ("-n", "--lines", "-"+flag) and i+1 < len(args):
            return args[i+1]
    return default

# ─── Linux/macOS → Windows ────────────────────────────────────────────────
_UNIX_TO_WIN: dict = {
    # ── file ops ──────────────────────────────────────────────────────────
    "cp":       lambda a: (f'xcopy /E /I /H /Y {_q(a[0])} {_q(a[1])}' if len(a)>=2 and os.path.isdir(a[0])
                           else 'copy ' + _join(a)),
    "mv":       lambda a: 'move ' + _join(a),
    "rm":       lambda a: ('rd /s /q ' if any(x in ('-r','-rf','-fr','-Rf') for x in a) else 'del /Q ')
                          + ' '.join(_q(x) for x in a if not x.startswith('-')),
    "ls":       lambda a: 'dir ' + ' '.join(a),
    "cat":      lambda a: 'type ' + _files(a),
    "touch":    lambda a: f'type nul > {_q(a[0])}' if a else 'type nul',
    "head":     lambda a: 'powershell -Command "Get-Content ' + _files(a) + ' -TotalCount ' + _nval(a,'n') + '"',
    "tail":     lambda a: 'powershell -Command "Get-Content ' + _files(a) + ' -Tail ' + _nval(a,'n') + '"',
    "wc":       lambda a: ('find /c /v "" ' + _files(a) if '-l' in a
                           else 'powershell -Command "(Get-Content ' + _files(a) + ' | Measure-Object -Word).Words"'),
    "diff":     lambda a: 'fc ' + _join(a),
    "stat":     lambda a: f'powershell -Command "Get-Item {_files(a)} | Format-List *"',
    "du":       lambda a: 'dir /s ' + _files(a),
    "df":       lambda a: 'powershell -Command "Get-PSDrive -PSProvider FileSystem | Format-Table"',
    "ln":       lambda a: ('mklink /D ' + _q(a[-1]) + ' ' + _q(a[-2])
                           if len(a)>=2 and '-s' in a and os.path.isdir(a[-2])
                           else 'mklink ' + _q(a[-1]) + ' ' + _q(a[-2])) if len(a)>=2 else '',
    "chmod":    lambda a: '',   # no equivalent; silently skip
    "chown":    lambda a: '',
    "chgrp":    lambda a: '',
    "less":     lambda a: 'more ' + _files(a),
    "more":     lambda a: 'more ' + _files(a),
    "sort":     lambda a: 'sort ' + ' '.join(a),
    "uniq":     lambda a: 'powershell -Command "Get-Content {_files(a)} | Sort-Object -Unique"',
    "tee":      lambda a: 'powershell -Command "Tee-Object -FilePath ' + (_q(a[-1]) if a else '"out.txt"') + '"',
    "zip":      lambda a: f'powershell -Command "Compress-Archive -Path {_q(a[0] if a else ".")} -DestinationPath {_q((a[1] if len(a)>1 else a[0]+".zip") if a else "archive.zip")}"',
    "unzip":    lambda a: f'powershell -Command "Expand-Archive -Path {_q(a[0] if a else ".")} -DestinationPath {_q(a[1] if len(a)>1 else ".")}"',
    "tar":      lambda a: 'tar ' + ' '.join(a),   # Windows 10+ ships tar
    # ── search ────────────────────────────────────────────────────────────
    "grep":     lambda a: 'findstr ' + ' '.join(a),
    "find":     lambda a: 'dir /s /b ' + _files(a),
    "which":    lambda a: 'where ' + ' '.join(a),
    "locate":   lambda a: 'where /r . ' + (a[0] if a else ''),
    # ── processes ─────────────────────────────────────────────────────────
    "ps":       lambda a: 'tasklist',
    "kill":     lambda a: (f'taskkill /PID {a[0]} /F' if a and a[0].lstrip('-').isdigit()
                           else 'taskkill /IM ' + (a[0] if a else '') + ' /F'),
    "killall":  lambda a: 'taskkill /IM ' + (a[0] if a else '') + ' /F',
    "pkill":    lambda a: 'taskkill /IM ' + (a[0] if a else '') + ' /F',
    "pgrep":    lambda a: 'tasklist | findstr /I ' + (a[0] if a else ''),
    "top":      lambda a: 'powershell -Command "Get-Process | Sort-Object CPU -Descending | Select-Object -First 20 | Format-Table -AutoSize"',
    "htop":     lambda a: 'powershell -Command "Get-Process | Sort-Object CPU -Descending | Select-Object -First 20 | Format-Table -AutoSize"',
    "nice":     lambda a: ' '.join(a),   # Windows scheduling is different
    # ── system ────────────────────────────────────────────────────────────
    "uname":    lambda a: 'powershell -Command "[System.Environment]::OSVersion.VersionString"',
    "whoami":   lambda a: 'whoami',
    "hostname": lambda a: 'hostname',
    "uptime":   lambda a: 'powershell -Command "(Get-Date)-(gcim Win32_OperatingSystem).LastBootUpTime|Select Days,Hours,Minutes"',
    "free":     lambda a: 'powershell -Command "gcim Win32_OperatingSystem|Select TotalVisibleMemorySize,FreePhysicalMemory"',
    "lscpu":    lambda a: 'wmic cpu get Name,NumberOfCores,MaxClockSpeed',
    "lsblk":    lambda a: 'wmic diskdrive list brief',
    "lsusb":    lambda a: 'powershell -Command "Get-PnpDevice -Class USB | Format-Table"',
    "lspci":    lambda a: 'powershell -Command "Get-PnpDevice | Format-Table"',
    "env":      lambda a: 'set',
    "printenv": lambda a: ('echo %" + a[0] + "%') if a else 'set',
    "export":   lambda a: ('set ' + a[0]) if a else 'set',
    "history":  lambda a: 'doskey /history',
    "man":      lambda a: (' '.join(a) + ' --help') if a else 'help',
    "sudo":     lambda a: ' '.join(a),   # run without elevation (user must launch as admin)
    "su":       lambda a: 'runas /user:Administrator cmd',
    "date":     lambda a: 'powershell -Command "Get-Date"',
    "sleep":    lambda a: 'timeout /T ' + (a[0] if a else '1') + ' /NOBREAK',
    "reboot":   lambda a: 'shutdown /r /t 0',
    "shutdown": lambda a: 'shutdown /s /t 0',
    "halt":     lambda a: 'shutdown /s /t 0',
    # ── network ───────────────────────────────────────────────────────────
    "ifconfig": lambda a: 'ipconfig /all',
    "ip":       lambda a: 'ipconfig /all',
    "traceroute": lambda a: 'tracert ' + ' '.join(a),
    "nslookup": lambda a: 'nslookup ' + ' '.join(a),
    "dig":      lambda a: 'nslookup ' + (a[0] if a else ''),
    "host":     lambda a: 'nslookup ' + (a[0] if a else ''),
    "wget":     lambda a: 'curl -L -O ' + (a[-1] if a else ''),
    "curl":     lambda a: 'curl ' + ' '.join(a),   # Windows 10+ ships curl
    "ssh":      lambda a: 'ssh ' + ' '.join(a),    # Windows 10+ ships OpenSSH
    "scp":      lambda a: 'scp ' + ' '.join(a),
    "netstat":  lambda a: 'netstat ' + ' '.join(a),
    "ss":       lambda a: 'netstat -ano',
    "nmap":     lambda a: 'nmap ' + ' '.join(a),
    "ping":     lambda a: 'ping ' + ' '.join(a),
    # ── text / misc ───────────────────────────────────────────────────────
    "echo":     lambda a: 'echo ' + ' '.join(a),
    "clear":    lambda a: 'cls',
    "pwd":      lambda a: 'cd',
    "xdg-open": lambda a: 'start ' + (a[0] if a else '.'),
    "open":     lambda a: 'start ' + (a[0] if a else '.'),   # macOS open
    "xclip":    lambda a: 'clip',
    "xsel":     lambda a: 'clip',
    "strings":  lambda a: 'findstr /p ' + ' '.join(a),
    "base64":   lambda a: f'powershell -Command "[Convert]::ToBase64String([IO.File]::ReadAllBytes({_q(a[0])}))"' if a else '',
    "md5sum":   lambda a: f'powershell -Command "Get-FileHash {_files(a)} -Algorithm MD5 | Format-Table"',
    "sha256sum": lambda a: f'powershell -Command "Get-FileHash {_files(a)} -Algorithm SHA256 | Format-Table"',
    "crontab":  lambda a: 'schtasks ' + ' '.join(a),
    "service":  lambda a: ('sc start ' + a[1] if len(a)>=2 and a[1]=='start'
                           else 'sc stop ' + a[1] if len(a)>=2 and a[1]=='stop'
                           else 'sc query ' + (a[0] if a else '')),
    "systemctl": lambda a: ('sc start ' + a[1] if len(a)>=2 and a[0]=='start'
                            else 'sc stop ' + a[1] if len(a)>=2 and a[0]=='stop'
                            else 'sc query ' + (a[1] if len(a)>=2 else '')),
}

# ─── Windows → Linux/macOS ────────────────────────────────────────────────
_WIN_TO_UNIX: dict = {
    # ── file ops ──────────────────────────────────────────────────────────
    "del":      lambda a: 'rm ' + _join([x for x in a if not x.startswith('/')]),
    "rd":       lambda a: 'rm -rf ' + _join([x for x in a if not x.startswith('/')]),
    "rmdir":    lambda a: 'rmdir ' + _join(a),
    "dir":      lambda a: 'ls -la ' + ' '.join(a),
    "type":     lambda a: 'cat ' + _join(a),
    "xcopy":    lambda a: 'cp -r ' + _join(a),
    "robocopy": lambda a: 'rsync -av ' + _join(a[:2]) if len(a)>=2 else 'rsync -av ' + _join(a),
    "md":       lambda a: 'mkdir -p ' + _join(a),
    "ren":      lambda a: 'mv ' + _join(a),
    "attrib":   lambda a: '',    # no Unix equivalent; skip
    "fc":       lambda a: 'diff ' + _join(a),
    "comp":     lambda a: 'diff ' + _join(a),
    "more":     lambda a: 'less ' + _join(a),
    "tree":     lambda a: ('find ' + (a[0] if a else '.') + ' | sort'),
    "compact":  lambda a: '',    # NTFS-only; skip
    # ── processes ─────────────────────────────────────────────────────────
    "tasklist": lambda a: 'ps aux',
    "taskkill": lambda a: ('kill ' + next((a[i+1] for i,x in enumerate(a) if x.upper()=='/PID'), '')
                           if any(x.upper()=='/PID' for x in a)
                           else 'killall ' + next((a[i+1] for i,x in enumerate(a) if x.upper()=='/IM'), '')),
    "tskill":   lambda a: 'kill ' + (a[0] if a else ''),
    "start":    lambda a: 'xdg-open ' + (a[0] if a else '.'),
    # ── system ────────────────────────────────────────────────────────────
    "cls":      lambda a: 'clear',
    "ver":      lambda a: 'uname -r',
    "systeminfo": lambda a: 'uname -a && cat /proc/cpuinfo | head -20',
    "set":      lambda a: ('echo "$' + a[0].split('=')[0] + '"' if a and '=' not in a[0] else 'env'),
    "where":    lambda a: 'which ' + ' '.join(a),
    "whoami":   lambda a: 'whoami',
    "hostname":  lambda a: 'hostname',
    "date":     lambda a: 'date',
    "time":     lambda a: 'date +%T',
    "timeout":  lambda a: 'sleep ' + next((a[i+1] for i,x in enumerate(a) if x.upper()=='/T'), a[0] if a else '1'),
    "waitfor":  lambda a: 'sleep 60',
    "runas":    lambda a: 'sudo ' + ' '.join(a),
    "chdir":    lambda a: 'pwd' if not a else 'cd ' + (a[0] if a else ''),
    "path":     lambda a: 'echo $PATH',
    "help":     lambda a: ('man ' + a[0] if a else 'help'),
    "assoc":    lambda a: 'xdg-mime query default ' + (a[0] if a else ''),
    "schtasks": lambda a: 'crontab -l',
    "ipconfig": lambda a: 'ip a',
    "tracert":  lambda a: 'traceroute ' + ' '.join(a),
    "nslookup": lambda a: 'nslookup ' + ' '.join(a),
    "netstat":  lambda a: 'netstat ' + ' '.join(a),
    "ping":     lambda a: 'ping ' + ' '.join(a),
    "nmap":     lambda a: 'nmap ' + ' '.join(a),
    "curl":     lambda a: 'curl ' + ' '.join(a),
    "ssh":      lambda a: 'ssh ' + ' '.join(a),
    "scp":      lambda a: 'scp ' + ' '.join(a),
    "echo":     lambda a: 'echo ' + ' '.join(a),
    "reg":      lambda a: '',    # Windows registry; no Unix equivalent
    "regedit":  lambda a: '',
    "msiexec":  lambda a: '',
    "wmic":     lambda a: '',
    "sc":       lambda a: ('systemctl start ' + a[1] if len(a)>=2 and a[0]=='start'
                           else 'systemctl stop ' + a[1] if len(a)>=2 and a[0]=='stop'
                           else 'systemctl status ' + a[1] if len(a)>=2 and a[0]=='query'
                           else 'systemctl ' + ' '.join(a)),
    "net":      lambda a: ('systemctl start ' + a[1] if len(a)>=2 and a[0].lower()=='start'
                           else 'systemctl stop ' + a[1] if len(a)>=2 and a[0].lower()=='stop'
                           else ' '.join(a)),
}

# ─── Linux-specific → macOS equivalent (applied on macOS only) ───────────
_LINUX_TO_MAC: dict = {
    "xdg-open":  lambda a: 'open ' + (a[0] if a else '.'),
    "xclip":     lambda a: ('pbcopy' if not any('paste' in x for x in a) else 'pbpaste'),
    "xsel":      lambda a: ('pbpaste' if '--output' in a or '-o' in a else 'pbcopy'),
    "update-alternatives": lambda a: '',
    "apt":       lambda a: 'brew ' + ' '.join(a),
    "apt-get":   lambda a: 'brew ' + ' '.join(a),
    "dpkg":      lambda a: 'brew ' + ' '.join(a),
    "yum":       lambda a: 'brew ' + ' '.join(a),
    "dnf":       lambda a: 'brew ' + ' '.join(a),
    "pacman":    lambda a: 'brew ' + ' '.join(a),
    "systemctl": lambda a: ('brew services start ' + a[1] if len(a)>=2 and a[0]=='start'
                            else 'brew services stop ' + a[1] if len(a)>=2 and a[0]=='stop'
                            else 'brew services restart ' + a[1] if len(a)>=2 and a[0]=='restart'
                            else 'brew services info ' + a[1] if len(a)>=2 and a[0] in ('status','is-active')
                            else 'brew services list' if a and a[0]=='list' else 'launchctl ' + ' '.join(a)),
    "journalctl": lambda a: ('log stream' if '-f' in a else 'log show --last 1h') + (
                             ' --predicate \'process == "' + next((a[i+1] for i,x in enumerate(a) if x=='-u'), '') + '"\''),
    "service":   lambda a: ('brew services start ' + a[0] if len(a)>=2 and a[1]=='start'
                            else 'brew services stop ' + a[0] if len(a)>=2 and a[1]=='stop'
                            else 'brew services info ' + (a[0] if a else '')),
    "ss":        lambda a: ('lsof -i :' + next((x for x in a if x.isdigit()),'') if any(x.isdigit() for x in a) else 'lsof -i -n -P'),
    "ip":        lambda a: 'ifconfig',
    "netstat":   lambda a: 'netstat ' + ' '.join(a),
    "free":      lambda a: 'vm_stat',
    "lscpu":     lambda a: 'sysctl -a | grep machdep.cpu',
    "lsblk":     lambda a: 'diskutil list',
    "lsusb":     lambda a: 'system_profiler SPUSBDataType',
    "lspci":     lambda a: 'system_profiler SPPCIDataType',
    "nproc":     lambda a: 'sysctl -n hw.logicalcpu',
    "uname":     lambda a: 'uname ' + ' '.join(a),   # works on macOS too
    "fuser":     lambda a: 'lsof ' + ' '.join(a),
}

# ─── macOS-specific → Linux equivalent (applied on Linux only) ───────────
_MAC_TO_LINUX: dict = {
    "open":      lambda a: 'xdg-open ' + (a[0] if a else '.'),
    "pbcopy":    lambda a: 'xclip -selection clipboard',
    "pbpaste":   lambda a: 'xclip -selection clipboard -o',
    "say":       lambda a: 'espeak ' + ' '.join(a),
    "sw_vers":   lambda a: 'cat /etc/os-release',
    "launchctl": lambda a: ('systemctl start ' + a[1] if len(a)>=2 and a[0]=='load'
                            else 'systemctl stop ' + a[1] if len(a)>=2 and a[0]=='unload'
                            else 'systemctl ' + ' '.join(a)),
    "brew":      lambda a: ('apt-get install ' + ' '.join(a[1:]) if a and a[0]=='install'
                            else 'apt-get remove ' + ' '.join(a[1:]) if a and a[0]=='remove'
                            else 'apt-get update && apt-get upgrade' if a and a[0]=='upgrade'
                            else 'apt-get ' + ' '.join(a)),
    "diskutil":  lambda a: 'lsblk',
    "networksetup": lambda a: 'nmcli ' + ' '.join(a),
    "caffeinate": lambda a: '',
    "osascript": lambda a: '',
    "defaults":  lambda a: '',
    "plutil":    lambda a: '',
    "dscacheutil": lambda a: '',
    "airport":   lambda a: 'iwconfig',
}


def translate_cross_platform(tokens: List[str]) -> Optional[str]:
    """Translate a foreign-OS command to the current OS's equivalent.

    Tables used per platform:
      Windows  : _UNIX_TO_WIN   (Linux/macOS commands → Windows)
      macOS    : _WIN_TO_UNIX + _LINUX_TO_MAC (Windows + Linux-specific → macOS)
      Linux    : _WIN_TO_UNIX + _MAC_TO_LINUX (Windows + macOS-specific → Linux)
    """
    if not tokens:
        return None
    cmd = tokens[0].lower()
    args = tokens[1:]

    if IS_WINDOWS:
        table = _UNIX_TO_WIN
    elif IS_MACOS:
        table = {**_WIN_TO_UNIX, **_LINUX_TO_MAC}
    else:
        table = {**_WIN_TO_UNIX, **_MAC_TO_LINUX}

    fn = table.get(cmd)
    if fn is None:
        return None
    try:
        translated = fn(args)
    except Exception:
        return None
    return translated.strip() if translated and translated.strip() else None



def load_adapters() -> List[str]:
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


# --------------------------------------------------------------------------
# Shell / alias helpers
# --------------------------------------------------------------------------

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


def shell_command(line: str) -> List[str]:
    if IS_WINDOWS:
        return ["cmd", "/c", line]
    return ["bash", "-c", line]


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


def ps_command(script: str) -> List[str]:
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def system_status_cmd(svc: str) -> List[str]:
    if IS_WINDOWS:
        return ["sc", "query", svc]
    if IS_MACOS:
        if shutil.which("brew"):
            return ["brew", "services", "info", svc]
        return ["launchctl", "print", f"system/{svc}"]
    return ["systemctl", "status", svc]


def system_start_cmd(svc: str) -> List[str]:
    if IS_WINDOWS:
        return ["sc", "start", svc]
    if IS_MACOS:
        if shutil.which("brew"):
            return ["brew", "services", "start", svc]
        return ["sudo", "launchctl", "load", f"/Library/LaunchDaemons/{svc}.plist"]
    return ["systemctl", "start", svc]


def system_stop_cmd(svc: str) -> List[str]:
    if IS_WINDOWS:
        return ["sc", "stop", svc]
    if IS_MACOS:
        if shutil.which("brew"):
            return ["brew", "services", "stop", svc]
        return ["sudo", "launchctl", "unload", f"/Library/LaunchDaemons/{svc}.plist"]
    return ["systemctl", "stop", svc]


def system_restart_cmd(svc: str) -> List[str]:
    if IS_WINDOWS:
        return ps_command(f"Restart-Service -Name {ps_quote(svc)}")
    if IS_MACOS:
        if shutil.which("brew"):
            return ["brew", "services", "restart", svc]
        return ["bash", "-c",
                f"sudo launchctl unload /Library/LaunchDaemons/{svc}.plist 2>/dev/null; "
                f"sudo launchctl load /Library/LaunchDaemons/{svc}.plist"]
    return ["systemctl", "restart", svc]


def system_logs_cmd(svc: str) -> List[str]:
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


def system_live_logs_cmd(svc: str) -> List[str]:
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


def reboot_cmd(arg: str) -> List[str]:
    if IS_WINDOWS:
        delay = shutdown_delay_seconds(arg)
        return ["shutdown", "/r", "/t", str(delay)]
    return ["shutdown", "-r", shutdown_time_arg(arg)]


def poweroff_cmd(arg: str) -> List[str]:
    if IS_WINDOWS:
        delay = shutdown_delay_seconds(arg)
        return ["shutdown", "/s", "/t", str(delay)]
    return ["shutdown", "-h", shutdown_time_arg(arg)]


def cancel_shutdown_cmd() -> List[str]:
    if IS_WINDOWS:
        return ["shutdown", "/a"]
    return ["shutdown", "-c"]


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


def list_cmd(arg: str) -> List[str]:
    if IS_WINDOWS:
        if arg.lower() == "all":
            return ["cmd", "/c", "dir", "/a"]
        return ["cmd", "/c", "dir", arg] if arg else ["cmd", "/c", "dir"]
    return ["ls", "-la", "--color=always"] if arg.lower() == "all" else (["ls", "--color=always", arg] if arg else ["ls", "--color=always"])


def ip_cmd() -> List[str]:
    if IS_WINDOWS:
        return ["ipconfig", "/all"]
    if IS_MACOS:
        return ["ifconfig"]
    return ["ip", "-c=always", "a"]


def port_cmd(arg: str) -> List[str]:
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


def containers_cmd() -> List[str]:
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


# --------------------------------------------------------------------------
# Build the command registry
# --------------------------------------------------------------------------

def build_commands(manager: Optional[str], names) -> dict:
    """`names` is a dict of zero-arg callables returning current candidate
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
        # ── Universal file-system commands (Python shutil, same on all OSes) ──
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
            run=lambda _: shell_command(MANAGER_UPDATE_CMDS[manager]),
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

    content = Text.from_ansi(output) if output else "(no output)"
    console.print(Panel(content, title=label, border_style=border, expand=False))


def print_help(commands: dict, manager: Optional[str]):
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
                console.print("[yellow]netsh returned a non-zero exit code — verify with 'ip' command. "
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
                console.print("[yellow]netsh returned a non-zero exit code — verify with 'ip' command. "
                              "You may need to run lazyctl as Administrator.[/yellow]")
    except KeyboardInterrupt:
        console.print("\n[dim]Cancelled.[/dim]")


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

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
                # Try cross-platform translation first (cp→copy, del→rm, etc.)
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


if __name__ == "__main__":
    main()
