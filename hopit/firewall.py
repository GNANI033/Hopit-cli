import sys
import subprocess
import shutil
import shlex
import os
import re
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from hopit.config import IS_WINDOWS, IS_MACOS

console = Console()

def get_network_interfaces() -> list[str]:
    """Auto-detect active network interfaces on the host system."""
    if IS_WINDOWS:
        try:
            res = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-NetAdapter | Select-Object -ExpandProperty Name"], capture_output=True, text=True)
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            return lines if lines else ["Ethernet", "Wi-Fi"]
        except Exception:
            return ["Ethernet", "Wi-Fi"]
    elif IS_MACOS:
        try:
            res = subprocess.run(["networksetup", "-listallhardwareports"], capture_output=True, text=True)
            ifaces = []
            for line in res.stdout.splitlines():
                if "Device:" in line:
                    ifaces.append(line.split(":")[1].strip())
            return ifaces if ifaces else ["en0", "en1"]
        except Exception:
            return ["en0", "en1"]
    else:
        # Linux
        try:
            ifaces = [i for i in os.listdir("/sys/class/net") if i != "lo"]
            if ifaces:
                return ifaces
        except Exception:
            pass
        try:
            res = subprocess.run(["ip", "-o", "link"], capture_output=True, text=True)
            ifaces = []
            for line in res.stdout.splitlines():
                parts = line.split(":")
                if len(parts) >= 2:
                    name = parts[1].strip()
                    if name != "lo" and not name.startswith("veth"):
                        ifaces.append(name)
            return ifaces if ifaces else ["eth0", "wlan0"]
        except Exception:
            return ["eth0", "wlan0"]


