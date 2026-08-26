import sys
import psutil
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

def inspect_process(proc):
    try:
        pid = proc.pid
        name = proc.name()
        try:
            exe = proc.exe()
        except Exception:
            exe = "Access Denied"
        try:
            cmdline = " ".join(proc.cmdline())
        except Exception:
            cmdline = "Access Denied"
        status = proc.status()
        
        try:
            parent = proc.parent()
            parent_str = f"{parent.name()} ({parent.pid})" if parent else "None"
        except Exception:
            parent_str = "Access Denied"
            
        cpu = f"{proc.cpu_percent(interval=0.1):.1f}%"
        
        try:
            mem_info = proc.memory_info()
            mem_rss = f"{mem_info.rss / 1024 / 1024:.1f} MB"
            mem_vms = f"{mem_info.vms / 1024 / 1024:.1f} MB"
        except Exception:
            mem_rss = "Access Denied"
            mem_vms = "Access Denied"
            
        try:
            user = proc.username()
        except Exception:
            user = "Access Denied"
            
        try:
            create_time = datetime.fromtimestamp(proc.create_time()).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            create_time = "Unknown"
            
        try:
            threads = proc.num_threads()
        except Exception:
            threads = "Unknown"
        
        try:
            num_fds = proc.num_fds()
        except AttributeError:
            try:
                num_fds = proc.num_handles()
            except Exception:
                num_fds = "N/A"
        except Exception:
            num_fds = "N/A"

        # Connections
        conns = []
        try:
            for c in proc.net_connections():
                laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "N/A"
                raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "*"
                conns.append(f"{laddr} -> {raddr} ({c.status})")
        except Exception:
            conns = ["Access Denied / Not Available"]

    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        print(f"Error inspecting process: {e}")
        return

    console = Console()
    
    grid = Table.grid(padding=(0, 2))
    grid.add_column("Key", style="bold cyan")
    grid.add_column("Value")
    
    grid.add_row("PID", str(pid))
    grid.add_row("Name", name)
    grid.add_row("Status", status)
    grid.add_row("Owner", user)
    grid.add_row("Parent", parent_str)
    grid.add_row("CPU Usage", cpu)
    grid.add_row("Memory RSS", mem_rss)
    grid.add_row("Memory VMS", mem_vms)
    grid.add_row("Threads", str(threads))
    grid.add_row("File Descriptors/Handles", str(num_fds))
    grid.add_row("Created At", create_time)
    grid.add_row("Executable", exe)
    grid.add_row("Command Line", cmdline)
    
    if conns and conns != ["Access Denied / Not Available"]:
        grid.add_row("Active Connections", "\n".join(conns[:5]))
        if len(conns) > 5:
            grid.add_row("", f"... and {len(conns)-5} more connections")
            
    console.print(Panel(grid, title=f"[bold green]Process Info: {name} (PID: {pid})[/bold green]", border_style="cyan"))

from hopit.config import safe_entrypoint

@safe_entrypoint
def main():
    if len(sys.argv) < 2:
        print("Usage: process <pid_or_name>")
        sys.exit(1)

    target = sys.argv[1]
    console = Console()

    if target.isdigit():
        pid = int(target)
        try:
            proc = psutil.Process(pid)
            inspect_process(proc)
        except psutil.NoSuchProcess:
            console.print(f"[red]No process found with PID {pid}.[/red]")
    else:
        # Search by name
        matches = []
        for proc in psutil.process_iter(attrs=['pid', 'name', 'username', 'cpu_percent', 'memory_percent']):
            try:
                if target.lower() in proc.info['name'].lower():
                    matches.append(proc)
            except Exception:
                pass
        
        if not matches:
            console.print(f"[red]No processes found matching name '{target}'.[/red]")
        elif len(matches) == 1:
            inspect_process(matches[0])
        else:
            # Display list of matches
            table = Table(title=f"Multiple matches found for '{target}'", border_style="cyan")
            table.add_column("PID", style="magenta")
            table.add_column("Name", style="green")
            table.add_column("User", style="yellow")
            table.add_column("CPU %", style="cyan")
            table.add_column("MEM %", style="cyan")
            
            for p in matches[:15]:
                try:
                    cpu_val = p.cpu_percent(interval=0.01)
                    mem_val = p.memory_percent()
                    cpu = f"{cpu_val:.1f}%"
                    mem = f"{mem_val:.1f}%"
                except Exception:
                    cpu = "N/A"
                    mem = "N/A"
                table.add_row(str(p.pid), p.name(), p.username() or "N/A", cpu, mem)
                
            console.print(table)
            if len(matches) > 15:
                console.print(f"[dim]... and {len(matches) - 15} more matches. Please specify a PID.[/dim]")

if __name__ == "__main__":
    main()
