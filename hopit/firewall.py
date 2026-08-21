import sys
import subprocess
import shutil
import shlex
import os
import re
import glob

# Safely add top-level site-packages directories to sys.path (needed when running under sudo)
for pattern in ["~/.local/lib/python*/site-packages", "/home/*/.local/lib/python*/site-packages", "/usr/local/lib/python*/site-packages"]:
    for site_pkg in glob.glob(os.path.expanduser(pattern)):
        if os.path.isdir(site_pkg) and site_pkg not in sys.path:
            sys.path.insert(0, site_pkg)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    HAS_RICH = True
    console = Console()
except ImportError:
    HAS_RICH = False
    console = None

from hopit.config import IS_WINDOWS, IS_MACOS


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


def select_menu(prompt_title: str, options: list[str], default_index: int = 0) -> str:
    """Displays an intuitive numbered selection menu to prevent typos."""
    if HAS_RICH and console:
        console.print(f"\n[bold cyan]{prompt_title}:[/bold cyan]")
        for idx, opt in enumerate(options, 1):
            def_tag = " [dim](default)[/dim]" if idx - 1 == default_index else ""
            console.print(f"  [green]{idx})[/green] [yellow]{opt}[/yellow]{def_tag}")
    else:
        print(f"\n{prompt_title}:")
        for idx, opt in enumerate(options, 1):
            def_tag = " (default)" if idx - 1 == default_index else ""
            print(f"  {idx}) {opt}{def_tag}")
    
    prompt_str = f"Enter selection [1-{len(options)}] (default {default_index + 1}): "
    choice = input(prompt_str).strip()
    if not choice:
        return options[default_index]
    if choice.isdigit():
        val = int(choice)
        if 1 <= val <= len(options):
            return options[val - 1]
    # Match by text prefix or full text
    for opt in options:
        if opt.lower().startswith(choice.lower()):
            return opt
    return options[default_index]