def show_firewall_status_table():
    """Parses system firewall status and displays a sleek Rich Table."""
    table = Table(title="🛡️ Active Firewall Configuration & Rules", border_style="cyan", header_style="bold green")
    table.add_column("Rule / Service", style="bold white")
    table.add_column("Port", style="bright_yellow")
    table.add_column("Protocol", style="bright_cyan")
    table.add_column("Action", style="bold")
    table.add_column("Interface / Scope", style="magenta")
    table.add_column("Backend / Details", style="dim white")

    if IS_WINDOWS:
        try:
            cmd = ["powershell", "-NoProfile", "-Command",
                   "Get-NetFirewallRule | Where-Object {$_.Enabled -eq $True -and $_.Direction -eq 'Inbound'} | Select-Object DisplayName, Action, LocalPort | Format-Table -HideTableHeaders"]
            res = subprocess.run(cmd, capture_output=True, text=True)
            for line in res.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                name = parts[0] if parts else "Inbound Rule"
                action = "[bold green]ALLOW[/bold green]" if "Allow" in line else "[bold red]BLOCK[/bold red]"
                port = parts[-1] if len(parts) > 1 and parts[-1].isdigit() else "Any"
                table.add_row(name, port, "TCP/UDP", action, "Inbound", "Windows Firewall")
        except Exception as e:
            console.print(f"[red]Error reading Windows Firewall rules: {e}[/red]")
            return
    elif IS_MACOS:
        try:
            res = subprocess.run(["sudo", "pfctl", "-sr"], capture_output=True, text=True)
            for line in res.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                action = "[bold green]ALLOW[/bold green]" if line.startswith("pass") else "[bold red]BLOCK[/bold red]"
                port_match = re.search(r"port\s+(\d+|\w+)", line)
                port = port_match.group(1) if port_match else "Any"
                table.add_row("PF Rule", port, "TCP", action, "All", "macOS pfctl")
        except Exception:
            table.add_row("macOS PF", "Any", "ANY", "[yellow]ACTIVE[/yellow]", "All", "pfctl")
    else:
        # Linux
        if shutil.which("firewall-cmd"):
            res = subprocess.run(["firewall-cmd", "--list-all"], capture_output=True, text=True)
            zone_match = re.search(r"^(\w+)\s+\(active\)", res.stdout, re.MULTILINE)
            active_zone = zone_match.group(1) if zone_match else "default"
            
            iface_match = re.search(r"interfaces:\s*(.*)", res.stdout)
            ifaces = iface_match.group(1).strip() if iface_match else "all"
            
            svc_match = re.search(r"services:\s*(.*)", res.stdout)
            services = svc_match.group(1).strip().split() if svc_match and svc_match.group(1).strip() else []
            
            port_match = re.search(r"ports:\s*(.*)", res.stdout)
            ports = port_match.group(1).strip().split() if port_match and port_match.group(1).strip() else []
            
            rich_match = re.findall(r"rule family=.*?drop|rule family=.*?accept|rule family=.*?reject", res.stdout)
            
            for s in services:
                table.add_row(f"Service: {s}", "Default", "TCP/UDP", "[bold green]ALLOW[/bold green]", ifaces, f"firewalld ({active_zone})")
            
            for p in ports:
                parts = p.split("/")
                p_num = parts[0]
                p_proto = parts[1].upper() if len(parts) > 1 else "TCP"
                table.add_row("Allowed Port", p_num, p_proto, "[bold green]ALLOW[/bold green]", ifaces, f"firewalld ({active_zone})")
            
            for r in rich_match:
                p_match = re.search(r'port="(\d+)"', r)
                p_val = p_match.group(1) if p_match else "Any"
                act = "[bold red]BLOCK[/bold red]" if "drop" in r or "reject" in r else "[bold green]ALLOW[/bold green]"
                table.add_row("Rich Rule", p_val, "TCP", act, ifaces, f"firewalld ({active_zone})")
                
            if not services and not ports and not rich_match:
                table.add_row(f"Zone: {active_zone}", "Default", "ANY", "[bold green]ACTIVE[/bold green]", ifaces, "firewalld")

        elif shutil.which("ufw"):
            res = subprocess.run(["ufw", "status", "verbose"], capture_output=True, text=True)
            for line in res.stdout.splitlines():
                if "ALLOW" in line or "DENY" in line or "REJECT" in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        port = parts[0]
                        act = "[bold green]ALLOW[/bold green]" if "ALLOW" in parts[1] else "[bold red]BLOCK[/bold red]"
                        src = parts[2] if len(parts) > 2 else "Anywhere"
                        table.add_row("UFW Rule", port, "TCP/UDP", act, src, "UFW")
            if table.row_count == 0:
                table.add_row("UFW Firewall", "All Ports", "ANY", "[bold green]ACTIVE[/bold green]", "Anywhere", "ufw")

        elif shutil.which("nft"):
            res = subprocess.run(["nft", "list", "ruleset"], capture_output=True, text=True)
            for line in res.stdout.splitlines():
                line = line.strip()
                if "dport" in line:
                    act = "[bold green]ALLOW[/bold green]" if "accept" in line else "[bold red]BLOCK[/bold red]"
                    port_match = re.search(r"dport\s+(\d+)", line)
                    port = port_match.group(1) if port_match else "Any"
                    table.add_row("NFT Rule", port, "TCP", act, "All", "nftables")

        elif shutil.which("iptables"):
            res = subprocess.run(["iptables", "-L", "INPUT", "-n", "-v"], capture_output=True, text=True)
            for line in res.stdout.splitlines():
                if "dpt:" in line:
                    act = "[bold green]ALLOW[/bold green]" if "ACCEPT" in line else "[bold red]BLOCK[/bold red]"
                    port_match = re.search(r"dpt:(\d+)", line)
                    port = port_match.group(1) if port_match else "Any"
                    table.add_row("IPTables Rule", port, "TCP", act, "All", "iptables")

    console.print(table)


def run_firewall_rule(action: str, port: str, proto: str = "tcp", iface: str = "all") -> bool:
    """Executes a firewall rule with action, port, protocol, and optional interface."""
    action = action.lower()
    proto = proto.lower()
    
    if IS_WINDOWS:
        act = "Allow" if action == "allow" else "Block"
        rule_name = f"Hopit {action.capitalize()} Port {port} ({proto.upper()})"
        cmd = f"New-NetFirewallRule -DisplayName '{rule_name}' -Direction Inbound -LocalPort {port} -Protocol {proto.upper()} -Action {act}"
        res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
        return res.returncode == 0
    elif IS_MACOS:
        pf_act = "pass" if action == "allow" else "block"
        rule = f"{pf_act} in proto {proto} from any to any port {port}"
        res = subprocess.run(["bash", "-c", f"echo '{rule}' | sudo pfctl -f -"], capture_output=True, text=True)
        return res.returncode == 0
    else:
        # Linux
        if shutil.which("firewall-cmd"):
            if action == "allow":
                cmd = f"firewall-cmd --add-port={port}/{proto} --permanent && firewall-cmd --reload"
            else:
                cmd = f"firewall-cmd --remove-port={port}/{proto} --permanent 2>/dev/null; firewall-cmd --add-rich-rule='rule family=\"ipv4\" port port=\"{port}\" protocol=\"{proto}\" drop' --permanent && firewall-cmd --reload"
            res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
            return res.returncode == 0
        elif shutil.which("ufw"):
            ufw_act = "allow" if action == "allow" else "deny"
            res = subprocess.run(["ufw", ufw_act, f"{port}/{proto}"], capture_output=True, text=True)
            return res.returncode == 0
        elif shutil.which("nft"):
            nft_act = "accept" if action == "allow" else "drop"
            res = subprocess.run(["nft", "add", "rule", "inet", "filter", "input", proto, "dport", port, nft_act], capture_output=True, text=True)
            return res.returncode == 0
        elif shutil.which("iptables"):
            target_act = "ACCEPT" if action == "allow" else "DROP"
            res = subprocess.run(["iptables", "-A", "INPUT", "-p", proto, "--dport", port, "-j", target_act], capture_output=True, text=True)
            return res.returncode == 0
    return False


