import sys
import subprocess
import shutil
import os
import re
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from hopit.config import IS_WINDOWS, IS_MACOS

def get_windows_sessions():
    try:
        proc = subprocess.run(["query", "user"], capture_output=True, text=True, errors="ignore", check=True)
        if proc.stdout and proc.stdout.strip():
            return proc.stdout
    except Exception:
        pass
        
    try:
        proc = subprocess.run(["qwinsta"], capture_output=True, text=True, errors="ignore", check=True)
        if proc.stdout and proc.stdout.strip():
            return proc.stdout
    except Exception:
        pass
        
    try:
        ps_script = (
            "Get-CimInstance Win32_LogonSession | "
            "Where-Object { $_.LogonType -in @(2, 7, 10, 11) } | "
            "ForEach-Object { "
                "$session = $_; "
                "$user = (Get-CimInstance Win32_LoggedOnUser | Where-Object { $_.Dependent.LogonId -eq $session.LogonId }).Antecedent.Name; "
                "if ($user) { "
                    "[PSCustomObject]@{ "
                        "USERNAME = $user; "
                        "SESSIONNAME = if ($session.LogonType -eq 2) { 'Console' } elseif ($session.LogonType -eq 10) { 'RDP' } else { 'Local' }; "
                        "ID = $session.LogonId; "
                        "STATE = 'Active'; "
                        "IDLE_TIME = '-'; "
                        "LOGON_TIME = $session.StartTime.ToString('yyyy-MM-dd HH:mm:ss') "
                    "} "
                "} "
            "} | ConvertTo-Json -Compress"
        )
        proc = subprocess.run(["powershell", "-Command", ps_script], capture_output=True, text=True, errors="ignore", check=True)
        if proc.stdout and proc.stdout.strip():
            return proc.stdout
    except Exception:
        pass
        
    try:
        import getpass
        username = getpass.getuser()
        lines = [
            "USERNAME              SESSIONNAME        ID  STATE   IDLE TIME  LOGON TIME",
            f">{username:<21}Console             1  Active          -  -"
        ]
        return "\n".join(lines)
    except Exception:
        return None

def parse_windows_sessions(output: str):
    if not output:
        return []
    output_strip = output.strip()
    if output_strip.startswith("[") or output_strip.startswith("{"):
        import json
        try:
            data = json.loads(output_strip)
            if isinstance(data, dict):
                data = [data]
            rows = []
            import getpass
            current_user = getpass.getuser().lower()
            for item in data:
                username = item.get("USERNAME", "") or item.get("Username", "")
                if not username:
                    continue
                current = username.lower() == current_user
                rows.append({
                    "username": username,
                    "sessionname": item.get("SESSIONNAME", "Console") or item.get("SessionName", "Console"),
                    "id": str(item.get("ID", "") or item.get("Id", "") or item.get("SessionId", "")),
                    "state": item.get("STATE", "Active") or item.get("State", "Active"),
                    "idle": item.get("IDLE_TIME", "-") or item.get("Idle", "-"),
                    "logon": item.get("LOGON_TIME", "-") or item.get("LogonTime", "-"),
                    "current": current
                })
            return rows
        except Exception:
            pass

    lines = output_strip.splitlines()
    if not lines:
        return []
    
    header = lines[0]
    idx_username = header.find("USERNAME")
    idx_sessionname = header.find("SESSIONNAME")
    idx_id = header.find("ID")
    idx_state = header.find("STATE")
    idx_idle = header.find("IDLE TIME")
    idx_logon = header.find("LOGON TIME")
    
    rows = []
    import getpass
    current_user = getpass.getuser().lower()
    
    for line in lines[1:]:
        if not line.strip():
            continue
        
        if idx_username != -1 and idx_sessionname != -1 and idx_id != -1 and idx_state != -1:
            indices = sorted([
                (idx_username, "username"),
                (idx_sessionname, "sessionname"),
                (idx_id, "id"),
                (idx_state, "state"),
                (idx_idle if idx_idle != -1 else 9999, "idle"),
                (idx_logon if idx_logon != -1 else 9999, "logon")
            ])
            
            val_map = {}
            for i, (start_idx, name) in enumerate(indices):
                if start_idx == 9999:
                    val_map[name] = "-"
                    continue
                end_idx = indices[i+1][0] if i+1 < len(indices) and indices[i+1][0] != 9999 else len(line)
                val_map[name] = line[start_idx:end_idx].strip()
                
            username = val_map.get("username", "")
            sessionname = val_map.get("sessionname", "")
            session_id = val_map.get("id", "")
            state = val_map.get("state", "")
            idle = val_map.get("idle", "-")
            logon = val_map.get("logon", "-")
        else:
            parts = line.split()
            if len(parts) >= 2:
                username = parts[0]
                sessionname = parts[1]
                session_id = parts[2] if len(parts) > 2 else "-"
                state = parts[3] if len(parts) > 3 else "Active"
                idle = "-"
                logon = "-"
            else:
                continue
        
        if not username:
            continue
            
        current = False
        if username.startswith(">"):
            username = username.lstrip(">").strip()
            current = True
        if sessionname.startswith(">"):
            sessionname = sessionname.lstrip(">").strip()
            current = True
            
        if username.lower() == current_user:
            current = True
            
        rows.append({
            "username": username,
            "sessionname": sessionname or "Console",
            "id": session_id,
            "state": state,
            "idle": idle,
            "logon": logon,
            "current": current
        })
    return rows

