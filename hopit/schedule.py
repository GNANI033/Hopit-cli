import sys
import subprocess
import shlex
import re
from hopit.config import IS_WINDOWS, IS_MACOS

try:
    from rich.prompt import Prompt, Confirm
    from rich.table import Table
    from hopit.config import console
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    console = None

def select_dropdown(title: str, choices: list[str], default_idx: int = 0) -> str:
    """Interactive Arrow-Key Dropdown Menu (Use Up/Down arrows + Enter)."""
    if not choices:
        return ""
    if not sys.stdin.isatty():
        return choices[default_idx]

    current_idx = default_idx

    if IS_WINDOWS:
        import msvcrt
        def getch():
            ch = msvcrt.getch()
            if ch in (b'\x00', b'\xe0'):
                ch = msvcrt.getch()
                if ch == b'H': return 'UP'
                if ch == b'P': return 'DOWN'
            if ch in (b'\r', b'\n'): return 'ENTER'
            return None
    else:
        import termios, tty
        def getch():
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = sys.stdin.read(1)
                if ch == '\x1b':
                    ch2 = sys.stdin.read(1)
                    ch3 = sys.stdin.read(1)
                    if ch3 == 'A': return 'UP'
                    if ch3 == 'B': return 'DOWN'
                if ch in ('\r', '\n'): return 'ENTER'
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            return None

    if HAS_RICH and console:
        console.print(f"\n[bold cyan]{title}[/bold cyan] [dim](Use ↑/↓ arrows and press ENTER)[/dim]:")
    else:
        print(f"\n{title} (Use UP/DOWN arrows and press ENTER):")

    lines_to_clear = len(choices)
    first_render = True
    
    while True:
        if not first_render:
            sys.stdout.write(f"\033[{lines_to_clear}A")
        first_render = False

        for idx, choice in enumerate(choices):
            if idx == current_idx:
                sys.stdout.write(f"\033[K  \033[1;36m❯ 🔘 {choice}\033[0m\n")
            else:
                sys.stdout.write(f"\033[K    ⚪ {choice}\n")
        sys.stdout.flush()

        key = getch()
        if key == 'UP':
            current_idx = (current_idx - 1) % len(choices)
        elif key == 'DOWN':
            current_idx = (current_idx + 1) % len(choices)
        elif key == 'ENTER':
            break

    return choices[current_idx]

def prompt_ask(prompt_text: str, default: str = "") -> str:
    """Safely prompt text input with fallback."""
    if HAS_RICH:
        return Prompt.ask(prompt_text, default=default)
    else:
        def_str = f" [{default}]" if default else ""
        res = input(f"{prompt_text}{def_str}: ").strip()
        return res if res else default

def prompt_confirm(prompt_text: str, default: bool = True) -> bool:
    """Safely prompt boolean confirmation with fallback."""
    if HAS_RICH:
        return Confirm.ask(prompt_text, default=default)
    else:
        def_str = "[Y/n]" if default else "[y/N]"
        res = input(f"{prompt_text} {def_str}: ").strip().lower()
        if not res:
            return default
        return res in ("y", "yes")

def get_current_crontab() -> str:
    res = subprocess.run(["crontab", "-l"], capture_output=True, text=True, errors="ignore")
    if res.returncode == 0:
        return res.stdout
    return ""

def write_crontab(content: str) -> bool:
    res = subprocess.run(["crontab", "-"], input=content, text=True, errors="ignore", capture_output=True)
    return res.returncode == 0