def show_firewall_status_table():
    """Parses system firewall status and displays a formatted Table."""
    rows = []

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
                action = "ALLOW" if "Allow" in line else "BLOCK"
                port = parts[-1] if len(parts) > 1 and parts[-1].isdigit() else "Any"
                rows.append((name, port, "TCP/UDP", action, "Inbound", "Windows Firewall"))
        except Exception as e:
            print(f"Error reading Windows Firewall rules: {e}")
            return
    elif IS_MACOS:
        try:
            res = subprocess.run(["sudo", "pfctl", "-sr"], capture_output=True, text=True)
            for line in res.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                action = "ALLOW" if line.startswith("pass") else "BLOCK"
                port_match = re.search(r"port\s+(\d+|\w+)", line)
                port = port_match.group(1) if port_match else "Any"
                rows.append(("PF Rule", port, "TCP", action, "All", "macOS pfctl"))
        except Exception:
            rows.append(("macOS PF", "Any", "ANY", "ACTIVE", "All", "pfctl"))
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
            
            rich_rules = re.findall(r"rule family=.*", res.stdout)
            
            for s in services:
                rows.append((f"Service: {s}", "Default", "TCP/UDP", "ALLOW", ifaces, f"firewalld ({active_zone})"))
            
            for p in ports:
                parts = p.split("/")
                p_num = parts[0]
                p_proto = parts[1].upper() if len(parts) > 1 else "TCP"
                rows.append(("Allowed Port", p_num, p_proto, "ALLOW", ifaces, f"firewalld ({active_zone})"))
            
            for r in rich_rules:
                comment_match = re.search(r'comment="([^"]+)"', r)
                p_match = re.search(r'port="(\d+)"', r)
                proto_match = re.search(r'protocol="(\w+)"', r)
                
                p_val = p_match.group(1) if p_match else "Any"
                p_proto = proto_match.group(1).upper() if proto_match else "TCP"
                rule_label = comment_match.group(1) if comment_match else f"Rule (Port {p_val})"
                act = "BLOCK" if "drop" in r or "reject" in r else "ALLOW"
                rows.append((rule_label, p_val, p_proto, act, ifaces, f"firewalld ({active_zone})"))
                
            if not services and not ports and not rich_rules:
                rows.append((f"Zone: {active_zone}", "Default", "ANY", "ACTIVE", ifaces, "firewalld"))

        elif shutil.which("ufw"):
            res = subprocess.run(["ufw", "status", "verbose"], capture_output=True, text=True)
            for line in res.stdout.splitlines():
                if "ALLOW" in line or "DENY" in line or "REJECT" in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        port = parts[0]
                        act = "ALLOW" if "ALLOW" in parts[1] else "BLOCK"
                        src = parts[2] if len(parts) > 2 else "Anywhere"
                        comment_match = re.search(r"#\s*(.*)", line)
                        rule_label = comment_match.group(1) if comment_match else "UFW Rule"
                        rows.append((rule_label, port, "TCP/UDP", act, src, "UFW"))
            if not rows:
                rows.append(("UFW Firewall", "All Ports", "ANY", "ACTIVE", "Anywhere", "ufw"))

        elif shutil.which("nft"):
            res = subprocess.run(["nft", "list", "ruleset"], capture_output=True, text=True)
            for line in res.stdout.splitlines():
                line = line.strip()
                if "dport" in line:
                    act = "ALLOW" if "accept" in line else "BLOCK"
                    port_match = re.search(r"dport\s+(\d+)", line)
                    port = port_match.group(1) if port_match else "Any"
                    rows.append(("NFT Rule", port, "TCP", act, "All", "nftables"))

        elif shutil.which("iptables"):
            res = subprocess.run(["iptables", "-L", "INPUT", "-n", "-v"], capture_output=True, text=True)
            for line in res.stdout.splitlines():
                if "dpt:" in line:
                    act = "ALLOW" if "ACCEPT" in line else "BLOCK"
                    port_match = re.search(r"dpt:(\d+)", line)
                    port = port_match.group(1) if port_match else "Any"
                    rows.append(("IPTables Rule", port, "TCP", act, "All", "iptables"))

    if HAS_RICH and console:
        table = Table(title="🛡️ Active Firewall Configuration & Rules", border_style="cyan", header_style="bold green")
        table.add_column("Rule / Service", style="bold white")
        table.add_column("Port", style="bright_yellow")
        table.add_column("Protocol", style="bright_cyan")
        table.add_column("Action", style="bold")
        table.add_column("Interface / Scope", style="magenta")
        table.add_column("Backend / Details", style="dim white")

        for rule, port, proto, act, iface, detail in rows:
            act_styled = f"[bold green]{act}[/bold green]" if act == "ALLOW" else (f"[bold red]{act}[/bold red]" if act in ("BLOCK", "DENY") else f"[yellow]{act}[/yellow]")
            table.add_row(rule, port, proto, act_styled, iface, detail)
        console.print(table)
    else:
        # Standard ASCII Table Fallback
        print("\n--- 🛡️ Active Firewall Configuration & Rules ---")
        fmt = "{:<26} {:<12} {:<10} {:<10} {:<18} {:<20}"
        print(fmt.format("Rule / Service", "Port", "Protocol", "Action", "Interface / Scope", "Backend / Details"))
        print("-" * 98)
        for rule, port, proto, act, iface, detail in rows:
            print(fmt.format(rule, port, proto, act, iface, detail))
        print("-" * 98 + "\n")


