import psutil
from rich.console import Console
from rich.table import Table

def main():
    console = Console()
    table = Table(border_style="cyan")
    table.add_column("Interface", style="green")
    table.add_column("MAC Address", style="magenta")
    table.add_column("Status", style="yellow")

    try:
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        
        # Sort interfaces
        for iface in sorted(addrs.keys()):
            mac = "N/A"
            for addr in addrs[iface]:
                if addr.family == psutil.AF_LINK:
                    mac = addr.address
                    break
            
            # Skip loopback interface MAC if it's not meaningful (e.g. 00:00:00:00:00:00)
            if iface.lower() in ("lo", "loopback", "lo0") and mac in ("00:00:00:00:00:00", "00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00"):
                continue
                
            is_up = stats.get(iface) and stats[iface].isup
            status = "Up" if is_up else "Down"
            table.add_row(iface, mac, status)
            
    except Exception as e:
        console.print(f"[red]Error fetching MAC addresses: {e}[/red]")
        return

    console.print(table)

if __name__ == "__main__":
    main()
