import psutil
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

def get_bar(pct, width=20):
    filled = int(pct / 100 * width)
    empty = width - filled
    color = "green" if pct < 60 else "yellow" if pct < 85 else "red"
    return f"[{color}]" + "█" * filled + "[/]" + "░" * empty

from hopit.config import safe_entrypoint

@safe_entrypoint
def main():
    console = Console()
    
    # 1. CPU Info
    cpu_pct = psutil.cpu_percent(interval=0.1)
    cpu_cores_phys = psutil.cpu_count(logical=False)
    cpu_cores_log = psutil.cpu_count(logical=True)
    try:
        cpu_freq = psutil.cpu_freq().current
        freq_str = f"{cpu_freq:.0f} MHz"
    except Exception:
        freq_str = "N/A"
        
    cpu_grid = Table.grid(padding=(0, 2))
    cpu_grid.add_column("Key", style="bold cyan")
    cpu_grid.add_column("Value")
    cpu_grid.add_row("Usage", f"{get_bar(cpu_pct)} {cpu_pct:.1f}%")
    cpu_grid.add_row("Cores", f"{cpu_cores_phys} Physical / {cpu_cores_log} Logical")
    cpu_grid.add_row("Frequency", freq_str)

    # 2. Memory Info
    mem = psutil.virtual_memory()
    mem_pct = mem.percent
    mem_used = mem.used / 1024 / 1024 / 1024
    mem_total = mem.total / 1024 / 1024 / 1024
    
    mem_grid = Table.grid(padding=(0, 2))
    mem_grid.add_column("Key", style="bold cyan")
    mem_grid.add_column("Value")
    mem_grid.add_row("RAM Usage", f"{get_bar(mem_pct)} {mem_pct:.1f}%")
    mem_grid.add_row("RAM Total", f"{mem_used:.1f} GB / {mem_total:.1f} GB")
    
    try:
        swap = psutil.swap_memory()
        swap_pct = swap.percent
        swap_used = swap.used / 1024 / 1024 / 1024
        swap_total = swap.total / 1024 / 1024 / 1024
        mem_grid.add_row("Swap Usage", f"{get_bar(swap_pct)} {swap_pct:.1f}%")
        mem_grid.add_row("Swap Total", f"{swap_used:.1f} GB / {swap_total:.1f} GB")
    except Exception:
        pass

    # 3. Disk Info
    disk_table = Table(border_style="cyan")
    disk_table.add_column("Mount", style="green")
    disk_table.add_column("Type", style="dim")
    disk_table.add_column("Usage", style="yellow")
    disk_table.add_column("Used / Total", style="magenta")
    
    for part in psutil.disk_partitions(all=False):
        if 'loop' in part.device or part.mountpoint.startswith('/snap'):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
            pct = usage.percent
            used = usage.used / 1024 / 1024 / 1024
            total = usage.total / 1024 / 1024 / 1024
            disk_table.add_row(
                part.mountpoint,
                part.fstype,
                f"{get_bar(pct, width=10)} {pct:.1f}%",
                f"{used:.1f} GB / {total:.1f} GB"
            )
        except Exception:
            pass

    # 4. Network Info
    try:
        net_io = psutil.net_io_counters()
        sent_gb = net_io.bytes_sent / 1024 / 1024 / 1024
        recv_gb = net_io.bytes_recv / 1024 / 1024 / 1024
    except Exception:
        sent_gb = 0.0
        recv_gb = 0.0
    
    net_grid = Table.grid(padding=(0, 2))
    net_grid.add_column("Key", style="bold cyan")
    net_grid.add_column("Value")
    net_grid.add_row("Bytes Sent", f"{sent_gb:.2f} GB")
    net_grid.add_row("Bytes Recv", f"{recv_gb:.2f} GB")

    dash_table = Table.grid(padding=1)
    dash_table.add_column("Col1")
    dash_table.add_column("Col2")
    
    dash_table.add_row(
        Panel(cpu_grid, title="[bold green]CPU Status[/bold green]", border_style="cyan"),
        Panel(mem_grid, title="[bold green]Memory Status[/bold green]", border_style="cyan")
    )
    
    dash_table.add_row(
        Panel(disk_table, title="[bold green]Disk Status[/bold green]", border_style="cyan"),
        Panel(net_grid, title="[bold green]Network Traffic[/bold green]", border_style="cyan")
    )

    console.print(Panel(dash_table, title="[bold green]📊 System Resource Dashboard[/bold green]", border_style="green"))

if __name__ == "__main__":
    main()
