import sys
import socket
import subprocess
import shutil
import time
import platform
import urllib.request
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.live import Live

def resolve_dns(target):
    info = {"ipv4": [], "ipv6": [], "ptr": None, "cname": None}
    
    # Check if target is an IP
    is_ip = False
    try:
        socket.inet_aton(target)
        is_ip = True
    except socket.error:
        try:
            socket.inet_pton(socket.AF_INET6, target)
            is_ip = True
        except socket.error:
            pass

    if is_ip:
        try:
            name, _, _ = socket.gethostbyaddr(target)
            info["ptr"] = name
        except Exception:
            pass
    else:
        try:
            ais = socket.getaddrinfo(target, None, socket.AF_INET)
            info["ipv4"] = list(set(ai[4][0] for ai in ais))
        except Exception:
            pass
        try:
            ais = socket.getaddrinfo(target, None, socket.AF_INET6)
            info["ipv6"] = list(set(ai[4][0] for ai in ais))
        except Exception:
            pass
        try:
            cname, _, _ = socket.gethostbyname_ex(target)
            if cname and cname != target:
                info["cname"] = cname
        except Exception:
            pass
            
    return info

def run_ping(target):
    system = platform.system()
    if system == "Windows":
        cmd = ["ping", "-n", "3", target]
    else:
        cmd = ["ping", "-c", "3", "-W", "2", target]
        
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        output = proc.stdout
        
        loss = "100%"
        rtt = "N/A"
        
        output_lower = output.lower()
        if "packet loss" in output_lower:
            for line in output.splitlines():
                if "packet loss" in line.lower():
                    loss = line.split("packet loss")[0].split(",")[-1].strip()
                if "min/avg/max" in line.lower() or "rtt min/avg/max" in line.lower():
                    rtt = line.split("=")[1].strip()
        elif "loss" in output_lower:
            for line in output.splitlines():
                if "lost =" in line.lower():
                    parts = line.split("(")
                    if len(parts) > 1:
                        loss = parts[1].split()[0]
                if "minimum =" in line.lower():
                    rtt = line.strip()
                    
        return {"success": proc.returncode == 0, "loss": loss, "rtt": rtt}
    except Exception as e:
        return {"success": False, "loss": "Error", "rtt": "N/A"}

def run_traceroute(target):
    system = platform.system()
    if system == "Windows":
        cmd = ["tracert", "-h", "10", "-d", target]
    else:
        # Use traceroute/tracepath limiting max hops to 10 for quick diagnostic
        if shutil.which("traceroute"):
            cmd = ["traceroute", "-m", "10", "-n", target]
        elif shutil.which("tracepath"):
            cmd = ["tracepath", "-m", "10", target]
        else:
            return ["traceroute/tracepath command not found on host path"]
            
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        hops = []
        for line in proc.stdout.splitlines():
            line_str = line.strip()
            if line_str and not line_str.startswith("traceroute") and not line_str.startswith("Tracing route") and not line_str.startswith("tracepath"):
                hops.append(line_str)
        return hops[:10] if hops else ["No hops recorded"]
    except Exception as e:
        return [f"Traceroute failed: {e}"]

def check_http(target):
    # Check if target resolves before HTTP probe
    try:
        socket.gethostbyname(target)
    except Exception:
        return {"status": "Name Not Resolved", "server": "N/A", "latency": "N/A"}

    url = target if (target.startswith("http://") or target.startswith("https://")) else f"https://{target}"
    start_time = time.time()
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Hopit-CLI/1.0'}
        )
        with urllib.request.urlopen(req, timeout=4) as response:
            status = response.status
            server = response.headers.get("Server", "Unknown")
            latency = f"{(time.time() - start_time) * 1000:.0f} ms"
            return {"status": f"{status} OK", "server": server, "latency": latency}
    except Exception as e:
        if url.startswith("https://"):
            url_http = url.replace("https://", "http://")
            start_time = time.time()
            try:
                req = urllib.request.Request(
                    url_http, 
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Hopit-CLI/1.0'}
                )
                with urllib.request.urlopen(req, timeout=4) as response:
                    status = response.status
                    server = response.headers.get("Server", "Unknown")
                    latency = f"{(time.time() - start_time) * 1000:.0f} ms"
                    return {"status": f"{status} OK (HTTP)", "server": server, "latency": latency}
            except Exception as e2:
                err_msg = str(e2)
        else:
            err_msg = str(e)
            
        status_txt = "Failed Connection"
        if "HTTP Error" in err_msg:
            status_txt = err_msg.split(":")[0]
        elif "timeout" in err_msg.lower():
            status_txt = "Timeout"
            
        return {"status": status_txt, "server": "N/A", "latency": "N/A"}

