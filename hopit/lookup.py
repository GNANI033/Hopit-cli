import sys
import socket
import subprocess
import shutil
import time
import platform
import urllib.request
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.live import Live
from rich.rule import Rule

def query_dns_record(host, record_type):
    record_type = record_type.upper()
    results = []
    
    # 1. Try using dig if available
    if shutil.which("dig"):
        try:
            out = subprocess.run(["dig", "+short", record_type, host], capture_output=True, text=True, timeout=3)
            for line in out.stdout.splitlines():
                line = line.strip()
                if line:
                    results.append(line.strip('"'))
            if results:
                return results
        except Exception:
            pass

    # 2. Try using nslookup if available
    if shutil.which("nslookup"):
        try:
            out = subprocess.run(["nslookup", f"-type={record_type}", host], capture_output=True, text=True, timeout=3)
            lines = out.stdout.splitlines()
            answer_section = False
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                # Skip nslookup server header
                if "Non-authoritative answer" in line or answer_section:
                    answer_section = True
                if not answer_section and (line.startswith("Server:") or line.startswith("Address:")):
                    continue
                
                if record_type == "MX":
                    if "mail exchanger" in line or "MX preference" in line:
                        val = line.split("=", 1)[1].strip() if "=" in line else line.strip()
                        results.append(val.strip('"'))
                elif record_type == "TXT":
                    if "text =" in line:
                        val = line.split("text =", 1)[1].strip()
                        results.append(val.strip('"'))
                    elif "text" in line and "=" in line:
                        val = line.split("=", 1)[1].strip()
                        results.append(val.strip('"'))
                elif record_type == "CNAME":
                    if "canonical name" in line:
                        val = line.split("canonical name =", 1)[1].strip()
                        results.append(val)
                    elif "cname" in line and "=" in line:
                        val = line.split("=", 1)[1].strip()
                        results.append(val)
                elif record_type == "NS":
                    if "nameserver =" in line:
                        val = line.split("nameserver =", 1)[1].strip()
                        results.append(val)
                    elif "nameserver" in line and "=" in line:
                        val = line.split("=", 1)[1].strip()
                        results.append(val)
                elif record_type in ("A", "AAAA"):
                    if line.startswith("Address:"):
                        addr = line.split("Address:", 1)[1].strip()
                        if addr and not addr.startswith("#"):
                            results.append(addr)
                    elif line.startswith("Addresses:"):
                        addr = line.split("Addresses:", 1)[1].strip()
                        if addr:
                            results.append(addr)
            if results:
                # Remove duplicates while preserving order
                seen = set()
                return [x for x in results if not (x in seen or seen.add(x))]
        except Exception:
            pass

    # 3. Fallback to python socket for basic records
    if record_type == "A":
        try:
            ais = socket.getaddrinfo(host, None, socket.AF_INET)
            return sorted(list(set(ai[4][0] for ai in ais)))
        except Exception:
            pass
    elif record_type == "AAAA":
        try:
            ais = socket.getaddrinfo(host, None, socket.AF_INET6)
            return sorted(list(set(ai[4][0] for ai in ais)))
        except Exception:
            pass
    elif record_type == "CNAME":
        try:
            cname, _, _ = socket.gethostbyname_ex(host)
            if cname and cname != host:
                return [cname]
        except Exception:
            pass

    return results

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
        output = proc.stdout or ""
        
        loss = "100%"
        rtt = "N/A"
        
        output_lower = output.lower()
        if "packet loss" in output_lower:
            for line in output.splitlines():
                if "packet loss" in line.lower():
                    parts = line.split("packet loss")[0].split(",")
                    if parts:
                        loss = parts[-1].strip()
                if "min/avg/max" in line.lower() or "rtt min/avg/max" in line.lower():
                    if "=" in line:
                        rtt = line.split("=")[1].strip()
                    else:
                        rtt = line.strip()
        elif "loss" in output_lower:
            for line in output.splitlines():
                if "lost =" in line.lower():
                    parts = line.split("(")
                    if len(parts) > 1:
                        loss = parts[1].split()[0]
                if "minimum =" in line.lower():
                    if "=" in line:
                        rtt = line.split("=")[-1].strip()
                    else:
                        rtt = line.strip()
                    
        return {"success": proc.returncode == 0, "loss": loss, "rtt": rtt}
    except Exception as e:
        return {"success": False, "loss": f"Error: {e}", "rtt": "N/A"}

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
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
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

