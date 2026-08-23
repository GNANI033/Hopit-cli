import os
import re
import shlex
import shutil
import subprocess
from prompt_toolkit import prompt
from prompt_toolkit.completion import Completer, Completion, WordCompleter, DummyCompleter
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from hopit.config import IS_WINDOWS, IS_MACOS, console, with_privilege, get_active_theme
from hopit.loaders import load_adapters
from hopit.commands import BUILTIN_DESCRIPTIONS

MAX_ARG_COMPLETIONS = 80
MIN_ARG_PREFIX_CHARS = {
    "available_pkg": 2,
    "installed_pkg": 1,
    "service": 0,
    "path": 0,
}



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


def _match_start(cand: str, word: str) -> bool:
    """Case-insensitive and slash-agnostic startswith check for autocomplete candidates."""
    c = cand.strip('"\'').lower().replace('\\', '/')
    w = word.strip('"\'').lower().replace('\\', '/')
    return c.startswith(w)


def completion_matches(text_before_cursor: str, commands: dict, all_names: list[str]) -> list[str]:
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
    for cand in candidates:
        if _match_start(cand, word):
            matches.append(cand)
            if len(matches) >= MAX_ARG_COMPLETIONS:
                break
    return matches

def get_git_completions(words: list[str]) -> list[tuple[str, str]]:
    if len(words) == 2:
        git_subcommand_descs = {
            "status": "Show working tree status",
            "log": "Show commit logs",
            "branch": "List, create, or delete branches",
            "diff": "Show changes between commits/files",
            "add": "Add file contents to the index",
            "commit": "Record changes to the repository",
            "push": "Update remote refs and commits",
            "pull": "Fetch and integrate with remote",
            "checkout": "Switch branches or restore files",
            "clone": "Clone a repository into a new directory",
        }
        return [(cmd, desc) for cmd, desc in git_subcommand_descs.items()]

    subcmd = words[1].lower()
    word = words[-1]

    # 1. Get git status changes
    changed_files = {}
    try:
        res = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=1)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if len(line) > 3:
                    status = line[:2]
                    filename = line[3:].strip()
                    if "M" in status:
                        desc = "⚠️ modified"
                    elif "?" in status:
                        desc = "🆕 untracked"
                    elif "D" in status:
                        desc = "❌ deleted"
                    elif "A" in status:
                        desc = "🟢 staged"
                    else:
                        desc = "changed file"
                    changed_files[filename] = desc
    except Exception:
        pass

    # 2. Get git branch names
    branches = []
    try:
        res = subprocess.run(["git", "branch", "--format=%(refname:short)"], capture_output=True, text=True, timeout=1)
        if res.returncode == 0:
            branches = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    except Exception:
        pass

    if subcmd in ("add", "rm", "restore", "stage", "reset"):
        candidates = []
        for fn, desc in changed_files.items():
            candidates.append((fn, desc))
        from hopit.loaders import load_path_entries
        paths = load_path_entries(word)
        existing = {c[0] for c in candidates}
        for p in paths:
            if p not in existing:
                desc = "📁 folder" if os.path.isdir(p) else "📄 file"
                candidates.append((p, desc))
        return candidates

    elif subcmd in ("checkout", "switch"):
        candidates = []
        for b in branches:
            candidates.append((b, "🌿 branch"))
        for fn, desc in changed_files.items():
            candidates.append((fn, desc))
        from hopit.loaders import load_path_entries
        paths = load_path_entries(word)
        existing = {c[0] for c in candidates}
        for p in paths:
            if p not in existing:
                desc = "📁 folder" if os.path.isdir(p) else "📄 file"
                candidates.append((p, desc))
        return candidates

    elif subcmd in ("branch", "merge", "rebase", "push", "pull"):
        return [(b, "🌿 branch") for b in branches]

    elif subcmd == "diff":
        candidates = []
        for fn, desc in changed_files.items():
            candidates.append((fn, desc))
        from hopit.loaders import load_path_entries
        paths = load_path_entries(word)
        existing = {c[0] for c in candidates}
        for p in paths:
            if p not in existing:
                desc = "📁 folder" if os.path.isdir(p) else "📄 file"
                candidates.append((p, desc))
        return candidates

    return []


def get_active_sessions_list() -> list[tuple[str, str]]:
    candidates = []
    if IS_WINDOWS:
        try:
            proc = subprocess.run(["query", "user"], capture_output=True, text=True)
            if proc.returncode == 0:
                lines = proc.stdout.strip().splitlines()
                if len(lines) > 1:
                    header = lines[0]
                    idx_username = header.find("USERNAME")
                    idx_session = header.find("SESSIONNAME")
                    idx_id = header.find("ID")
                    for line in lines[1:]:
                        if line.strip():
                            username = line[idx_username:idx_session].strip().lstrip(">").strip()
                            session_id = line[idx_id:idx_id+5].strip()
                            candidates.append((session_id, f"Logon session for {username}"))
                            candidates.append((username, f"User {username}"))
        except Exception:
            pass
    else:
        try:
            proc = subprocess.run(["w", "-h"], capture_output=True, text=True)
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    if line.strip():
                        user = line[0:8].strip()
                        tty = line[8:17].strip()
                        candidates.append((tty, f"Logon session for {user}"))
        except Exception:
            try:
                proc = subprocess.run(["who"], capture_output=True, text=True)
                if proc.returncode == 0:
                    for line in proc.stdout.splitlines():
                        parts = line.split()
                        if len(parts) >= 2:
                            candidates.append((parts[1], f"Logon session for {parts[0]}"))
            except Exception:
                pass
        if shutil.which("tmux"):
            try:
                proc = subprocess.run(["tmux", "list-sessions"], capture_output=True, text=True)
                if proc.returncode == 0:
                    for line in proc.stdout.splitlines():
                        if ":" in line:
                            name = line.split(":")[0]
                            candidates.append((name, "Tmux session"))
            except Exception:
                pass
        if shutil.which("screen"):
            try:
                proc = subprocess.run(["screen", "-list"], capture_output=True, text=True)
                for line in proc.stdout.splitlines():
                    line_strip = line.strip()
                    if not line_strip or "There is a screen on" in line_strip or "Socket" in line_strip:
                        continue
                    parts = line_strip.split(maxsplit=2)
                    if len(parts) >= 1:
                        name = parts[0]
                        candidates.append((name, "Screen session"))
            except Exception:
                pass
    return candidates