def interactive_firewall_setup():
    """Launches an interactive setup wizard for network firewall management."""
    console.print(Panel("[bold green]Hopit Interactive Firewall Setup Wizard[/bold green]\nConfigure ports, protocols, and network interfaces interactively.", border_style="cyan"))
    
    action = Prompt.ask("Choose action", choices=["allow", "block", "status"], default="allow")
    if action == "status":
        show_firewall_status_table()
        return

    port = Prompt.ask("Enter port number or range (e.g. 80, 443, 8080)")
    if not port:
        console.print("[yellow]Port number is required.[/yellow]")
        return
        
    proto = Prompt.ask("Select protocol", choices=["tcp", "udp", "both"], default="tcp")
    
    ifaces = get_network_interfaces()
    console.print(f"[cyan]Detected Network Interfaces:[/cyan] {', '.join(ifaces)}")
    iface = Prompt.ask("Select adapter / interface (or 'all')", default="all")
    
    console.print(f"\n[bold yellow]Rule Summary:[/bold yellow] {action.upper()} port [bold cyan]{port}[/bold cyan] ({proto.upper()}) on adapter [magenta]{iface}[/magenta]")
    if Confirm.ask("Apply this firewall rule now?", default=True):
        if proto == "both":
            r1 = run_firewall_rule(action, port, "tcp", iface)
            r2 = run_firewall_rule(action, port, "udp", iface)
            success = r1 and r2
        else:
            success = run_firewall_rule(action, port, proto, iface)
            
        if success:
            console.print("[bold green]✓ Firewall rule applied successfully![/bold green]")
            show_firewall_status_table()
        else:
            console.print("[bold red]✗ Failed to apply firewall rule. Ensure root/sudo privileges.[/bold red]")


def handle_firewall_cli(args: list[str]):
    """Main CLI handler for firewall single-line commands and subcommands."""
    if not args or args[0].lower() in ("interactive", "config", "wizard"):
        interactive_firewall_setup()
        return

    sub = args[0].lower()
    
    if sub == "status":
        show_firewall_status_table()
        return

    if sub in ("allow", "block", "deny"):
        if len(args) < 2:
            interactive_firewall_setup()
            return
            
        port = args[1]
        proto = args[2].lower() if len(args) > 2 and args[2].lower() in ("tcp", "udp", "both") else "tcp"
        iface = args[3] if len(args) > 3 else "all"
        
        act_name = "allow" if sub == "allow" else "block"
        
        if proto == "both":
            r1 = run_firewall_rule(act_name, port, "tcp", iface)
            r2 = run_firewall_rule(act_name, port, "udp", iface)
            success = r1 and r2
        else:
            success = run_firewall_rule(act_name, port, proto, iface)
            
        if success:
            console.print(f"[bold green]✓ Successfully applied {act_name.upper()} rule for port {port} ({proto.upper()})![/bold green]")
            show_firewall_status_table()
        else:
            console.print("[bold red]✗ Failed to apply firewall rule. Ensure root/sudo privileges.[/bold red]")
        return

    console.print(f"[yellow]Unknown firewall action: {sub}. Usage: firewall [status|allow|block|interactive][/yellow]")


def main():
    handle_firewall_cli(sys.argv[1:])

if __name__ == "__main__":
    main()