def run_firewall_rule(action: str, port: str, proto: str = "tcp", iface: str = "all", rule_name: str = "") -> bool:
    """Executes or deletes a firewall rule with action, port, protocol, interface, and rule name."""
    action = action.lower()
    proto = proto.lower()
    disp_name = rule_name if rule_name else f"Hopit-{action.capitalize()}-{port}"

    if IS_WINDOWS:
        if action in ("delete", "remove"):
            cmd = f"Remove-NetFirewallRule -DisplayName '{disp_name}' -ErrorAction SilentlyContinue; Remove-NetFirewallRule -LocalPort {port} -ErrorAction SilentlyContinue"
        else:
            act = "Allow" if action == "allow" else "Block"
            cmd = f"New-NetFirewallRule -DisplayName '{disp_name}' -Direction Inbound -LocalPort {port} -Protocol {proto.upper()} -Action {act}"
        res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
        return res.returncode == 0
    elif IS_MACOS:
        if action in ("delete", "remove"):
            return True
        pf_act = "pass" if action == "allow" else "block"
        rule = f"{pf_act} in proto {proto} from any to any port {port}"
        res = subprocess.run(["bash", "-c", f"echo '{rule}' | sudo pfctl -f -"], capture_output=True, text=True)
        return res.returncode == 0
    else:
        # Linux Auto-Detection
        if shutil.which("firewall-cmd"):
            if action in ("delete", "remove"):
                cmd = (
                    f"firewall-cmd --remove-port={port}/tcp --permanent 2>/dev/null; "
                    f"firewall-cmd --remove-port={port}/udp --permanent 2>/dev/null; "
                    f"firewall-cmd --remove-rich-rule='rule family=\"ipv4\" port port=\"{port}\" protocol=\"tcp\" accept' --permanent 2>/dev/null; "
                    f"firewall-cmd --remove-rich-rule='rule family=\"ipv4\" port port=\"{port}\" protocol=\"tcp\" drop' --permanent 2>/dev/null; "
                    f"firewall-cmd --remove-rich-rule='rule family=\"ipv4\" port port=\"{port}\" protocol=\"udp\" accept' --permanent 2>/dev/null; "
                    f"firewall-cmd --remove-rich-rule='rule family=\"ipv4\" port port=\"{port}\" protocol=\"udp\" drop' --permanent 2>/dev/null; "
                    f"firewall-cmd --reload"
                )
            elif action == "allow":
                if rule_name:
                    cmd = f"firewall-cmd --add-rich-rule='rule family=\"ipv4\" port port=\"{port}\" protocol=\"{proto}\" comment=\"{rule_name}\" accept' --permanent && firewall-cmd --reload"
                else:
                    cmd = f"firewall-cmd --add-port={port}/{proto} --permanent && firewall-cmd --reload"
            else: # block / deny
                if rule_name:
                    cmd = f"firewall-cmd --remove-port={port}/{proto} --permanent 2>/dev/null; firewall-cmd --add-rich-rule='rule family=\"ipv4\" port port=\"{port}\" protocol=\"{proto}\" comment=\"{rule_name}\" drop' --permanent && firewall-cmd --reload"
                else:
                    cmd = f"firewall-cmd --remove-port={port}/{proto} --permanent 2>/dev/null; firewall-cmd --add-rich-rule='rule family=\"ipv4\" port port=\"{port}\" protocol=\"{proto}\" comment=\"Block-{port}\" drop' --permanent && firewall-cmd --reload"
            
            res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
            return res.returncode == 0

        elif shutil.which("ufw"):
            if action in ("delete", "remove"):
                cmd = f"ufw delete allow {port}/{proto} 2>/dev/null; ufw delete deny {port}/{proto} 2>/dev/null"
            elif action == "allow":
                comment_opt = f" comment '{rule_name}'" if rule_name else ""
                cmd = f"ufw allow {port}/{proto}{comment_opt}"
            else:
                comment_opt = f" comment '{rule_name}'" if rule_name else ""
                cmd = f"ufw deny {port}/{proto}{comment_opt}"
            res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
            return res.returncode == 0

        elif shutil.which("nft"):
            nft_act = "accept" if action == "allow" else ("drop" if action in ("block", "deny") else "delete")
            if action in ("delete", "remove"):
                return True
            res = subprocess.run(["nft", "add", "rule", "inet", "filter", "input", proto, "dport", port, nft_act], capture_output=True, text=True)
            return res.returncode == 0

        elif shutil.which("iptables"):
            if action in ("delete", "remove"):
                cmd = f"iptables -D INPUT -p {proto} --dport {port} -j ACCEPT 2>/dev/null; iptables -D INPUT -p {proto} --dport {port} -j DROP 2>/dev/null"
            else:
                target_act = "ACCEPT" if action == "allow" else "DROP"
                cmd = f"iptables -A INPUT -p {proto} --dport {port} -j {target_act}"
            res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
            return res.returncode == 0

    return False