def get_user_group_perm_completions(words: list[str], commands: dict, aliases_dict: dict = None) -> list[tuple[str, str]]:
    if len(words) < 2:
        return []
    cmd = words[0].lower()
    if cmd == "permissions":
        cmd = "permission"
    elif cmd == "drive":
        cmd = "disk"
    elif cmd == "compress":
        cmd = "archive"

    elif cmd in ("w", "who", "quser", "qwinsta"):
        if len(words) == 2:
            from hopit.loaders import load_users
            users = load_users()
            return [(u, "👤 user") for u in users]
        return []
    elif cmd == "query":
        if len(words) == 2:
            return [
                ("user", "List information about logged on users"),
                ("session", "List information about logon sessions"),
            ]
        elif len(words) == 3 and words[1].lower() == "user":
            from hopit.loaders import load_users
            users = load_users()
            return [(u, "👤 user") for u in users]
        return []
    elif cmd == "logoff":
        if len(words) == 2:
            return get_active_sessions_list()
        return []
    elif cmd == "loginctl":
        if len(words) == 2:
            return [
                ("list-sessions", "List active logon sessions"),
                ("terminate-session", "Terminate/disconnect a logon session"),
                ("kill-session", "Terminate/disconnect a logon session"),
            ]
        elif len(words) == 3 and words[1].lower() in ("terminate-session", "kill-session"):
            return get_active_sessions_list()
        return []
    
    if cmd == "user":
        if len(words) == 2:
            user_subs = {
                "add": "Add a new system user",
                "remove": "Delete a system user",
                "delete": "Delete a system user",
                "passwd": "Change a user's password",
                "password": "Change a user's password",
                "join": "Add a user to a group",
                "list": "List system users",
            }
            return [(k, v) for k, v in user_subs.items()]
            
        sub = words[1].lower()
        word = words[-1]
        
        if sub in ("remove", "delete", "passwd", "password"):
            from hopit.loaders import load_users
            users = load_users()
            return [(u, "👤 user") for u in users]
            
        elif sub == "join":
            if len(words) == 3:
                from hopit.loaders import load_groups
                groups = load_groups()
                return [(g, "👥 group") for g in groups]
            elif len(words) == 4:
                from hopit.loaders import load_users
                users = load_users()
                return [(u, "👤 user") for u in users]
                
    elif cmd == "group":
        if len(words) == 2:
            group_subs = {
                "add": "Add a new system group",
                "remove": "Delete a system group",
                "delete": "Delete a system group",
                "list": "List system groups",
            }
            return [(k, v) for k, v in group_subs.items()]
            
        sub = words[1].lower()
        if sub in ("remove", "delete"):
            from hopit.loaders import load_groups
            groups = load_groups()
            return [(g, "👥 group") for g in groups]
            
    elif cmd == "permission":
        if len(words) == 2:
            perm_subs = {
                "set": "Set read/write/execute permissions (chmod)",
                "owner": "Change owner of file/folder (chown)",
                "group": "Change group of file/folder (chgrp)",
            }
            return [(k, v) for k, v in perm_subs.items()]
            
        sub = words[1].lower()
        word = words[-1]
        if sub == "set":
            if len(words) == 3:
                return [("755", "rwxr-xr-x"), ("644", "rw-r--r--"), ("700", "rwx------"), ("+x", "make executable")]
            elif len(words) >= 4:
                from hopit.loaders import load_path_entries
                paths = load_path_entries(word)
                return [(p, "📁 folder" if os.path.isdir(p) else "📄 file") for p in paths]
                
        elif sub == "owner":
            if len(words) == 3:
                from hopit.loaders import load_users
                users = load_users()
                return [(u, "👤 user") for u in users]
            elif len(words) >= 4:
                from hopit.loaders import load_path_entries
                paths = load_path_entries(word)
                return [(p, "📁 folder" if os.path.isdir(p) else "📄 file") for p in paths]
                
        elif sub == "group":
            if len(words) == 3:
                from hopit.loaders import load_groups
                groups = load_groups()
                return [(g, "👥 group") for g in groups]
            elif len(words) >= 4:
                from hopit.loaders import load_path_entries
                paths = load_path_entries(word)
                return [(p, "📁 folder" if os.path.isdir(p) else "📄 file") for p in paths]

    elif cmd == "sessions":
        if len(words) == 2:
            return [
                ("list", "List active logon and terminal multiplexer sessions"),
                ("kill", "Terminate/disconnect a session: sessions kill <session_id/tty/name>"),
            ]
        elif len(words) == 3 and words[1].lower() in ("kill", "remove", "delete", "disconnect"):
            return get_active_sessions_list()

    elif cmd == "firewall":
        if len(words) == 2:
            fw_subs = {
                "status": "Check active firewall rule profiles and status",
                "allow": "Allow inbound traffic on port",
                "block": "Block inbound traffic on port",
                "delete": "Delete a specific firewall rule by ID or port",
            }
            return [(k, v) for k, v in fw_subs.items()]

        sub = words[1].lower()
        if sub in ("delete", "remove"):
            if len(words) == 3:
                try:
                    from hopit.firewall import parse_firewall_rules
                    rules = parse_firewall_rules()
                    return [(str(r["id"]), f"Rule #{r['id']}: {r['name']} ({r['port']}/{r['proto']})") for r in rules]
                except Exception:
                    return [("1", "Rule #1"), ("2", "Rule #2")]
        elif sub in ("allow", "block", "deny"):
            if len(words) == 3:
                return [
                    ("<port>", "Or type any custom port number / range of your choosing"),
                    ("80", "HTTP Web Server"),
                    ("443", "HTTPS Secure Web"),
                    ("22", "SSH Remote Access"),
                    ("8080", "Web Alt / App Server"),
                    ("3306", "MySQL Database"),
                    ("5432", "PostgreSQL Database"),
                    ("27017", "MongoDB Database"),
                    ("6379", "Redis Cache"),
                ]
            elif len(words) == 4:
                return [
                    ("tcp", "Transmission Control Protocol (Default)"),
                    ("udp", "User Datagram Protocol"),
                    ("both", "Both TCP and UDP protocols"),
                ]
            elif len(words) == 5:
                from hopit.loaders import load_adapters
                ifaces = load_adapters()
                res = [("all", "All network interfaces")]
                for i in ifaces:
                    if i != "all":
                        res.append((i, "🌐 network adapter"))
                return res

    elif cmd in ("disk", "drive"):
        if len(words) == 2:
            disk_subs = {
                "list":    "List physical drives and volumes",
                "usage":   "Check disk space utilization",
                "mount":   "Mount a drive or partition",
                "unmount": "Unmount a mounted volume",
                "check":   "Perform filesystem integrity check",
                "health":  "Check disk health / SMART status",
                "format":  "Format a partition (Destructive)",
            }
            return [(k, v) for k, v in disk_subs.items()]

        sub = words[1].lower()
        word = words[-1]

        if sub == "usage":
            # disk usage maps to 'df -h <path>' — only directories are meaningful here
            from hopit.loaders import load_path_entries
            paths = load_path_entries(word)
            return [(p, "📁 folder") for p in paths if p.endswith("/") or os.path.isdir(p)]

        elif sub in ("check", "health"):
            # Show mounted filesystems + block devices for fsck/chkdsk/smart
            from hopit.loaders import load_mount_points, load_block_devices
            if len(words) == 3:
                candidates = []
                for mnt, desc in load_mount_points():
                    candidates.append((mnt, f"🔧 {desc}"))
                for dev, desc in load_block_devices():
                    candidates.append((dev, f"💾 {desc}"))
                return candidates
            return []

        elif sub == "format":
            from hopit.loaders import load_block_devices
            if len(words) == 3:
                # Show block devices
                return [(dev, f"💾 {desc} (Destructive)") for dev, desc in load_block_devices()]
            elif len(words) == 4:
                # Show filesystems
                fs = ["ext4", "ntfs", "vfat", "exfat", "btrfs"] if not IS_MACOS else ["APFS", "ExFAT", "MS-DOS", "HFS+"]
                return [(f, "📂 filesystem") for f in fs]
            return []

        elif sub == "unmount":
            # Show only currently mounted filesystems
            from hopit.loaders import load_mount_points
            if len(words) == 3:
                return [(mnt, f"💿 {desc}") for mnt, desc in load_mount_points()]
            return []

        elif sub == "mount":
            from hopit.loaders import load_block_devices, load_path_entries
            if len(words) == 3:
                # <device> arg — show block devices
                return [(dev, f"💾 {desc}") for dev, desc in load_block_devices()]
            elif len(words) == 4:
                # <target> (mount point) arg — show directory navigation
                paths = load_path_entries(word)
                return [(p, "📁 mount target") for p in paths if p.endswith("/") or os.path.isdir(p)]
            return []

    elif cmd in ("archive", "compress"):
        if len(words) == 2:
            arch_subs = {
                "create": "Create a compressed archive (.zip, .tar.gz)",
                "extract": "Extract files from a compressed archive",
            }
            return [(k, v) for k, v in arch_subs.items()]
        elif len(words) >= 3:
            from hopit.loaders import load_path_entries
            word = words[-1]
            paths = load_path_entries(word)
            return [(p, "📁 folder" if os.path.isdir(p) else "📄 file") for p in paths]

    elif cmd == "create":
        if len(words) == 2:
            create_subs = {
                "folder": "Create a new directory (including parent directories)",
                "file": "Create a new empty file",
                "shortcut": "Create a CLI shortcut (alias)",
                "venv": "Create a new Python virtual environment",
            }
            return [(k, v) for k, v in create_subs.items()]
        elif len(words) >= 3:
            if words[1].lower() == "shortcut":
                return []
            from hopit.loaders import load_path_entries
            word = words[-1]
            paths = load_path_entries(word)
            return [(p, "📁 folder" if os.path.isdir(p) else "📄 file") for p in paths]

    elif cmd == "enter":
        if len(words) == 2:
            return [("venv", "Enter (activate) a Python virtual environment")]
        elif len(words) >= 3 and words[1].lower() == "venv":
            from hopit.loaders import load_path_entries
            word = words[-1]
            paths = load_path_entries(word)
            result = []
            for p in paths:
                if not os.path.isdir(p):
                    continue  # skip files — venvs are always directories
                is_venv = os.path.isfile(os.path.join(p, "pyvenv.cfg"))
                meta = "🐍 venv" if is_venv else "📁 folder"
                result.append((p, meta))
            return result

    elif cmd == "exit":
        if len(words) == 2:
            return [("venv", "Exit (deactivate) the current Python virtual environment")]

    elif cmd == "remove":
        if len(words) == 2:
            from hopit.loaders import load_path_entries
            word = words[-1]
            paths = load_path_entries(word)
            completions = [(p, "📁 folder" if os.path.isdir(p) else "📄 file") for p in paths]
            if "shortcut".startswith(word.lower()):
                completions.insert(0, ("shortcut", "Remove a CLI shortcut (alias)"))
            return completions
        elif len(words) == 3 and words[1].lower() == "shortcut":
            from hopit.main import shell_rc_file
            rc = shell_rc_file(os.environ.get("SHELL", "/bin/bash"))
            aliases = []
            if aliases_dict:
                for k in aliases_dict.keys():
                    aliases.append((k, "CLI shortcut (alias)"))
            if os.path.isfile(rc):
                try:
                    with open(rc, "r") as f:
                        for line in f:
                            line = line.strip()
                            name = None
                            if line.startswith("alias "):
                                name = line[6:].split("=")[0]
                            elif line.startswith("doskey "):
                                name = line[7:].split("=")[0]
                            elif line.startswith("abbr --add "):
                                name = line[11:].split(" ")[0]
                            if name and not any(a[0] == name for a in aliases):
                                aliases.append((name, "CLI shortcut (alias)"))
                except Exception:
                    pass
            return aliases
        elif len(words) >= 2 and words[1].lower() != "shortcut":
            from hopit.loaders import load_path_entries
            word = words[-1]
            paths = load_path_entries(word)
            return [(p, "📁 folder" if os.path.isdir(p) else "📄 file") for p in paths]


    elif cmd == "show":
        if len(words) == 2:
            show_subs = {
                "file": "Show contents of a file (cat)",
                "start": "Show the first N lines of a file (head)",
                "end": "Show the last N lines of a file (tail)",
                "tree": "Show directory structure in a tree (tree)",
                "env": "View or filter environment variables (env)",
                "history": "Show the session command history (history)",
                "arp": "View Address Resolution Protocol (ARP) table",
                "mac": "Display MAC addresses of active network interfaces",
                "gateway": "Display system default gateway IP address",
                "ip": "Show IP addresses and network interfaces",
                "route": "View the system network routing table",
                "hostname": "View or change the system's host name",
                "shortcut": "View all CLI shortcuts (aliases) and their state",
            }
            return [(k, v) for k, v in show_subs.items()]
        elif len(words) >= 3:
            sub = words[1].lower()
            if sub in ("file", "start", "end", "tree"):
                from hopit.loaders import load_path_entries
                word = words[-1]
                paths = load_path_entries(word)
                return [(p, "📁 folder" if os.path.isdir(p) else "📄 file") for p in paths]

    elif cmd == "lookup":
        if len(words) == 2:
            lookup_subs = {
                "all": "Consolidated diagnostics (DNS, Ping, HTTP, Traceroute)",
                "A": "Query DNS A records (IPv4 addresses)",
                "AAAA": "Query DNS AAAA records (IPv6 addresses)",
                "CNAME": "Query DNS CNAME records (canonical names)",
                "MX": "Query DNS MX records (mail exchangers)",
                "TXT": "Query DNS TXT records (text records)",
                "NS": "Query DNS NS records (name servers)",
            }
            return [(k, v) for k, v in lookup_subs.items()]

    elif cmd == "netconfig":
        if len(words) == 2:
            from hopit.loaders import load_adapters
            adapters = load_adapters()
            ans = [(adapter, "🌐 adapter") for adapter in adapters]
            ans.append(("dhcp", "Manage DHCP lease"))
            ans.append(("reset", "Reset adapter manual changes"))
            return ans
        elif len(words) == 3 and words[1].lower() == "reset":
            from hopit.loaders import load_adapters
            adapters = load_adapters()
            return [(adapter, "🌐 adapter") for adapter in adapters]
        elif len(words) == 3 and words[1].lower() == "dhcp":
            return [("release", "Release DHCP lease"), ("renew", "Renew DHCP lease")]
        elif len(words) == 4 and words[1].lower() == "dhcp" and words[2].lower() in ("release", "renew"):
            from hopit.loaders import load_adapters
            adapters = load_adapters()
            return [(adapter, "🌐 adapter") for adapter in adapters]
            
    elif cmd == "schedule":
        if len(words) == 2:
            schedule_subs = {
                "list": "List all scheduled tasks",
                "add": "Add a new scheduled task interactively",
                "remove": "Remove an existing scheduled task",
                "edit": "Edit the raw scheduled tasks file",
                "-/": "Pass native arguments directly (e.g. -l, /query)",
            }
            return [(k, v) for k, v in schedule_subs.items()]
            
    elif cmd == "crontab":
        if len(words) == 2:
            return [
                ("-l", "List your scheduled cron jobs"),
                ("-e", "Edit your cron jobs interactively"),
                ("-r", "Remove all of your cron jobs"),
            ]
            
    elif cmd == "schtasks":
        if len(words) == 2:
            return [
                ("/query", "List all scheduled tasks"),
                ("/create", "Create a new scheduled task"),
                ("/delete", "Delete an existing scheduled task"),
            ]
            
        sub = words[1].lower()
        if sub == "remove" and len(words) == 3:
            try:
                from hopit.schedule import get_schedule_names
                names = get_schedule_names()
                return [(n, "🕒 scheduled task") for n in names]
            except Exception:
                pass
        elif sub == "add":
            if len(words) == 3:
                return [("<task_name>", "Name of the task to add (no spaces)")]
            elif len(words) == 4:
                return [("<command>", "Shell command to execute")]
            elif len(words) == 5:
                return [
                    ("Minute", "Every minute"),
                    ("Hourly", "Every hour"),
                    ("Daily", "Every day at midnight"),
                    ("Weekly", "Every Sunday"),
                    ("Monthly", "Every 1st of the month"),
                    ("Reboot", "At system startup"),
                    ('"* * * * *"', "Custom cron expression")
                ]
            
    return []


