import sys
import psutil
from rich.console import Console
from rich.table import Table

from hopit.config import safe_entrypoint

@safe_entrypoint
def main():
    console = Console()
    sort_by = "cpu"
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg in ("cpu", "mem", "memory", "name", "pid"):
            sort_by = "memory" if arg == "mem" else arg

    processes = []
    # Fetch all processes
    for proc in psutil.process_iter(attrs=['pid', 'name', 'username', 'cpu_percent', 'memory_percent', 'status']):
        try:
            info = proc.info
            # Avoid showing 0.0% for everything by getting a quick read if needed
            if info['cpu_percent'] is None:
                info['cpu_percent'] = 0.0
            processes.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    # Sort processes
    if sort_by == "cpu":
        processes.sort(key=lambda x: x.get('cpu_percent') or 0.0, reverse=True)
    elif sort_by == "memory":
        processes.sort(key=lambda x: x.get('memory_percent') or 0.0, reverse=True)
    elif sort_by == "pid":
        processes.sort(key=lambda x: x.get('pid') or 0)
    elif sort_by == "name":
        processes.sort(key=lambda x: (x.get('name') or "").lower())

    table = Table(border_style="cyan")
    table.add_column("PID", style="magenta", justify="right")
    table.add_column("Name", style="green")
    table.add_column("User", style="yellow")
    table.add_column("Status", style="blue")
    table.add_column("CPU %", style="cyan", justify="right")
    table.add_column("MEM %", style="cyan", justify="right")

    for p in processes[:30]:
        pid = str(p['pid'])
        name = p['name'] or "N/A"
        user = p['username'] or "N/A"
        status = p['status'] or "N/A"
        
        cpu_val = p['cpu_percent'] or 0.0
        mem_val = p['memory_percent'] or 0.0
        
        cpu = f"{cpu_val:.1f}%"
        mem = f"{mem_val:.1f}%"
        table.add_row(pid, name, user, status, cpu, mem)

    console.print(table)

if __name__ == "__main__":
    main()