def prompt_ask(prompt_text: str, default: str = "") -> str:
    """Safely prompt text input with fallback."""
    if HAS_RICH:
        return Prompt.ask(prompt_text, default=default)
    else:
        def_str = f" [{default}]" if default else ""
        res = input(f"{prompt_text}{def_str}: ").strip()
        return res if res else default


def prompt_confirm(prompt_text: str, default: bool = True) -> bool:
    """Safely prompt boolean confirmation with fallback."""
    if HAS_RICH:
        return Confirm.ask(prompt_text, default=default)
    else:
        def_str = "[Y/n]" if default else "[y/N]"
        res = input(f"{prompt_text} {def_str}: ").strip().lower()
        if not res:
            return default
        return res in ("y", "yes")


def interactive_firewall_setup():
    """Launches an interactive setup wizard with numbered dropdown menus and rule naming."""
    if HAS_RICH and console:
        console.print(Panel("[bold green]Hopit Interactive Firewall Setup Wizard[/bold green]\nConfigure ports, protocols, rule names, and adapters with dropdown menus.", border_style="cyan"))
    else:
        print("\n--- Hopit Interactive Firewall Setup Wizard ---")

    action = select_menu("Choose Action", ["allow", "block", "delete", "status"], default_index=0)
    if action == "status":
        show_firewall_status_table()
        return

    port = prompt_ask("Enter port number or range (e.g. 80, 443, 8080)")
    if not port:
        print("Port number is required.")
        return
        
    proto = select_menu("Select Protocol", ["tcp", "udp", "both"], default_index=0)
    
    ifaces = get_network_interfaces()
    iface = select_menu("Select Adapter / Interface", ["all"] + ifaces, default_index=0)
    
    rule_name = ""
    if action in ("allow", "block"):
        rule_name = prompt_ask("Enter custom Rule Name (optional, e.g. Web-Server)", default="")
    
    name_desc = f" (Name: '{rule_name}')" if rule_name else ""
    print(f"\nRule Summary: {action.upper()} port {port} ({proto.upper()}) on adapter '{iface}'{name_desc}")
    
    if prompt_confirm("Apply this firewall rule now?", default=True):
        if proto == "both":
            r1 = run_firewall_rule(action, port, "tcp", iface, rule_name)
            r2 = run_firewall_rule(action, port, "udp", iface, rule_name)
            success = r1 and r2
        else:
            success = run_firewall_rule(action, port, proto, iface, rule_name)
            
        if success:
            print(f"✓ Firewall rule '{action}' executed successfully!")
            show_firewall_status_table()
        else:
            print("✗ Failed to execute firewall rule. Ensure root/sudo privileges.")


def handle_firewall_cli(args: list[str]):
    """Main CLI handler for firewall single-line commands and subcommands."""
    if not args or args[0].lower() in ("interactive", "config", "wizard"):
        interactive_firewall_setup()
        return

    sub = args[0].lower()
    
    if sub.startswith("st"):  # status, st, stat
        show_firewall_status_table()
        return

    if sub in ("allow", "block", "deny", "delete", "remove"):
        if len(args) < 2:
            interactive_firewall_setup()
            return
            
        port = args[1]
        proto = args[2].lower() if len(args) > 2 and args[2].lower() in ("tcp", "udp", "both") else "tcp"
        iface = args[3] if len(args) > 3 else "all"
        rule_name = args[4] if len(args) > 4 else ""
        
        act_name = "delete" if sub in ("delete", "remove") else ("allow" if sub == "allow" else "block")
        
        if proto == "both":
            r1 = run_firewall_rule(act_name, port, "tcp", iface, rule_name)
            r2 = run_firewall_rule(act_name, port, "udp", iface, rule_name)
            success = r1 and r2
        else:
            success = run_firewall_rule(act_name, port, proto, iface, rule_name)
            
        if success:
            print(f"✓ Successfully executed {act_name.upper()} rule for port {port} ({proto.upper()})!")
            show_firewall_status_table()
        else:
            print("✗ Failed to execute firewall rule. Ensure root/sudo privileges.")
        return

    print(f"Unknown firewall action: {sub}. Usage: firewall [status|allow|block|delete|interactive]")


def main():
    handle_firewall_cli(sys.argv[1:])

if __name__ == "__main__":
    main()
