import os
import shutil
import subprocess

# Must be set before prompt_toolkit creates its output object. Some terminals
# don't answer cursor-position reports reliably, and waiting for those replies
# can make typing appear frozen.
os.environ.setdefault("PROMPT_TOOLKIT_NO_CPR", "1")

from rich.console import Console

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


def detect_package_manager() -> str | None:
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


def detect_editor() -> str | None:
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


def get_git_branch() -> str | None:
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


def with_privilege(argv: list[str], needs_sudo: bool) -> list[str]:
    """On Linux, prepend sudo when needed. On Windows, privileges must be
    requested via UAC at launch time -- we can only warn here, not escalate."""
    if IS_WINDOWS:
        # Cannot programmatically elevate mid-session on Windows without
        # spawning a new elevated process. We return argv as-is and rely on
        # the user having launched the terminal as Administrator when needed.
        return argv
    if needs_sudo and os.geteuid() != 0:
        return ["sudo"] + argv
    return argv
