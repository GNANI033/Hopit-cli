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

THEMES = {
    "hopit": {
        "name": "Hopit Green (Default)",
        "hopit": "#b8d769",   # Bright lime green (Base)
        "user": "#c6df87",    # Light lime green (20% Tint)
        "cwd": "#d4e7a5",     # Lighter lime green (40% Tint)
        "git": "#eaf3d2",     # Soft green-white (70% Tint)
        "time": "#f8fbf0",    # Very light green-white (90% Tint)
        "text": "#262d16",    # Deep forest green (text contrast)
        "border": "#b8d769",  # Accent green
    },
    "hopit-dark": {
        "name": "Hopit Dark",
        "hopit": "#35433f",   # Dark base slate-green (0% Tint)
        "user": "#727b79",    # Medium slate-green (30% Tint)
        "cwd": "#9aa19f",     # Medium-light slate-green (50% Tint)
        "git": "#c2c7c5",     # Light slate-green (70% Tint)
        "time": "#ebecec",    # Very light slate-green (90% Tint)
        "text": "#111111",    # Dark text for high contrast on light backgrounds
        "border": "#b8d769",  # Accent green
    },
    "catppuccin": {
        "name": "Catppuccin Mocha",
        "hopit": "#f38ba8", # Red / Pink
        "user": "#fab387",  # Peach
        "cwd": "#a6e3a1",   # Green
        "git": "#cba6f7",   # Mauve / Purple
        "time": "#89b4fa",  # Blue
        "text": "#11111b",  # Crust / Dark Charcoal
        "border": "#89b4fa",
    },
    "dracula": {
        "name": "Dracula",
        "hopit": "#ff5555", # Red
        "user": "#ffb86c",  # Orange
        "cwd": "#50fa7b",   # Green
        "git": "#bd93f9",   # Purple
        "time": "#8be9fd",  # Cyan
        "text": "#282a36",  # Dark Background contrast text
        "border": "#bd93f9",
    },
    "nord": {
        "name": "Nord",
        "hopit": "#bf616a", # Frost Red
        "user": "#d08770",  # Frost Orange
        "cwd": "#a3be8c",   # Frost Green
        "git": "#b48ead",   # Frost Purple
        "time": "#88c0d0",  # Frost Ice Blue
        "text": "#2e3440",  # Nord Polar Night Dark
        "border": "#88c0d0",
    },
    "tokyo-night": {
        "name": "Tokyo Night",
        "hopit": "#f7768e", # Red Pink
        "user": "#ff9e64",  # Orange
        "cwd": "#9ece6a",   # Green
        "git": "#bb9af7",   # Purple
        "time": "#7dcfff",  # Cyan Blue
        "text": "#16161e",  # Night Dark
        "border": "#bb9af7",
    },
    "one-dark": {
        "name": "One Dark Pro",
        "hopit": "#e06c75", # Red
        "user": "#d19a66",  # Orange
        "cwd": "#98c379",   # Green
        "git": "#c678dd",   # Purple
        "time": "#61afef",  # Blue
        "text": "#21252b",  # Dark Slate
        "border": "#61afef",
    },
    "cyberpunk": {
        "name": "Cyberpunk Neon",
        "hopit": "#ff0055", # Hot Red/Pink
        "user": "#ff8c00",  # Neon Amber
        "cwd": "#00ff66",   # Neon Green
        "git": "#9d00ff",   # Neon Violet
        "time": "#00e5ff",  # Neon Cyan
        "text": "#0a0a12",  # Deep Midnight
        "border": "#00e5ff",
    },
    "monokai": {
        "name": "Monokai Pro",
        "hopit": "#ff6188", # Pink Red
        "user": "#fc9867",  # Orange
        "cwd": "#a9dc76",   # Green
        "git": "#ab9df2",   # Purple
        "time": "#78dce8",  # Cyan Blue
        "text": "#19181a",  # Monokai Dark
        "border": "#ff6188",
    },
    "gruvbox": {
        "name": "Gruvbox Dark",
        "hopit": "#fb4934", # Bright Red
        "user": "#fe8019",  # Bright Orange
        "cwd": "#b8bb26",   # Bright Green
        "git": "#d3869b",   # Bright Purple
        "time": "#83a598",  # Bright Aqua
        "text": "#1d2021",  # Dark Hard
        "border": "#fe8019",
    },
    "solarized": {
        "name": "Solarized Dark",
        "hopit": "#dc322f", # Red
        "user": "#cb4b16",  # Orange
        "cwd": "#859900",   # Green
        "git": "#d33682",   # Magenta
        "time": "#268bd2",  # Blue
        "text": "#002b36",  # Solarized Base03
        "border": "#268bd2",
    },
    "synthwave": {
        "name": "Synthwave '84",
        "hopit": "#fe4450", # Neon Coral
        "user": "#ff7edb",  # Neon Pink
        "cwd": "#72f1b8",   # Neon Mint
        "git": "#b967ff",   # Electric Violet
        "time": "#36f9f6",  # Electric Cyan
        "text": "#241b2f",  # Deep Purple Night
        "border": "#f92aad",
    },
}

