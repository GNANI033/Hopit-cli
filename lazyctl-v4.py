#!/usr/bin/env python3
"""
lazyctl — a fast TUI overlay that makes common Linux admin commands short and
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
from datetime import datetime
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
}

MANAGER_UPDATE_CMDS = {
    "apt-get": "apt-get update && apt-get upgrade -y",
    "dnf":     "dnf upgrade -y",
    "yum":     "yum update -y",
    "pacman":  "pacman -Syu --noconfirm",
    "zypper":  "zypper --non-interactive update",
    "apk":     "apk update && apk upgrade",
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
}


def detect_package_manager() -> Optional[str]:
    """Detect by checking which manager binary is actually on PATH —
    more reliable across distro derivatives than parsing os-release."""
    for mgr in ("apt-get", "dnf", "yum", "pacman", "zypper", "apk"):
        if shutil.which(mgr):
            return mgr
    return None


def read_os_pretty_name() -> str:
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


def load_installed_packages(manager: Optional[str]) -> List[str]:
    if not manager:
        return []
    cmd = MANAGER_PKG[manager]["installed_cmd"]
    lines = _run_lines(cmd, timeout=5)
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


def load_available_packages(manager: Optional[str]) -> List[str]:
    if not manager:
        return []
    cfg = MANAGER_PKG[manager]
    cmd = cfg.get("available_cmd")
    if not cmd:
        return []
    lines = _run_lines(cmd, timeout=20)
    if cfg.get("available_parse") == "yum":
        return sorted(set(_parse_yum_available(lines)))
    return sorted(set(l.strip() for l in lines if l.strip()))


def load_path_entries() -> List[str]:
    """List files and directories in the current working directory."""
    try:
        return sorted(os.listdir("."))
    except OSError:
        return []


def load_adapters() -> List[str]:
    """List network adapters on the system."""
    try:
        return sorted(os.listdir('/sys/class/net/'))
    except OSError:
        return []


# --------------------------------------------------------------------------
# Shell / alias helpers
# --------------------------------------------------------------------------

def detect_user_shell() -> str:
    """Return the user's login shell binary path (e.g. /bin/bash, /bin/zsh)."""
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
        tokens = shlex.split(line)
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
    shell_name = os.path.basename(shell)
    if shell_name == "fish":
        line = f"\nabbr --add {name} '{value}'\n"
    else:
        line = f"\nalias {name}='{value}'\n"
    with open(rc, "a") as f:
        f.write(line)
    return rc


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


# --------------------------------------------------------------------------
# Build the command registry
# --------------------------------------------------------------------------

def build_commands(manager: Optional[str], names) -> dict:
    """`names` is a dict of zero-arg callables returning current candidate
    lists: {"service": ..., "installed_pkg": ..., "available_pkg": ...}"""

    commands = {
        "status": Command(
            run=lambda svc: ["systemctl", "status", svc],
            desc="Show the status of a service",
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "start": Command(
            run=lambda svc: ["systemctl", "start", svc],
            desc="Start a service",
            needs_sudo=True,
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "stop": Command(
            run=lambda svc: ["systemctl", "stop", svc],
            desc="Stop a service",
            needs_sudo=True,
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "restart": Command(
            run=lambda svc: ["systemctl", "restart", svc],
            desc="Restart a service",
            needs_sudo=True,
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "logs": Command(
            run=lambda svc: ["journalctl", "-u", svc, "-n", "50", "--no-pager"],
            desc="Show recent logs for a service",
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "live": Command(
            run=lambda svc: ["journalctl", "-u", svc, "-f"],
            desc="Follow a service's logs live (Ctrl-C to stop)",
            mode="stream",
            arg_completions=names["service"],
            arg_completion_kind="service",
        ),
        "reboot": Command(
            run=lambda arg: ["shutdown", "-r", shutdown_time_arg(arg)],
            desc="Reboot now, in N minutes, or at HH:MM ('reboot 10', 'reboot 23:30')",
            needs_arg=False,
            needs_sudo=True,
            mode="stream",
        ),
        "shutdown": Command(
            run=lambda arg: ["shutdown", "-h", shutdown_time_arg(arg)],
            desc="Power off now, in N minutes, or at HH:MM ('shutdown 10')",
            needs_arg=False,
            needs_sudo=True,
            mode="stream",
        ),
        "cancel": Command(
            run=lambda _: ["shutdown", "-c"],
            desc="Cancel a pending scheduled shutdown/reboot",
            needs_arg=False,
            needs_sudo=True,
            mode="stream",
        ),
        "list": Command(
            run=lambda arg: ["ls", "-la", "--color=always"] if arg.lower() == "all"
                            else (["ls", "--color=always", arg] if arg else ["ls", "--color=always"]),
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
            run=lambda _: ["ip", "-c=always", "a"],
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
            run=lambda arg: [
                "bash", "-c",
                "ss -tulnp | awk -v port=" + shlex.quote(arg) + " "
                + shlex.quote(
                    'NR==1 || $0 ~ (":" port "[[:space:]]")'
                    if arg.isdigit() else
                    'NR==1 || tolower($0) ~ tolower(port)'
                )
            ],
            desc="Show which program is using a port (by port number or program name)",
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
            run=lambda _: ["bash", "-c", MANAGER_UPDATE_CMDS[manager]],
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

    matches = []
    word_lower = word.lower()
    for cand in cmd.arg_completions():
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
        desc = cmd.desc + ("  [dim](sudo)[/dim]" if cmd.needs_sudo else "")
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


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

def main():
    # Clear the terminal on startup for a clean slate
    os.system("clear")

    manager = detect_package_manager()
    shell = detect_user_shell()
    aliases = load_shell_aliases(shell)

    services = load_service_names()
    installed_pkgs = load_installed_packages(manager)               # fast enough to load synchronously
    available_pkgs_holder = BackgroundNames(
        lambda: load_available_packages(manager),
        start_immediately=False,
    )

    names = {
        "service": lambda: services,
        "installed_pkg": lambda: installed_pkgs,
        "available_pkg": available_pkgs_holder.get,
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
            
            sep = "\ue0b0"
            
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
                    ("class:git", f"  {branch} "),
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
            tokens = shlex.split(line)
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
                # Fallback: expand aliases then run as a raw shell command
                expanded = expand_aliases(line, aliases)
                try:
                    subprocess.run(expanded, shell=True, executable=shell)
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
                console.print(f"[dim]Saved to {rc_path} — run 'source {rc_path}' in a new terminal to apply globally.[/dim]")
            except KeyboardInterrupt:
                console.print("\n[dim]Cancelled.[/dim]")
            continue

        if name == "netconfig":
            if not rest:
                console.print("[yellow]Please specify an adapter, e.g., 'netconfig eth0'[/yellow]")
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