def get_unix_sessions():
    try:
        # Try running 'w' without headers
        proc = subprocess.run(["w", "-h"], capture_output=True, text=True, errors="ignore", check=True)
        return proc.stdout, "w"
    except Exception:
        try:
            # Fallback to 'who'
            proc = subprocess.run(["who"], capture_output=True, text=True, errors="ignore", check=True)
            return proc.stdout, "who"
        except Exception:
            return None, ""

def parse_unix_sessions(output: str, mode: str):
    if not output:
        return []
    lines = output.strip().splitlines()
    rows = []
    if mode == "w":
        for line in lines:
            if not line.strip():
                continue
            # Extract based on typical 'w' columns
            user = line[0:8].strip()
            tty = line[8:17].strip()
            host = line[17:34].strip()
            login = line[34:43].strip()
            idle = line[43:50].strip()
            jcpu = line[50:57].strip()
            pcpu = line[57:64].strip()
            what = line[64:].strip()
            rows.append({
                "user": user,
                "tty": tty,
                "from": host or "-",
                "login": login,
                "idle": idle,
                "what": what
            })
    else:
        for line in lines:
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 2:
                rows.append({
                    "user": parts[0],
                    "tty": parts[1],
                    "from": parts[5] if len(parts) > 5 else (parts[2] if len(parts) > 2 else "-"),
                    "login": " ".join(parts[2:5]) if len(parts) >= 5 else "-",
                    "idle": "-",
                    "what": "-"
                })
    return rows

def get_tmux_sessions():
    if not shutil.which("tmux"):
        return None
    try:
        proc = subprocess.run(["tmux", "list-sessions"], capture_output=True, text=True, errors="ignore")
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return None

def parse_tmux_sessions(output: str):
    if not output:
        return []
    rows = []
    for line in output.splitlines():
        if not line.strip():
            continue
        match = re.match(r"^([^:]+):\s+(\d+)\s+windows\s+\(created\s+([^)]+)\)\s+\[([^\]]+)\]\s*(.*)$", line)
        if match:
            name, windows, created, size, status = match.groups()
            rows.append({
                "name": name,
                "windows": windows,
                "created": created,
                "size": size,
                "status": status.strip("()") or "detached"
            })
        else:
            rows.append({
                "name": line.split(":")[0] if ":" in line else line,
                "windows": "-",
                "created": "-",
                "size": "-",
                "status": "active"
            })
    return rows

def get_screen_sessions():
    if not shutil.which("screen"):
        return None
    try:
        proc = subprocess.run(["screen", "-list"], capture_output=True, text=True, errors="ignore")
        return proc.stdout.strip()
    except Exception:
        pass
    return None

def parse_screen_sessions(output: str):
    if not output:
        return []
    rows = []
    for line in output.splitlines():
        line_strip = line.strip()
        if not line_strip or "There is a screen on" in line_strip or "Socket" in line_strip:
            continue
        parts = line_strip.split(maxsplit=2)
        if len(parts) >= 2:
            name = parts[0]
            created = parts[1].strip("()")
            status = parts[2].strip("()") if len(parts) > 2 else "unknown"
            rows.append({
                "name": name,
                "created": created,
                "status": status
            })
    return rows