def add_schedule(name: str = None, command: str = None, timing: str = None):
    print("\n--- Add Scheduled Task ---")
    if not name:
        name = prompt_ask("Task Name (without spaces)", default="MyTask").replace(" ", "_")
    if not command:
        command = prompt_ask("Command to execute")
    if not command:
        print("Command is required.")
        return

    timing_options = [
        "Every Minute (* * * * *)",
        "Hourly (0 * * * *)",
        "Daily at midnight (0 0 * * *)",
        "Weekly on Sunday (0 0 * * 0)",
        "Monthly on the 1st (0 0 1 * *)",
        "At Reboot (@reboot)",
        "Custom (Enter manually)"
    ]
    
    if timing:
        # Match from provided arg
        selected_timing = next((t for t in timing_options if timing.lower() in t.lower()), timing_options[-1])
        if selected_timing == timing_options[-1]:
            # if they provided a raw cron expression like "0 * * * *"
            selected_timing = f"Custom ({timing})"
    else:
        selected_timing = select_dropdown("Select Timing / Frequency", timing_options)
    
    if IS_WINDOWS:
        # Windows schtasks
        sc = "MINUTE"
        mo = "1"
        d = ""
        if "Minute" in selected_timing:
            sc, mo = "MINUTE", "1"
        elif "Hourly" in selected_timing:
            sc, mo = "HOURLY", "1"
        elif "Daily" in selected_timing:
            sc, mo = "DAILY", "1"
        elif "Weekly" in selected_timing:
            sc, mo, d = "WEEKLY", "1", "SUN"
        elif "Monthly" in selected_timing:
            sc, mo, d = "MONTHLY", "1", "1"
        elif "Reboot" in selected_timing:
            sc = "ONSTART"
            mo = ""
        else:
            print("Custom schedule parsing on Windows via wizard is limited.")
            sc_input = prompt_ask("Schedule Type (MINUTE, HOURLY, DAILY, WEEKLY, MONTHLY, ONSTART, ONLOGON, ONIDLE)", default="DAILY")
            mo_input = prompt_ask("Modifier (e.g. 1 for every 1 day/min)", default="1")
            sc = sc_input
            mo = mo_input
        
        # Windows Task Scheduler requires an executable. Wrap shell commands in cmd.exe.
        win_cmd = command
        if not win_cmd.lower().startswith(("cmd.exe", "powershell.exe")):
            win_cmd = f'cmd.exe /c "{command}"'

        cmd = ["schtasks", "/create", "/tn", name, "/tr", win_cmd, "/sc", sc]
        if sc not in ("ONSTART", "ONLOGON", "ONIDLE") and mo:
            cmd.extend(["/mo", mo])
        if d:
            cmd.extend(["/d", d])
        
        if prompt_confirm(f"Create Windows Task '{name}'?"):
            res = subprocess.run(cmd)
            if res.returncode == 0:
                print("Task created successfully.")
            else:
                print("Failed to create task.")

    else:
        # Linux/macOS crontab
        cron_expr = ""
        if "Minute" in selected_timing:
            cron_expr = "* * * * *"
        elif "Hourly" in selected_timing:
            cron_expr = "0 * * * *"
        elif "Daily" in selected_timing:
            cron_expr = "0 0 * * *"
        elif "Weekly" in selected_timing:
            cron_expr = "0 0 * * 0"
        elif "Monthly" in selected_timing:
            cron_expr = "0 0 1 * *"
        elif "Reboot" in selected_timing:
            cron_expr = "@reboot"
        else:
            if "Custom (" in selected_timing and selected_timing != "Custom (Enter manually)":
                cron_expr = selected_timing.split("(", 1)[1].rstrip(")")
            else:
                cron_expr = prompt_ask("Enter custom cron expression (e.g. '*/5 * * * *')", default="* * * * *")
            
        full_line = f"{cron_expr} {command} # {name}\n"
        
        if prompt_confirm(f"Add this to crontab?\n{full_line.strip()}"):
            current = get_current_crontab()
            if not current.endswith("\n") and current:
                current += "\n"
            new_cron = current + full_line
            if write_crontab(new_cron):
                print("Task added to crontab successfully.")
            else:
                print("Failed to add task to crontab.")

def remove_schedule(task_name: str = None):
    print("\n--- Remove Scheduled Task ---")
    if IS_WINDOWS:
        res = subprocess.run(["schtasks", "/query", "/fo", "CSV", "/nh"], capture_output=True, text=True, errors="ignore")
        if res.returncode != 0:
            print("Failed to list tasks.")
            return
        tasks = []
        for line in res.stdout.splitlines():
            if line.strip():
                parts = line.split('","')
                if parts:
                    tname = parts[0].strip('"')
                    if tname:
                        tasks.append(tname)
        
        if not tasks:
            print("No tasks found.")
            return
            
        if task_name:
            if task_name not in tasks:
                print(f"Task '{task_name}' not found.")
                return
            choice = task_name
        else:
            tasks.sort()
            tasks.insert(0, "Cancel")
            choice = select_dropdown("Select Task to Remove", tasks)
            if choice == "Cancel":
                return
            
        if prompt_confirm(f"Delete task {choice}?"):
            res = subprocess.run(["schtasks", "/delete", "/tn", choice, "/f"])
            if res.returncode == 0:
                print("Task deleted.")
            else:
                print("Failed to delete task.")
    else:
        current = get_current_crontab()
        if not current.strip():
            print("No cron jobs found.")
            return
            
        if task_name:
            # find line with # task_name
            target = f"# {task_name}"
            matches = [l for l in current.split("\n") if l.strip() and target in l]
            if not matches:
                print(f"No cron job found matching '{task_name}'.")
                return
            choice = matches[0]
        else:
            lines = current.strip().split("\n")
            lines.insert(0, "Cancel")
            choice = select_dropdown("Select Cron Job to Remove", lines)
            
            if choice == "Cancel":
                return
            
        new_lines = [l for l in current.split("\n") if l != choice and l.strip()]
        new_cron = "\n".join(new_lines) + "\n"
        
        if prompt_confirm("Remove selected cron job?"):
            if write_crontab(new_cron):
                print("Cron job removed.")
            else:
                print("Failed to remove cron job.")