def get_all_dns_records(target):
    record_types = ["A", "AAAA", "CNAME", "MX", "TXT", "NS"]
    records = {}
    for rtype in record_types:
        res = query_dns_record(target, rtype)
        if res:
            records[rtype] = res
    return records

def build_dns_records_table(dns_records, target):
    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Record Type", style="bold cyan", width=15)
    table.add_column("Value", style="green")
    
    found_any = False
    for rtype in ["A", "AAAA", "CNAME", "MX", "TXT", "NS"]:
        results = dns_records.get(rtype)
        if results:
            found_any = True
            for idx, val in enumerate(results):
                type_str = rtype if idx == 0 else ""
                table.add_row(type_str, val)
            table.add_row("", "")
            
    return table, found_any

def main():
    if len(sys.argv) < 2:
        print("Usage: lookup [A|AAAA|CNAME|MX|TXT|NS|all] <host_or_ip>")
        sys.exit(1)
        
    subcmd = "DASHBOARD"
    target = ""
    
    if len(sys.argv) >= 3:
        first_arg = sys.argv[1].upper()
        if first_arg in ("A", "AAAA", "CNAME", "MX", "TXT", "NS"):
            subcmd = first_arg
            target = sys.argv[2]
        elif first_arg == "ALL":
            subcmd = "ALL"
            target = sys.argv[2]
        else:
            subcmd = "DASHBOARD"
            target = sys.argv[1]
    else:
        target = sys.argv[1]
        subcmd = "DASHBOARD"
        
    # Prevent option injection and invalid characters
    if not target or target.startswith('-') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:' for c in target):
        print(f"Error: Invalid target hostname or IP address '{target}'.")
        sys.exit(1)

    from hopit.config import console, get_active_theme
    theme = get_active_theme()
    border_color = theme.get("border", "cyan")
    
    if subcmd in ("A", "AAAA", "CNAME", "MX", "TXT", "NS"):
        results = query_dns_record(target, subcmd)
        if not results:
            console.print(Panel(f"[red]No {subcmd} records found for '{target}' (or query failed).[/red]", title=f"[bold red]DNS Lookup: {subcmd} ({target})[/bold red]", border_style="red"))
            sys.exit(1)
            
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Index", style="bold cyan")
        table.add_column("Value")
        for idx, val in enumerate(results, 1):
            table.add_row(f"#{idx}", val)
            
        console.print(Panel(table, title=f"[bold green]DNS Lookup: {subcmd} records for {target}[/bold green]", border_style=border_color))
        sys.exit(0)

    results = {}
    
    def generate_diagnostic_summary(res):
        parts = []
        if "ping" not in res and "http" not in res:
            return None
            
        p = res.get("ping")
        h = res.get("http")
        
        if p:
            loss = p.get("loss", "100%")
            if loss == "0%":
                parts.append("[green]✓ Ping: stable (0% loss)[/green]")
            elif loss == "100%":
                parts.append("[red]✗ Ping: unreachable (100% loss)[/red]")
            elif "error" in loss.lower():
                parts.append(f"[yellow]⚠ Ping: error ({loss})[/yellow]")
            else:
                parts.append(f"[yellow]⚠ Ping: degraded ({loss} loss)[/yellow]")
                
            rtt = p.get("rtt", "N/A")
            if rtt != "N/A" and "/" in rtt:
                try:
                    avg_rtt = rtt.split("/")[1]
                    parts.append(f"  Latency: [cyan]{avg_rtt} ms[/cyan] avg")
                except Exception:
                    pass
                    
        if h:
            status = str(h.get("status", "N/A"))
            server = h.get("server", "N/A")
            latency = h.get("latency", "N/A")
            
            if "200" in status or "OK" in status:
                parts.append(f"[green]✓ HTTP: active ({latency})[/green]")
            elif "HTTP Error" in status:
                parts.append(f"[red]✗ HTTP: returned {status}[/red]")
            elif "Error" in status:
                parts.append(f"[red]✗ HTTP: failed ({status})[/red]")
            else:
                parts.append(f"[yellow]⚠ HTTP: status {status}[/yellow]")
                
            if server and server != "N/A":
                parts.append(f"  Server: [bold]{server}[/bold]")
                
        if p and h:
            p_ok = p.get("success", False) and p.get("loss") == "0%"
            h_ok = "200" in str(h.get("status", "")) or "OK" in str(h.get("status", ""))
            
            parts.append("")
            if p_ok and h_ok:
                parts.append("[bold green]Conclusion:[/bold green] Host is healthy & active.")
            elif p_ok and not h_ok:
                sc = str(h.get("status", ""))
                if "403" in sc:
                    parts.append("[bold yellow]Conclusion:[/bold yellow] Reachable, but access restricted (403).")
                elif "401" in sc or "407" in sc:
                    parts.append("[bold yellow]Conclusion:[/bold yellow] Reachable, auth required.")
                else:
                    parts.append("[bold red]Conclusion:[/bold red] Reachable, but HTTP service failed.")
            elif not p_ok and h_ok:
                parts.append("[bold yellow]Conclusion:[/bold yellow] HTTP is up, ping is blocked.")
            else:
                parts.append("[bold red]Conclusion:[/bold red] Host is completely offline.")
                
        return "\n".join(parts)
    
    def generate_dashboard():
        table = Table.grid(padding=1)
        table.add_column("Col")
        
        # Header
        table.add_row(f"[bold green]🔍 Consolidating Diagnostic Lookup for: {target}[/bold green]\n")
        
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
 
        summary_text = generate_diagnostic_summary(results)
        if summary_text:
            http_content = Group(
                http_table,
                Rule(style="dim cyan"),
                summary_text
            )
        else:
            http_content = http_table
        
        if subcmd == "ALL":
            # DNS records on the left
            if "dns_records" in results:
                dns_records_table, found = build_dns_records_table(results["dns_records"], target)
                if found:
                    left_panel = Panel(dns_records_table, title=f"[bold green]DNS Lookup: ALL records for {target}[/bold green]", border_style=border_color)
                else:
                    left_panel = Panel(f"[red]No DNS records found for '{target}' (or query failed).[/red]", title=f"[bold red]DNS Lookup: ALL ({target})[/bold red]", border_style="red")
            else:
                left_panel = Panel("[yellow]Querying comprehensive DNS records...[/yellow]", title=f"[bold green]DNS Lookup: ALL records for {target}[/bold green]", border_style=border_color)

            # Diagnostics stacked on the right (Ping + HTTP)
            right_grid = Table.grid(padding=1)
            right_grid.add_column("Col")
            right_grid.add_row(Panel(ping_table, title="[bold green]Ping Latency Test[/bold green]", border_style=border_color))
            right_grid.add_row(Panel(http_content, title="[bold green]HTTP Web Probe[/bold green]", border_style=border_color))

            # Upper row containing DNS Lookup on left, Ping + HTTP on right
            top_grid = Table.grid(padding=1)
            top_grid.add_column("LeftCol")
            top_grid.add_column("RightCol")
            top_grid.add_row(left_panel, right_grid)
            
            # Add top grid and then the Route Trace panel full width below it
            table.add_row(top_grid)
            table.add_row(Panel(trace_table, title="[bold green]Route Trace[/bold green]", border_style=border_color))
        else:
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

            grids_table = Table.grid(padding=1)
            grids_table.add_column("Col1")
            grids_table.add_column("Col2")
            
            grids_table.add_row(
                Panel(dns_table, title="[bold green]DNS Resolution[/bold green]", border_style=border_color),
                Panel(ping_table, title="[bold green]Ping Latency Test[/bold green]", border_style=border_color)
            )
            grids_table.add_row(
                Panel(http_content, title="[bold green]HTTP Web Probe[/bold green]", border_style=border_color),
                Panel(trace_table, title="[bold green]Route Trace[/bold green]", border_style=border_color)
            )
            table.add_row(grids_table)

        return table
 
    with Live(generate_dashboard(), console=console, refresh_per_second=4) as live:
        # Step 1: DNS
        if subcmd == "ALL":
            results["dns_records"] = get_all_dns_records(target)
        else:
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
