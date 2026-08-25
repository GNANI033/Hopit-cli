import sys
import socket
import subprocess
import shutil
from rich.panel import Panel
from rich.table import Table

def main():
    if len(sys.argv) < 2:
        print("Usage: dns <host_name>")
        sys.exit(1)

    host = sys.argv[1]
    # Prevent option injection and invalid characters
    if not host or host.startswith('-') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:' for c in host):
        print(f"Error: Invalid hostname or IP address '{host}'.")
        sys.exit(1)

    from hopit.config import console, get_active_theme
    theme = get_active_theme()
    border_color = theme.get("border", "cyan")
    
    grid = Table.grid(padding=(0, 2))
    grid.add_column("Type", style="bold cyan")
    grid.add_column("Value")

    # Resolve IPv4
    ips_v4 = set()
    try:
        ais = socket.getaddrinfo(host, None, socket.AF_INET)
        for ai in ais:
            ips_v4.add(ai[4][0])
    except Exception:
        pass

    # Resolve IPv6
    ips_v6 = set()
    try:
        ais = socket.getaddrinfo(host, None, socket.AF_INET6)
        for ai in ais:
            ips_v6.add(ai[4][0])
    except Exception:
        pass

    if not ips_v4 and not ips_v6:
        console.print(f"[red]Failed to resolve host '{host}'.[/red]")
        sys.exit(1)

    if ips_v4:
        grid.add_row("IPv4 Addresses", "\n".join(sorted(ips_v4)))
    if ips_v6:
        grid.add_row("IPv6 Addresses", "\n".join(sorted(ips_v6)))

    # Try resolving aliases/canonical name
    try:
        cname, aliases, _ = socket.gethostbyname_ex(host)
        if cname and cname != host:
            grid.add_row("Canonical Name", cname)
        if aliases:
            grid.add_row("Aliases", ", ".join(aliases))
    except Exception:
        pass

    # Check for MX and TXT records using dig or nslookup
    mx_records = []
    txt_records = []
    
    if shutil.which("dig"):
        try:
            out_mx = subprocess.run(["dig", "+short", "MX", host], capture_output=True, text=True, errors="ignore", timeout=3)
            for line in out_mx.stdout.splitlines():
                if line.strip():
                    mx_records.append(line.strip())
            out_txt = subprocess.run(["dig", "+short", "TXT", host], capture_output=True, text=True, errors="ignore", timeout=3)
            for line in out_txt.stdout.splitlines():
                if line.strip():
                    txt_records.append(line.strip())
        except Exception:
            pass
    elif shutil.which("nslookup"):
        try:
            out_mx = subprocess.run(["nslookup", "-type=MX", host], capture_output=True, text=True, errors="ignore", timeout=3)
            for line in out_mx.stdout.splitlines():
                if "mail exchanger" in line or "MX preference" in line:
                    mx_records.append(line.strip())
            out_txt = subprocess.run(["nslookup", "-type=TXT", host], capture_output=True, text=True, errors="ignore", timeout=3)
            for line in out_txt.stdout.splitlines():
                if "text =" in line:
                    txt_records.append(line.strip())
        except Exception:
            pass

    if mx_records:
        grid.add_row("MX Records", "\n".join(mx_records[:5]))
    if txt_records:
        grid.add_row("TXT Records", "\n".join(txt_records[:5]))

    console.print(Panel(grid, title=f"[bold green]DNS Query: {host}[/bold green]", border_style=border_color))

if __name__ == "__main__":
    main()