def list_sessions():
    console = Console()
    
    # 1. Logon Sessions
    if IS_WINDOWS:
        raw_sessions = get_windows_sessions()
        if raw_sessions:
            rows = parse_windows_sessions(raw_sessions)
            if rows:
                table = Table(title="👤 Active Logon Sessions", border_style="cyan")
                table.add_column("Current", justify="center")
                table.add_column("Username", style="green")
                table.add_column("Session Name", style="blue")
                table.add_column("ID", style="magenta", justify="right")
                table.add_column("State", style="cyan")
                table.add_column("Idle Time", style="yellow")
                table.add_column("Logon Time", style="white")
                for r in rows:
                    curr_marker = "[bold green]➔[/bold green]" if r["current"] else ""
                    table.add_row(curr_marker, r["username"], r["sessionname"], r["id"], r["state"], r["idle"], r["logon"])
                console.print(table)
                console.print()
            else:
                console.print("[yellow]No active logon sessions retrieved.[/yellow]")
        else:
            console.print("[yellow]Could not query active logon sessions on Windows.[/yellow]")
    else:
        raw_sessions, mode = get_unix_sessions()
        if raw_sessions:
            rows = parse_unix_sessions(raw_sessions, mode)
            if rows:
                table = Table(title="👤 Active Logon Sessions", border_style="cyan")
                table.add_column("User", style="green")
                table.add_column("TTY", style="magenta")
                table.add_column("From / Host", style="blue")
                table.add_column("Login Time", style="white")
                table.add_column("Idle Time", style="yellow")
                table.add_column("Active Process (WHAT)", style="cyan")
                for r in rows:
                    table.add_row(r["user"], r["tty"], r["from"], r["login"], r["idle"], r["what"])
                console.print(table)
                console.print()
            else:
                console.print("[yellow]No active logon sessions found.[/yellow]")
        else:
            console.print("[yellow]Could not query active logon sessions.[/yellow]")

    # 2. Terminal Multiplexers
    if not IS_WINDOWS:
        # Tmux
        tmux_raw = get_tmux_sessions()
        if tmux_raw:
            tmux_rows = parse_tmux_sessions(tmux_raw)
            if tmux_rows:
                table = Table(title="📟 Active Tmux Sessions", border_style="cyan")
                table.add_column("Session Name/ID", style="green")
                table.add_column("Windows", style="magenta", justify="right")
                table.add_column("Created Time", style="blue")
                table.add_column("Size", style="yellow")
                table.add_column("Status", style="cyan")
                for r in tmux_rows:
                    table.add_row(r["name"], r["windows"], r["created"], r["size"], r["status"])
                console.print(table)
                console.print()
        
        # Screen
        screen_raw = get_screen_sessions()
        if screen_raw and "No Sockets found" not in screen_raw:
            screen_rows = parse_screen_sessions(screen_raw)
            if screen_rows:
                table = Table(title="📺 Active Screen Sessions", border_style="cyan")
                table.add_column("Session Name/PID", style="green")
                table.add_column("Created Time", style="blue")
                table.add_column("Status", style="cyan")
                for r in screen_rows:
                    table.add_row(r["name"], r["created"], r["status"])
                console.print(table)
                console.print()

def kill_session(target):
    console = Console()
    if not target:
        console.print("[yellow]Usage: sessions kill <session_id|tty|name>[/yellow]")
        return False
        
    if IS_WINDOWS:
        try:
            proc = subprocess.run(["logoff", target], capture_output=True, text=True, errors="ignore")
            if proc.returncode == 0:
                console.print(f"[bold green]✓ Successfully logged off session {target}[/bold green]")
                return True
            else:
                err = (proc.stderr or proc.stdout or "").strip()
                console.print(f"[red]Failed to log off session {target}.[/red]")
                if err:
                    console.print(f"[dim]{err}[/dim]")
        except Exception as e:
            console.print(f"[red]Error running logoff: {e}[/red]")
    else:
        # Check if Tmux session
        is_killed = False
        if shutil.which("tmux"):
            try:
                check = subprocess.run(["tmux", "has-session", "-t", target], capture_output=True)
                if check.returncode == 0:
                    subprocess.run(["tmux", "kill-session", "-t", target])
                    console.print(f"[bold green]✓ Successfully terminated Tmux session '{target}'[/bold green]")
                    is_killed = True
            except Exception:
                pass
                
        if is_killed:
            return True
            
        # Check Screen session
        if shutil.which("screen"):
            try:
                list_proc = subprocess.run(["screen", "-list"], capture_output=True, text=True, errors="ignore")
                if target in list_proc.stdout:
                    subprocess.run(["screen", "-XS", target, "quit"])
                    console.print(f"[bold green]✓ Successfully terminated Screen session '{target}'[/bold green]")
                    is_killed = True
            except Exception:
                pass
                
        if is_killed:
            return True

        # Fallback to TTY / logind
        tty_name = target.replace("/dev/", "")
        console.print(f"[cyan]Attempting to terminate session on {tty_name}...[/cyan]")
        try:
            # Try pkill -t
            proc = subprocess.run(["pkill", "-t", tty_name], capture_output=True, text=True, errors="ignore")
            if proc.returncode == 0:
                console.print(f"[bold green]✓ Successfully terminated processes on {tty_name}[/bold green]")
                return True
            else:
                # Try systemd logind terminate-session if target is digit
                if target.isdigit() and shutil.which("loginctl"):
                    login_proc = subprocess.run(["loginctl", "terminate-session", target], capture_output=True, text=True, errors="ignore")
                    if login_proc.returncode == 0:
                        console.print(f"[bold green]✓ Successfully terminated logind session {target}[/bold green]")
                        return True
                console.print(f"[red]No active sessions or multiplexers found matching '{target}'.[/red]")
        except Exception as e:
            console.print(f"[red]Error terminating session: {e}[/red]")
    return False

def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd in ("kill", "remove", "delete", "disconnect"):
            target = sys.argv[2] if len(sys.argv) > 2 else ""
            kill_session(target)
        elif cmd in ("list", "show"):
            list_sessions()
        else:
            Console().print(f"[red]Unknown subcommand '{cmd}'. Supported: list, kill[/red]")
    else:
        list_sessions()

if __name__ == "__main__":
    main()
