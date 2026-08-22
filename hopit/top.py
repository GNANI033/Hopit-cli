import time
import sys
import psutil
from rich.console import Console
from rich.table import Table
from rich.live import Live

def generate_table():
    processes = []
    # Force psutil to calculate CPU percent for each process
    for proc in psutil.process_iter(attrs=['pid', 'name', 'username', 'cpu_percent', 'memory_percent']):
        try:
            info = proc.info
            if info['cpu_percent'] is None:
                info['cpu_percent'] = 0.0
            processes.append(info)
        except Exception:
            pass
            
    processes.sort(key=lambda x: x.get('cpu_percent') or 0.0, reverse=True)
    
    table = Table(border_style="cyan")
    table.add_column("PID", style="magenta", justify="right")
    table.add_column("Name", style="green")
    table.add_column("User", style="yellow")
    table.add_column("CPU %", style="cyan", justify="right")
    table.add_column("MEM %", style="cyan", justify="right")
    
    for p in processes[:20]:
        pid = str(p['pid'])
        name = p['name'] or "N/A"
        user = p['username'] or "N/A"
        cpu = f"{p['cpu_percent'] or 0.0:.1f}%"
        mem = f"{p['memory_percent'] or 0.0:.1f}%"
        table.add_row(pid, name, user, cpu, mem)
        
    return table

def main():
    console = Console()
    console.print("[dim]Starting top monitoring. Press Ctrl+C to exit...[/dim]")
    # First measurement to initialize CPU percent tracking
    psutil.cpu_percent(interval=None)
    for p in psutil.process_iter():
        try:
            p.cpu_percent(interval=None)
        except Exception:
            pass
            
    try:
        with Live(generate_table(), console=console, refresh_per_second=1) as live:
            while True:
                time.sleep(2)
                live.update(generate_table())
    except KeyboardInterrupt:
        console.print("\n[yellow]Exited top process monitor.[/yellow]")

if __name__ == "__main__":
    main()