def get_k8s_completions(words: list[str]) -> list[tuple[str, str]]:
    """
    Dynamic Kubernetes completions.
    Returns a list of (value, display_meta) tuples.
    """
    from hopit.kubernetes import (
        K8S_TOP_COMPLETIONS, KUBECTL_SUBCOMMANDS, KUBECTL_RESOURCE_TYPES,
        load_pods, load_namespaces, load_deployments, load_services,
        load_nodes, load_contexts, load_containers_in_pod, kubectl_available
    )

    cmd = words[0].lower()  # 'k8s', 'kubernetes', or 'kubectl'
    n = len(words)

    # ─── k8s / kubernetes ──────────────────────────────────────
    if cmd in ("k8s", "kubernetes"):
        if n == 2:
            # Show all simple-english verbs
            return list(K8S_TOP_COMPLETIONS)

        sub = words[1].lower()
        sub2 = (words[1] + " " + words[2]).lower() if n >= 3 else ""

        # After "logs / follow / exec / sh / pod info / delete pod" → show pods
        if sub in ("logs", "follow", "tail", "exec", "sh") and n == 3:
            return [(p, "📦 pod") for p in load_pods()]

        if sub2 in ("pod info", "delete pod") and n == 4:
            return [(p, "📦 pod") for p in load_pods()]

        # After "restart / scale / deployment info / rollout status/history/undo" → deployments
        if sub in ("restart", "scale") and n == 3:
            return [(d, "🚀 deployment") for d in load_deployments()]

        if sub2 in ("deployment info", "rollout status", "rollout history", "rollout undo") and n == 4:
            return [(d, "🚀 deployment") for d in load_deployments()]

        # After "service info" → services
        if sub2 == "service info" and n == 4:
            return [(s, "🌐 service") for s in load_services()]

        # After "node info / drain / cordon / uncordon" → nodes
        if sub in ("drain", "cordon", "uncordon") and n == 3:
            return [(node, "🖥️  node") for node in load_nodes()]
        if sub2 == "node info" and n == 4:
            return [(node, "🖥️  node") for node in load_nodes()]

        # After "use context / switch context" → contexts
        if sub in ("use context", "switch context") and n == 4:
            return [(ctx, "🌍 context") for ctx in load_contexts()]

        # After "apply / delete" with file path
        if sub in ("apply", "delete") and n >= 3:
            from hopit.loaders import load_path_entries
            word = words[-1]
            paths = load_path_entries(word)
            return [(p, "📄 manifest" if p.endswith((".yaml", ".yml", ".json")) else ("📁 folder" if os.path.isdir(p) else "📄 file")) for p in paths]

        # After "get" → resource types
        if sub == "get" and n == 3:
            return list(KUBECTL_RESOURCE_TYPES)

        # After "forward / port-forward" → pods
        if sub in ("forward", "portforward") and n == 3:
            return [(p, "📦 pod — then specify local:remote ports") for p in load_pods()]

        # After "namespaces / create namespace / delete namespace" w/arg → namespace list
        if sub in ("delete namespace", "remove namespace") and n == 4:
            return [(ns, "📁 namespace") for ns in load_namespaces()]

        return []

    # ─── kubectl (raw) ───────────────────────────────────────────
    if cmd == "kubectl":
        if n == 2:
            return list(KUBECTL_SUBCOMMANDS)

        sub = words[1].lower()
        ns_flag = False
        ns_val = "default"
        # detect -n / --namespace flags anywhere in the word list
        for i, w in enumerate(words):
            if w in ("-n", "--namespace") and i + 1 < len(words):
                ns_val = words[i + 1]
                ns_flag = True

        if sub in ("get", "describe", "delete", "edit", "patch", "label", "annotate") and n == 3:
            return list(KUBECTL_RESOURCE_TYPES)

        # 4th word completions: after "kubectl get <resource>" or "kubectl describe pods"
        if sub in ("get", "describe", "delete", "edit", "logs", "exec", "attach") and n >= 4:
            resource = words[2].lower().rstrip("s")  # rough singular
            if resource in ("pod", ""):
                return [(p, "📦 pod") for p in load_pods(ns_val)]
            elif resource in ("deployment", "deploy"):
                return [(d, "🚀 deployment") for d in load_deployments(ns_val)]
            elif resource in ("service", "svc"):
                return [(s, "🌐 service") for s in load_services(ns_val)]
            elif resource in ("node",):
                return [(node, "🖥️  node") for node in load_nodes()]
            elif resource in ("namespace", "ns"):
                return [(ns, "📁 namespace") for ns in load_namespaces()]

        if sub in ("-n", "--namespace") and n == 3:
            return [(ns, "📁 namespace") for ns in load_namespaces()]

        if sub in ("config",) and n == 3:
            return [
                ("get-contexts",  "List all kubectl contexts"),
                ("use-context",   "Switch active kubectl context"),
                ("current-context","Show current context"),
                ("set-cluster",   "Set cluster configuration"),
                ("set-credentials","Set user credentials"),
                ("view",          "Display merged kubeconfig"),
            ]
        if sub == "config" and n == 4 and words[2].lower() in ("use-context",):
            return [(ctx, "🌍 context") for ctx in load_contexts()]

        if sub in ("apply", "create", "replace", "delete") and n >= 3:
            # After -f flag, show file completions
            if words[-2] in ("-f", "--filename", "-k", "--kustomize"):
                from hopit.loaders import load_path_entries
                word = words[-1]
                paths = load_path_entries(word)
                return [(p, "📄 manifest" if p.endswith((".yaml", ".yml", ".json")) else ("📁 folder" if os.path.isdir(p) else "📄 file")) for p in paths]
            if n == 3:
                return [("-f", "Specify file, directory, or URL"), ("--filename", "Specify file, directory, or URL"), ("-k", "Kustomize directory")]

        if sub == "rollout" and n == 3:
            return [
                ("status",  "Show rollout status of a deployment"),
                ("history", "View rollout revision history"),
                ("undo",    "Roll back to a previous revision"),
                ("pause",   "Pause a rollout"),
                ("resume",  "Resume a paused rollout"),
                ("restart", "Restart a rollout"),
            ]
        if sub == "rollout" and n == 4:
            return [(f"deployment/{d}", "🚀 deployment") for d in load_deployments(ns_val)] + \
                   [(f"statefulset/{d}", "📆 statefulset") for d in _run_silent_list("statefulsets", ns_val)]

        if sub == "scale" and n == 3:
            return [(f"deployment/{d}", "🚀 deployment") for d in load_deployments(ns_val)]

        if sub == "port-forward" and n == 3:
            return [(p, "📦 pod") for p in load_pods(ns_val)]

        if sub == "top" and n == 3:
            return [("pods", "Show pod CPU/Memory usage"), ("nodes", "Show node CPU/Memory usage")]

        return []

    return []