def list_schedule():
    if IS_WINDOWS:
        subprocess.run(["schtasks", "/query", "/fo", "TABLE"])
    else:
        subprocess.run(["crontab", "-l"])

def interactive_schedule():
    print("\n--- Interactive Schedule Wizard ---")
    action = select_dropdown("Choose Action", ["List Tasks", "Add Task", "Remove Task", "Edit Task (Raw)", "Exit"])
    
    if action == "Exit":
        return
    elif action == "List Tasks":
        list_schedule()
    elif action == "Add Task":
        add_schedule()
    elif action == "Remove Task":
        remove_schedule()
    elif action == "Edit Task (Raw)":
        if IS_WINDOWS:
            print("Interactive editing is not supported directly via schtasks. Please use Task Scheduler GUI (taskschd.msc).")
        else:
            subprocess.run(["crontab", "-e"])

def get_schedule_names() -> list[str]:
    names = []
    if IS_WINDOWS:
        res = subprocess.run(["schtasks", "/query", "/fo", "CSV", "/nh"], capture_output=True, text=True, errors="ignore")
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if line.strip():
                    parts = line.split('","')
                    if parts:
                        tname = parts[0].strip('"')
                        if tname:
                            names.append(tname)
    else:
        current = get_current_crontab()
        for line in current.splitlines():
            if line.strip() and not line.startswith("#"):
                if "#" in line:
                    names.append(line.split("#")[-1].strip())
                else:
                    names.append(line.strip())
    return names

from hopit.config import safe_entrypoint

@safe_entrypoint
def main():
    try:
        args = sys.argv[1:]
        
        is_crontab = False
        is_schtasks = False
        
        if args and args[0] == "EMULATE_CRONTAB":
            is_crontab = True
            args = args[1:]
        elif args and args[0] == "EMULATE_SCHTASKS":
            is_schtasks = True
            args = args[1:]

        if not args:
            if is_crontab:
                print("usage: crontab [-l | -r | -e]")
                return
            if is_schtasks:
                print("schtasks /query | /create | /delete")
                return
            interactive_schedule()
            return
            
        sub = args[0]
        sub_lower = sub.lower()
        cmd_args = args[1:]
        
        # 1. Native Command Pass-Through (if they pass complex native flags directly)
        if sub_lower.startswith("/") and IS_WINDOWS and (len(cmd_args) > 0 or sub_lower not in ("/query", "/create", "/delete")):
            subprocess.run(["schtasks"] + args)
            return
        elif sub_lower.startswith("-") and not IS_WINDOWS and (len(cmd_args) > 0 or sub_lower not in ("-l", "-e", "-r")):
            subprocess.run(["crontab"] + args)
            return

        # 2. Unified Hopit Commands & Simple Fallbacks
        if sub_lower in ("list", "-l", "/query"):
            list_schedule()
        elif sub_lower in ("add", "/create"):
            add_schedule(*cmd_args)
        elif sub_lower in ("remove", "-r", "/delete"):
            if cmd_args:
                remove_schedule(*cmd_args)
            else:
                remove_schedule()
        elif sub_lower in ("edit", "-e"):
            if IS_WINDOWS:
                print("Interactive editing is not supported directly via schtasks. Please use Task Scheduler GUI (taskschd.msc).")
            else:
                subprocess.run(["crontab", "-e"])
        else:
            interactive_schedule()
    except KeyboardInterrupt:
        print("\nOperation cancelled.")
        sys.exit(1)

if __name__ == "__main__":
    main()