def get_active_theme_name() -> str:
    try:
        config_path = os.path.expanduser("~/.hopit-config.json")
        if os.path.exists(config_path):
            import json
            with open(config_path, "r") as f:
                cfg = json.load(f)
                theme = cfg.get("theme")
                if theme and theme in THEMES:
                    return theme
    except Exception:
        pass
    return "hopit"

def is_nerd_fonts_enabled() -> bool:
    try:
        config_path = os.path.expanduser("~/.hopit-config.json")
        if os.path.exists(config_path):
            import json
            with open(config_path, "r") as f:
                cfg = json.load(f)
                val = cfg.get("nerd_fonts", False)
                if isinstance(val, str):
                    return val.lower() == "true"
                return bool(val)
    except Exception:
        pass
    return False

# Build the Rich Console with the right color profile.
if IS_WINDOWS_TERMINAL:
    # Force truecolor Rich output inside Windows Terminal.
    console = Console(color_system="truecolor", force_terminal=True)
else:
    # Let Rich auto-detect (works on Linux; degrades gracefully on plain cmd).
    console = Console()

_theme_pushed = False

def update_console_theme(theme: dict):
    global _theme_pushed
    from rich.theme import Theme
    if _theme_pushed:
        try:
            console.pop_theme()
        except Exception:
            pass
    
    border_color = theme.get("border", "#b8d769")
    accent_color = theme.get("hopit", "#b8d769")
    git_color = theme.get("git", "#c6df87")
    
    console.push_theme(Theme({
        "cyan": border_color,
        "bold cyan": f"bold {border_color}",
        "magenta": git_color,
        "bold magenta": f"bold {git_color}",
        "green": accent_color,
        "bold green": f"bold {accent_color}",
    }))
    _theme_pushed = True

def get_active_theme() -> dict:
    theme = THEMES[get_active_theme_name()]
    update_console_theme(theme)
    return theme

# Set initial theme on the console
get_active_theme()

def get_syntax_theme() -> str:
    theme_name = get_active_theme_name()
    mapping = {
        "hopit": "monokai",
        "hopit-dark": "monokai",
        "catppuccin": "one-dark",
        "dracula": "dracula",
        "nord": "nord",
        "tokyo-night": "one-dark",
        "one-dark": "one-dark",
        "cyberpunk": "monokai",
        "monokai": "monokai",
        "gruvbox": "gruvbox-dark",
        "solarized": "solarized-dark",
        "synthwave": "monokai",
    }
    return mapping.get(theme_name, "monokai")



def detect_package_manager() -> str | None:
    """Detect by checking which manager binary is actually on PATH."""
    try:
        config_path = os.path.expanduser("~/.hopit-config.json")
        if os.path.exists(config_path):
            import json
            with open(config_path, "r") as f:
                cfg = json.load(f)
                if cfg.get("package_manager"):
                    return cfg["package_manager"]
    except Exception:
        pass

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
    try:
        config_path = os.path.expanduser("~/.hopit-config.json")
        if os.path.exists(config_path):
            import json
            with open(config_path, "r") as f:
                cfg = json.load(f)
                if cfg.get("editor"):
                    return cfg["editor"]
    except Exception:
        pass

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