def _run_silent_list(resource: str, namespace: str = "default") -> list[str]:
    """Generic: kubectl get <resource> -n <ns> and return names."""
    import subprocess, shutil
    if not shutil.which("kubectl"):
        return []
    try:
        r = subprocess.run(
            ["kubectl", "get", resource, "-n", namespace, "--no-headers",
             "-o", "custom-columns=NAME:.metadata.name"],
            capture_output=True, text=True, timeout=4
        )
        return [l.strip() for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        return []


class LazyCompleter(Completer):
    def __init__(self, commands: dict, aliases: dict = None):
        self.commands = commands
        self.aliases = aliases or {}
        aliases_to_hide = {
            "permissions", "drive", "compress", "ps", "where", "findcommand",
            "adduser", "deluser", "addgroup", "delgroup", "viewstart", "viewend",
            "scrollfile", "findfile", "findtext", "kubernetes", "doskey"
        }
        self.all_names = [k for k in commands if k not in aliases_to_hide] + list(BUILTIN_DESCRIPTIONS.keys())

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        words = text.split(" ")
        words = [w for w in words[:-1] if w] + [words[-1]]
        word = words[-1]

        # Check if we are completing for git or structured subcommands
        if len(words) > 1:
            head = words[0].lower()
            resolved, _ = resolve_command(self.all_names, head)
            
            # Map aliases to primary command names
            if head == "permissions":
                resolved = "permission"
            elif head == "drive":
                resolved = "disk"
            elif head == "compress":
                resolved = "archive"
            elif head == "ps":
                resolved = "processes"
            elif head == "where" or head == "findcommand":
                resolved = "which"
            elif head == "adduser":
                resolved = "useradd"
            elif head == "deluser":
                resolved = "userdel"
            elif head == "addgroup":
                resolved = "groupadd"
            elif head == "delgroup":
                resolved = "groupdel"
            elif head == "viewstart":
                resolved = "head"
            elif head == "viewend":
                resolved = "tail"
            elif head == "scrollfile":
                resolved = "less"
            elif head == "findfile":
                resolved = "find"
            elif head == "findtext":
                resolved = "grep"
            elif head == "whereami":
                resolved = "pwd"

            if resolved == "git":
                git_candidates = get_git_completions(words)
                matches = []
                for cand, meta in git_candidates:
                    if _match_start(cand, word):
                        matches.append((cand, meta))
                        if len(matches) >= MAX_ARG_COMPLETIONS:
                            break
                for match, meta in matches:
                    yield Completion(match, start_position=-len(word), display_meta=meta)
                return
            elif resolved == "config":
                from hopit.config import THEMES
                if len(words) == 2:
                    candidates = [("set", "Change a configuration setting"), ("reset", "Reset configuration to defaults")]
                elif len(words) == 3 and words[1].lower() == "set":
                    candidates = [
                        ("theme", "Color scheme appearance"),
                        ("editor", "Default text editor"),
                        ("package_manager", "Default package manager override"),
                    ]
                elif len(words) == 4 and words[1].lower() == "set" and words[2].lower() == "theme":
                    candidates = [(k, t["name"]) for k, t in THEMES.items()]
                elif len(words) == 4 and words[1].lower() == "set" and words[2].lower() == "editor":
                    candidates = [("nano", "Nano text editor"), ("vim", "Vim text editor"), ("code", "VS Code editor"), ("micro", "Micro text editor")]
                elif len(words) == 4 and words[1].lower() == "set" and words[2].lower() == "package_manager":
                    candidates = [("apt-get", "APT package manager"), ("dnf", "DNF package manager"), ("pacman", "Pacman package manager"), ("brew", "Homebrew"), ("winget", "Windows Package Manager")]
                else:
                    candidates = []

                matches = []
                for cand, meta in candidates:
                    if _match_start(cand, word):
                        matches.append((cand, meta))
                        if len(matches) >= MAX_ARG_COMPLETIONS:
                            break
                for match, meta in matches:
                    yield Completion(match, start_position=-len(word), display_meta=meta)
                return
            elif resolved in ("user", "group", "permission", "firewall", "disk", "archive", "show", "lookup", "enter", "exit", "remove", "netconfig", "schedule", "crontab", "schtasks", "sessions", "w", "who", "quser", "qwinsta", "query", "logoff", "loginctl"):
                candidates = get_user_group_perm_completions(words, self.commands, getattr(self, "aliases", None))
                matches = []
                for cand, meta in candidates:
                    if _match_start(cand, word):
                        matches.append((cand, meta))
                        if len(matches) >= MAX_ARG_COMPLETIONS:
                            break
                for match, meta in matches:
                    yield Completion(match, start_position=-len(word), display_meta=meta)
                return
            elif resolved in ("k8s", "kubernetes", "kubectl"):
                candidates = get_k8s_completions(words)
                matches = []
                for cand, meta in candidates:
                    if _match_start(cand, word):
                        matches.append((cand, meta))
                        if len(matches) >= MAX_ARG_COMPLETIONS:
                            break
                for match, meta in matches:
                    yield Completion(match, start_position=-len(word), display_meta=meta)
                return
            elif resolved == "create":
                if len(words) >= 3:
                    sub = words[1].lower() if len(words) > 1 else ""
                    if sub == "shortcut":
                        yield Completion(
                            "",
                            start_position=0,
                            display="💡 Press ENTER to open the shortcut wizard",
                            display_meta="info"
                        )
                        return
                    elif sub not in ("folder", "file", "venv"):
                        pass
                    else:
                        yield Completion(
                            "",
                            start_position=0,
                            display="💡 Enter name to create here or full path",
                            display_meta="info"
                        )
                candidates = get_user_group_perm_completions(words, self.commands, self.aliases)
                matches = []
                for cand, meta in candidates:
                    if _match_start(cand, word):
                        matches.append((cand, meta))
                        if len(matches) >= MAX_ARG_COMPLETIONS:
                            break
                for match, meta in matches:
                    yield Completion(match, start_position=-len(word), display_meta=meta)
                return

        matches = completion_matches(text, self.commands, self.all_names)

        # Detect if we're completing a path-type argument
        arg_kind = None
        if len(words) > 1:
            head = words[0].lower()
            resolved, _ = resolve_command(self.all_names, head)
            if resolved and resolved in self.commands:
                arg_kind = self.commands[resolved].arg_completion_kind

        git_subcommand_descs = {
            "status": "Show working tree status",
            "log": "Show commit logs",
            "branch": "List, create, or delete branches",
            "diff": "Show changes between commits, commit and working tree, etc.",
            "add": "Add file contents to the index",
            "commit": "Record changes to the repository",
            "push": "Update remote refs along with associated objects",
            "pull": "Fetch from and integrate with another repository or a local branch",
            "checkout": "Switch branches or restore working tree files",
            "clone": "Clone a repository into a new directory",
        }

        for match in matches:
            if len(words) > 1:
                # Completing an argument
                if arg_kind == "path":
                    if os.path.isdir(match):
                        meta = "📁 folder"
                    else:
                        meta = "📄 file"
                elif arg_kind == "git_subcommand":
                    meta = git_subcommand_descs.get(match.lower(), "git subcommand")
                else:
                    meta = arg_kind if arg_kind else ""
            else:
                # Completing a command name
                cmd = self.commands.get(match)
                meta = cmd.desc if cmd else BUILTIN_DESCRIPTIONS.get(match, "")
            yield Completion(match, start_position=-len(word), display_meta=meta)


def render_result(
    proc: subprocess.CompletedProcess,
    label: str,
    cmd_name: str = None,
    cmd_arg: str = None,
    show_cmd: bool = False,
):
    output = (proc.stdout or "") + (proc.stderr or "")
    output = output.rstrip("\n")

    theme = get_active_theme()
    border = theme.get("border", "cyan")

    if output:
        lines = output.splitlines()
        styled_lines = []
        for i, line in enumerate(lines):
            line_text = Text.from_ansi(line)
            is_header = False
            if i == 0 and len(line.strip()) > 0:
                header_words = ["netid", "state", "recv-q", "send-q", "local", "peer", "command", "pid", "user", "fd", "type", "device", "node", "name", "status", "ports", "service", "displayname"]
                line_lower = line.lower()
                if any(hw in line_lower for hw in header_words) or (len(line.split()) >= 3 and all(w[0].isupper() or w[0].isdigit() or not w[0].isalpha() for w in line.split() if w)):
                    is_header = True
            
            if is_header:
                line_text.stylize("bold cyan")
            else:
                line_text.highlight_regex(r"(?i)\b(active \(running\)|running|up|online|enabled|listen|listening|estab|established|success|succeeded)\b", "bold green")
                line_text.highlight_regex(r"(?i)\b(inactive \(dead\)|inactive|dead|disabled|down|offline|unconn|stopped)\b", "bold yellow")
                line_text.highlight_regex(r"(?i)\b(failed|error|severe|critical|stopped \(failed\))\b", "bold red")
                line_text.highlight_regex(r"^\s*[A-Za-z_-]+(?:\s+[A-Za-z_-]+)*\s*:", "bold cyan")
                line_text.highlight_regex(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\b", "cyan")
                line_text.highlight_regex(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(/\d+)?(:\d+)?\b", "green")
                line_text.highlight_regex(r"\b([a-fA-F0-9:]+::[a-fA-F0-9:]*(/\d+)?|::[a-fA-F0-9:]*(/\d+)?)\b", "green")
                line_text.highlight_regex(r"\b[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}\b", "yellow")
                line_text.highlight_regex(r"\b(\*|\[::\]|localhost|\[[a-fA-F0-9:]+\]):\d+\b", "green")
                line_text.highlight_regex(r"\bpid=\d+\b", "magenta")
                line_text.highlight_regex(r"users:\(\(.*?\)\)", "magenta")
                
            styled_lines.append(line_text)
        content = Text("\n").join(styled_lines)
    else:
        if proc.returncode in (0, 3):
            if cmd_name == "start":
                content_str = f"Service '{cmd_arg}' started successfully." if cmd_arg else "Service started successfully."
            elif cmd_name == "stop":
                content_str = f"Service '{cmd_arg}' stopped successfully." if cmd_arg else "Service stopped successfully."
            elif cmd_name == "restart":
                content_str = f"Service '{cmd_arg}' restarted successfully." if cmd_arg else "Service restarted successfully."
            elif cmd_name == "install":
                content_str = f"Package '{cmd_arg}' installed successfully." if cmd_arg else "Package installed successfully."
            elif cmd_name == "uninstall":
                content_str = f"Package '{cmd_arg}' uninstalled successfully." if cmd_arg else "Package uninstalled successfully."
            else:
                content_str = "Command executed successfully."
            content = Text(content_str, style="bold green")
        else:
            if cmd_name in ("start", "stop", "restart", "install", "uninstall") and cmd_arg:
                content_str = f"Failed to run action '{cmd_name}' on '{cmd_arg}' (exit code: {proc.returncode})."
            else:
                content_str = f"Command failed (exit code: {proc.returncode})."
            content = Text(content_str, style="bold red")

    title = label if show_cmd else None
    console.print(Panel(content, title=title, border_style=border, expand=False))


def print_help(commands: dict, manager: str | None):
    # Subcommands mapping for nested/indented display
    SUBCOMMANDS = {
        "git": [
            ("log", "Show commit history with interactive scrolling"),
            ("status", "Show working tree status"),
            ("diff", "Show changes between commits/working tree"),
            ("branch", "List, create, or delete branches"),
            ("add", "Stage files for commit: git add <files>"),
            ("commit", "Record staged changes: git commit <msg>"),
            ("push", "Update remote refs"),
            ("pull", "Fetch and integrate with local branch"),
        ],
        "create": [
            ("folder", "Create a folder (mkdir -p): create folder <path>"),
            ("file", "Create an empty file: create file <path>"),
            ("shortcut", "Create a CLI shortcut (alias): create shortcut"),
            ("venv", "Create a Python virtual environment: create venv <path>"),
        ],
        "enter": [
            ("venv", "Enter (activate) a virtual environment: enter venv <path>"),
        ],
        "show": [
            ("file", "Show content of a file: show file <path>"),
            ("start", "Show first N lines of a file: show start [-n lines] <path>"),
            ("end", "Show last N lines of a file: show end [-n lines] <path>"),
            ("tree", "Show directory structure: show tree [path]"),
            ("env", "View environment variables: show env [var]"),
            ("history", "View shell command history: show history"),
            ("arp", "View Address Resolution Protocol (ARP) table: show arp [args]"),
            ("mac", "Display MAC addresses of active network interfaces: show mac"),
            ("gateway", "Display system default gateway IP address: show gateway"),
            ("ip", "Show IP addresses and network interfaces: show ip"),
            ("route", "View the system network routing table: show route [args]"),
            ("hostname", "View or change the system's host name: show hostname [new_name]"),
        ],
        "lookup": [
            ("all", "Perform consolidated diagnostics: lookup all <host_or_ip>"),
            ("A", "Query DNS A records (IPv4 addresses): lookup A <host>"),
            ("AAAA", "Query DNS AAAA records (IPv6 addresses): lookup AAAA <host>"),
            ("CNAME", "Query DNS CNAME records (canonical names): lookup CNAME <host>"),
            ("MX", "Query DNS MX records (mail exchangers): lookup MX <host>"),
            ("TXT", "Query DNS TXT records (text records): lookup TXT <host>"),
            ("NS", "Query DNS NS records (name servers): lookup NS <host>"),
        ],
        "netconfig": [
            ("reset", "Clear manual IP/DNS changes and restore DHCP: netconfig reset <adapter>"),
            ("dhcp release", "Release DHCP lease for an adapter: netconfig dhcp release <adapter>"),
            ("dhcp renew", "Renew DHCP lease for an adapter: netconfig dhcp renew <adapter>"),
        ],
        "k8s": [
            ("pods",            "List pods in current namespace"),
            ("pods all",        "List pods across ALL namespaces"),
            ("pod info",        "Describe a pod: k8s pod info <name>"),
            ("logs",            "Show pod logs: k8s logs <pod>"),
            ("follow",          "Follow live pod logs: k8s follow <pod>"),
            ("exec",            "Bash into a pod: k8s exec <pod>"),
            ("deployments",     "List all deployments"),
            ("scale",           "Scale deployment: k8s scale <deploy> <replicas>"),
            ("restart",         "Restart a deployment: k8s restart <deploy>"),
            ("rollout status",  "Check rollout: k8s rollout status <deploy>"),
            ("rollout undo",    "Rollback: k8s rollout undo <deploy>"),
            ("services",        "List all services"),
            ("nodes",           "List all cluster nodes"),
            ("namespaces",      "List all namespaces"),
            ("apply",           "Apply manifest: k8s apply <file.yaml>"),
            ("delete",          "Delete resources: k8s delete <file.yaml>"),
            ("top pods",        "Show pod CPU/Memory usage"),
            ("events",          "Show recent cluster events"),
            ("contexts",        "List all kubectl contexts"),
            ("use context",     "Switch cluster context: k8s use context <name>"),
            ("forward",         "Port-forward: k8s forward <pod> <local>:<remote>"),
            ("cluster info",    "Show Kubernetes cluster API endpoint"),
        ],
        "kubectl": [
            ("get pods",                  "List pods: kubectl get pods [-n namespace]"),
            ("get deployments",           "List deployments"),
            ("get services",              "List services"),
            ("get nodes",                 "List cluster nodes"),
            ("get namespaces",            "List namespaces"),
            ("describe pod <name>",       "Detailed pod info"),
            ("describe deployment <name>","Detailed deployment info"),
            ("logs <pod>",                "Show pod logs"),
            ("logs -f <pod>",             "Follow pod logs live"),
            ("exec -it <pod> -- bash",    "Open shell in pod"),
            ("apply -f <file.yaml>",      "Apply a manifest file"),
            ("delete -f <file.yaml>",     "Delete resources from manifest"),
            ("scale deployment <name> --replicas=N", "Scale a deployment"),
            ("rollout status deployment/<name>",     "Check rollout status"),
            ("rollout undo deployment/<name>",       "Roll back deployment"),
            ("rollout restart deployment/<name>",    "Restart deployment"),
            ("port-forward pod/<pod> 8080:8080",     "Port-forward to pod"),
            ("config get-contexts",                  "List kubectl contexts"),
            ("config use-context <name>",            "Switch kubectl context"),
            ("top pods",                             "Show resource usage of pods"),
            ("top nodes",                            "Show resource usage of nodes"),
            ("get events --sort-by=.lastTimestamp",  "Show sorted cluster events"),
            ("drain <node> --ignore-daemonsets",     "Drain a node safely"),
            ("cordon <node>",                        "Mark node as unschedulable"),
            ("uncordon <node>",                      "Mark node as schedulable"),
            ("api-resources",                        "List all available API resource types"),
            ("explain <resource>",                   "Get API documentation for a resource"),
        ],
        "firewall": [
            ("status", "Show active rules, open ports, and backends"),
            ("allow", "Allow port traffic: firewall allow <port> [proto] [iface]"),
            ("block", "Block port traffic: firewall block <port> [proto] [iface]"),
            ("interactive", "Prompt-driven interactive firewall wizard"),
        ],
        "disk": [
            ("list", "List all storage drives/partitions"),
            ("usage", "Show directory storage usage: disk usage <path>"),
            ("mount", "Mount a drive or partition: disk mount <dev> <target>"),
            ("unmount", "Unmount a mounted volume: disk unmount <target>"),
            ("check", "Perform filesystem integrity check: disk check <target>"),
            ("health", "Check disk health / SMART status: disk health [target]"),
            ("format", "Format a partition: disk format <dev> <fs>"),
        ],
        "archive": [
            ("create", "Create a compressed archive: archive create <out.zip> <path>"),
            ("extract", "Extract a compressed archive: archive extract <archive> [dest]"),
        ],
        "find": [
            ("file", "Search files by name pattern: find file <pattern> [path]"),
            ("text", "Search text pattern inside files: find text <pattern> [path]"),
        ],
        "config": [
            ("set", "Change a configuration setting: config set <setting> <value>"),
            ("reset", "Reset configuration to defaults: config reset"),
        ],
        "user": [
            ("add", "Add a new system user: user add <username>"),
            ("remove", "Delete a system user: user remove <username>"),
            ("list", "List system users: user list"),
            ("passwd", "Change user password: user passwd <username>"),
            ("join", "Add a user to a group: user join <group> <username>"),
        ],
        "sessions": [
            ("list", "List active logon and terminal multiplexer (tmux/screen) sessions"),
            ("kill", "Terminate/disconnect a session: sessions kill <session_id/tty/name>"),
        ],
        "session": [
            ("list", "List active logon and terminal multiplexer (tmux/screen) sessions"),
            ("kill", "Terminate/disconnect a session: session kill <session_id/tty/name>"),
        ],
        "group": [
            ("add", "Add a new system group: group add <group>"),
            ("remove", "Delete a system group: group remove <group>"),
            ("list", "List system groups: group list"),
        ],
        "permission": [
            ("set", "Set read/write/execute permissions (chmod): permission set <perms> <path>"),
            ("owner", "Change owner of file/folder (chown): permission owner <owner> <path>"),
            ("group", "Change group of file/folder (chgrp): permission group <group> <path>"),
        ],
        "schedule": [
            ("list", "List all scheduled tasks"),
            ("add", "Add a new scheduled task interactively"),
            ("remove", "Remove an existing scheduled task"),
            ("edit", "Edit the raw scheduled tasks file"),
        ],
        "crontab": [
            ("-l", "List your scheduled cron jobs"),
            ("-e", "Edit your cron jobs interactively"),
            ("-r", "Remove all of your cron jobs"),
        ],
        "schtasks": [
            ("/query", "List all scheduled tasks"),
            ("/create", "Create a new scheduled task"),
            ("/delete", "Delete an existing scheduled task"),
        ],
    }

    # Categorized commands mapping
    categories = {
        "📂 File & Directory Management": [
            "list", "cd", "back", "open", "create", "mkdir", "copy", "move", "remove", "show", "archive",
            "pwd", "whereami", "touch", "cat", "head", "tail", "less", "tree", "find", "grep", "search", "disk"
        ],
        "🐍 Python Virtual Environments": [
            "create", "enter",
        ],
        "⚙️ Process & System Resources": [
            "processes", "process", "top", "kill", "pkill", "killport", "sysinfo", "whoami", "resources", "sqlite",
            "containers", "config", "history", "env", "which", "reboot",
            "shutdown", "cancel", "port"
        ],
        "🕒 Task Scheduling & Automation": [
            "schedule", "crontab", "schtasks"
        ],
        "🌐 Network & Web Diagnostics": [
            "lookup", "dns", "ping", "traceroute", "nslookup", "connections", "netconfig", "netstat"
        ],
        "🔒 Remote Access, Services & Security": [
            "ssh", "scp", "sftp", "download", "wget", "curl", "firewall", "user", "group", "permission",
            "sessions", "session", "status", "start", "stop", "restart", "logs", "live", "enable", "disable",
            "chmod", "chown", "chgrp", "useradd", "userdel", "usermod", "passwd",
            "groupadd", "groupdel"
        ],
        "⎈️ Kubernetes & Containers": [
            "k8s", "kubectl", "containers",
        ],
        "🛠️ Version Control (Git)": [
            "git", "gitsave"
        ]
    }

    # Tracking categorized command names to catch any missing ones
    categorized_names = set()
    for cmd_list in categories.values():
        categorized_names.update(cmd_list)

    # Add Built-ins
    builtins = ["help", "clear", "exit", "quit"]
    categorized_names.update(builtins)

    # Anything else in commands goes to Miscellaneous
    aliases_to_hide = {
        "permissions", "drive", "compress", "ps", "where", "findcommand",
        "adduser", "deluser", "addgroup", "delgroup", "viewstart", "viewend",
        "scrollfile", "findfile", "findtext", "kubernetes", "doskey"
    }

    misc = []
    for name in commands:
        if name not in categorized_names and name not in aliases_to_hide:
            misc.append(name)
            
    if misc:
        categories["🛠️ Other Commands"] = misc

    # Header
    console.print("[bold green]hopit-cli — Universal Administrative Shell[/bold green]\n")

    for cat_title, cmd_list in categories.items():
        cat_cmds = []
        for name in cmd_list:
            if name in commands:
                cat_cmds.append((name, commands[name]))

        if not cat_cmds:
            continue

        table = Table(title=cat_title, show_lines=False, title_justify="left", box=None, padding=(0, 2))
        table.add_column("Command", style="bold cyan", width=42)
        table.add_column("Description", width=50)
        table.add_column("Platform Support", style="bold green", justify="center", width=18)

        for name, cmd in cat_cmds:
            privilege_label = "admin" if IS_WINDOWS else "sudo"
            desc = cmd.desc + (f"  [dim]({privilege_label})[/dim]" if cmd.needs_sudo else "")
            
            # Universal compat since we support/translate them all
            compat = "L | M | W"
            table.add_row(name, desc, compat)
            
            # Render subcommands if available
            clean_name = name.split()[0].strip()
            if clean_name in SUBCOMMANDS:
                # In the Python venv section, only show the 'venv' subcommand for 'create'
                subs = SUBCOMMANDS[clean_name]
                if "Python Virtual" in cat_title and clean_name == "create":
                    subs = [(s, d) for s, d in subs if s == "venv"]
                for sub_name, sub_desc in subs:
                    table.add_row(f"  ↳ [cyan]{sub_name}[/cyan]", f"[dim]{sub_desc}[/dim]", f"[dim]{compat}[/dim]")

        # Inject 'exit venv' for the Python venv section (it's a builtin, not in commands)
        if "Python Virtual" in cat_title:
            table.add_row(
                "exit",
                "Deactivate the current virtual environment: exit venv (prefix ok: exit v)",
                "L | M | W"
            )
            table.add_row(
                "  ↳ [cyan]venv[/cyan]",
                "[dim]Exit (deactivate) the currently active Python virtual environment[/dim]",
                "[dim]L | M | W[/dim]"
            )
            
        console.print(table)
        console.print()

    # CLI Builtins table
    builtin_cmds = []
    for name in builtins:
        if name == "quit":
            continue
        label = "exit / quit" if name == "exit" else name
        builtin_cmds.append((label, BUILTIN_DESCRIPTIONS[name]))
        
    if builtin_cmds:
        table = Table(title="🛠️ CLI Built-ins", show_lines=False, title_justify="left", box=None, padding=(0, 2))
        table.add_column("Command", style="bold cyan", width=42)
        table.add_column("Description", width=50)
        table.add_column("Platform Support", style="bold green", justify="center", width=18)
        
        for name, desc in builtin_cmds:
            table.add_row(name, desc, "L | M | W")
        console.print(table)
        console.print()

    console.print(
        "[dim]Note: Commands support prefix auto-resolution and interactive discovery. "
        "Type '?' after any command or sub-argument for positional context and usage syntax.[/dim]"
    )
    if not manager:
        console.print("[yellow]No supported package manager detected — install/uninstall/update unavailable.[/yellow]")


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
                console.print("[yellow]netsh returned a non-zero exit code -- verify with 'ip' command. "
                              "You may need to run hopit-cli as Administrator.[/yellow]")
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
                console.print("[yellow]netsh returned a non-zero exit code -- verify with 'ip' command. "
                              "You may need to run hopit-cli as Administrator.[/yellow]")
    except KeyboardInterrupt:
        console.print("\n[dim]Cancelled.[/dim]")
