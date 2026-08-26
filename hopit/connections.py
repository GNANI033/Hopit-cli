import psutil
from rich.console import Console
from rich.table import Table
import socket

from hopit.config import safe_entrypoint

@safe_entrypoint
def main():
    console = Console()
    table = Table(border_style="cyan")
    table.add_column("Proto", style="cyan")
    table.add_column("Local Address", style="green")
    table.add_column("Remote Address", style="yellow")
    table.add_column("Status", style="blue")
    table.add_column("Process (PID)", style="magenta")

    # Resolve process names
    proc_cache = {}
    def get_proc_name(pid):
        if not pid:
            return "N/A"
        if pid not in proc_cache:
            try:
                proc_cache[pid] = f"{psutil.Process(pid).name()} ({pid})"
            except Exception:
                proc_cache[pid] = f"Unknown ({pid})"
        return proc_cache[pid]

    try:
        conns = psutil.net_connections(kind='inet')
    except Exception as e:
        console.print(f"[red]Error fetching connections: {e}[/red]")
        return

    # Sort connections: TCP first, then by Local Port
    conns.sort(key=lambda x: (x.type, x.laddr.port if x.laddr else 0))

    # Show top 40 connections to keep the UI clean
    for c in conns[:40]:
        proto = "TCP" if c.type == socket.SOCK_STREAM else "UDP"
        
        laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "N/A"
        raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "*"
        status = c.status if proto == "TCP" else "-"
        proc = get_proc_name(c.pid)
        
        table.add_row(proto, laddr, raddr, status, proc)

    console.print(table)
    if len(conns) > 40:
        console.print(f"[dim]... and {len(conns) - 40} more connections. Use 'netstat' for full output.[/dim]")

if __name__ == "__main__":
    main()