def main():
    if len(sys.argv) < 2:
        print("Usage: lookup <host_or_ip>")
        sys.exit(1)
        
    target = sys.argv[1]
    # Prevent option injection and invalid characters
    if not target or target.startswith('-') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:' for c in target):
        print(f"Error: Invalid target hostname or IP address '{target}'.")
        sys.exit(1)

    console = Console()
    
    results = {}
    
    def generate_dashboard():
        table = Table.grid(padding=1)
        table.add_column("Col")
        
        # Header
        table.add_row(f"[bold green]🔍 Consolidating Diagnostic Lookup for: {target}[/bold green]\n")
        
        # DNS Resolution
        dns_status = "[yellow]Querying DNS...[/yellow]" if "dns" not in results else "[green]Done[/green]"
        dns_table = Table.grid(padding=(0, 2))
        dns_table.add_column("Key", style="bold cyan")
        dns_table.add_column("Value")
        
        if "dns" in results:
            dns_info = results["dns"]
            if dns_info.get("ptr"):
                dns_table.add_row("PTR (Reverse DNS)", dns_info["ptr"])
            if dns_info.get("cname"):
                dns_table.add_row("Canonical Name", dns_info["cname"])
            if dns_info.get("ipv4"):
                dns_table.add_row("IPv4 Addresses", "\n".join(dns_info["ipv4"]))
            if dns_info.get("ipv6"):
                dns_table.add_row("IPv6 Addresses", "\n".join(dns_info["ipv6"]))
            if not dns_info.get("ptr") and not dns_info.get("ipv4") and not dns_info.get("ipv6"):
                dns_table.add_row("Result", "Resolution Failed")
        else:
            dns_table.add_row("Status", dns_status)
            
        # Ping latency test
        ping_status = "[yellow]Running Ping...[/yellow]" if "ping" not in results else "[green]Done[/green]"
        ping_table = Table.grid(padding=(0, 2))
        ping_table.add_column("Key", style="bold cyan")
        ping_table.add_column("Value")
        
        if "ping" in results:
            ping_info = results["ping"]
            ping_table.add_row("Success", "[green]Yes[/green]" if ping_info["success"] else "[red]No[/red]")
            ping_table.add_row("Packet Loss", ping_info["loss"])
            ping_table.add_row("RTT Stats", ping_info["rtt"])
        else:
            ping_table.add_row("Status", ping_status)
            
        # HTTP port probe
        http_status = "[yellow]Probing HTTP/HTTPS...[/yellow]" if "http" not in results else "[green]Done[/green]"
        http_table = Table.grid(padding=(0, 2))
        http_table.add_column("Key", style="bold cyan")
        http_table.add_column("Value")
        
        if "http" in results:
            h = results["http"]
            status_style = "green" if "OK" in str(h["status"]) or "200" in str(h["status"]) else "red"
            http_table.add_row("HTTP Status", f"[{status_style}]{h['status']}[/{status_style}]")
            http_table.add_row("Web Server", h["server"])
            http_table.add_row("Latency", h["latency"])
        else:
            http_table.add_row("Status", http_status)

        # Traceroute
        trace_status = "[yellow]Tracing route...[/yellow]" if "trace" not in results else "[green]Done[/green]"
        trace_table = Table.grid(padding=(0, 2))
        trace_table.add_column("Hop", style="bold cyan")
        trace_table.add_column("Details")
        
        if "trace" in results:
            for idx, hop in enumerate(results["trace"]):
                trace_table.add_row(f"#{idx+1}", hop)
        else:
            trace_table.add_row("Status", trace_status)

        grids_table = Table.grid(padding=1)
        grids_table.add_column("Col1", width=42)
        grids_table.add_column("Col2", width=42)
        
        grids_table.add_row(
            Panel(dns_table, title="[bold green]DNS Resolution[/bold green]", border_style="cyan"),
            Panel(ping_table, title="[bold green]Ping Latency Test[/bold green]", border_style="cyan")
        )
        
        grids_table.add_row(
            Panel(http_table, title="[bold green]HTTP Web Probe[/bold green]", border_style="cyan"),
            Panel(trace_table, title="[bold green]Route Trace[/bold green]", border_style="cyan")
        )
        
        table.add_row(grids_table)
        return table

    with Live(generate_dashboard(), console=console, refresh_per_second=4) as live:
        # Step 1: DNS
        results["dns"] = resolve_dns(target)
        live.update(generate_dashboard())
        
        # Step 2: Ping
        results["ping"] = run_ping(target)
        live.update(generate_dashboard())
        
        # Step 3: HTTP
        results["http"] = check_http(target)
        live.update(generate_dashboard())
        
        # Step 4: Traceroute
        results["trace"] = run_traceroute(target)
        live.update(generate_dashboard())

if __name__ == "__main__":
    main()
