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
    from hopit.config import console
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    HAS_RICH = True
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


def select_dropdown(title: str, choices: list[str], default_idx: int = 0) -> str:
    """Interactive Arrow-Key Dropdown Menu (Use Up/Down arrows + Enter)."""
    if not choices:
        return ""
    if not sys.stdin.isatty():
        return choices[default_idx]

    current_idx = default_idx

    if IS_WINDOWS:
        import msvcrt
        def getch():
            ch = msvcrt.getch()
            if ch in (b'\x00', b'\xe0'):
                ch = msvcrt.getch()
                if ch == b'H': return 'UP'
                if ch == b'P': return 'DOWN'
            if ch in (b'\r', b'\n'): return 'ENTER'
            return None
    else:
        import termios, tty
        def getch():
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = sys.stdin.read(1)
                if ch == '\x1b':
                    ch2 = sys.stdin.read(1)
                    ch3 = sys.stdin.read(1)
                    if ch3 == 'A': return 'UP'
                    if ch3 == 'B': return 'DOWN'
                if ch in ('\r', '\n'): return 'ENTER'
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            return None

    if HAS_RICH and console:
        console.print(f"\n[bold cyan]{title}[/bold cyan] [dim](Use ↑/↓ arrows and press ENTER)[/dim]:")
    else:
        print(f"\n{title} (Use UP/DOWN arrows and press ENTER):")

    lines_to_clear = len(choices)
    first_render = True
    
    while True:
        if not first_render:
            sys.stdout.write(f"\033[{lines_to_clear}A")
        first_render = False

        for idx, choice in enumerate(choices):
            if idx == current_idx:
                sys.stdout.write(f"\033[K  \033[1;36m❯ 🔘 {choice}\033[0m\n")
            else:
                sys.stdout.write(f"\033[K    ⚪ {choice}\n")
        sys.stdout.flush()

        key = getch()
        if key == 'UP':
            current_idx = (current_idx - 1) % len(choices)
        elif key == 'DOWN':
            current_idx = (current_idx + 1) % len(choices)
        elif key == 'ENTER':
            break

    return choices[current_idx]


def parse_firewall_rules() -> list[dict]:
    """Parses system firewall state into structured numbered rule objects."""
    rules = []
    rule_id = 1

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
                rules.append({
                    "id": rule_id,
                    "type": "win",
                    "name": name,
                    "port": port,
                    "proto": "TCP/UDP",
                    "action": action,
                    "iface": "Inbound",
                    "details": "Windows Firewall",
                    "raw": name
                })
                rule_id += 1
        except Exception:
            pass
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
                rules.append({
                    "id": rule_id,
                    "type": "pf",
                    "name": "PF Rule",
                    "port": port,
                    "proto": "TCP",
                    "action": action,
                    "iface": "All",
                    "details": "macOS pfctl",
                    "raw": line
                })
                rule_id += 1
        except Exception:
            pass
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
                rules.append({
                    "id": rule_id,
                    "type": "firewalld_service",
                    "name": f"Service: {s}",
                    "port": "Default",
                    "proto": "TCP/UDP",
                    "action": "ALLOW",
                    "iface": ifaces,
                    "details": f"firewalld ({active_zone})",
                    "raw": s
                })
                rule_id += 1
            
            for p in ports:
                parts = p.split("/")
                p_num = parts[0]
                p_proto = parts[1].upper() if len(parts) > 1 else "TCP"
                rules.append({
                    "id": rule_id,
                    "type": "firewalld_port",
                    "name": "Allowed Port",
                    "port": p_num,
                    "proto": p_proto,
                    "action": "ALLOW",
                    "iface": ifaces,
                    "details": f"firewalld ({active_zone})",
                    "raw": p
                })
                rule_id += 1
            
            for r in rich_rules:
                comment_match = re.search(r'comment="([^"]+)"', r)
                p_match = re.search(r'port="(\d+)"', r)
                proto_match = re.search(r'protocol="(\w+)"', r)
                
                p_val = p_match.group(1) if p_match else "Any"
                p_proto = proto_match.group(1).upper() if proto_match else "TCP"
                rule_label = comment_match.group(1) if comment_match else f"Rule (Port {p_val})"
                act = "BLOCK" if "drop" in r or "reject" in r else "ALLOW"
                rules.append({
                    "id": rule_id,
                    "type": "firewalld_rich",
                    "name": rule_label,
                    "port": p_val,
                    "proto": p_proto,
                    "action": act,
                    "iface": ifaces,
                    "details": f"firewalld ({active_zone})",
                    "raw": r
                })
                rule_id += 1

        elif shutil.which("ufw"):
            res = subprocess.run(["ufw", "status", "numbered"], capture_output=True, text=True)
            for line in res.stdout.splitlines():
                match = re.search(r"\[\s*(\d+)\]\s+(.*)", line)
                if match:
                    num = match.group(1)
                    rest = match.group(2)
                    parts = rest.split()
                    port = parts[0] if parts else "Any"
                    act = "ALLOW" if len(parts) > 1 and "ALLOW" in parts[1] else "BLOCK"
                    rules.append({
                        "id": int(num),
                        "type": "ufw",
                        "name": f"UFW Rule #{num}",
                        "port": port,
                        "proto": "TCP/UDP",
                        "action": act,
                        "iface": "Anywhere",
                        "details": "UFW",
                        "raw": num
                    })
                    rule_id += 1

        elif shutil.which("nft"):
            res = subprocess.run(["nft", "list", "ruleset"], capture_output=True, text=True)
            for line in res.stdout.splitlines():
                line = line.strip()
                if "dport" in line:
                    act = "ALLOW" if "accept" in line else "BLOCK"
                    port_match = re.search(r"dport\s+(\d+)", line)
                    port = port_match.group(1) if port_match else "Any"
                    rules.append({
                        "id": rule_id,
                        "type": "nft",
                        "name": f"NFT Rule #{rule_id}",
                        "port": port,
                        "proto": "TCP",
                        "action": act,
                        "iface": "All",
                        "details": "nftables",
                        "raw": line
                    })
                    rule_id += 1

        elif shutil.which("iptables"):
            res = subprocess.run(["iptables", "-L", "INPUT", "-n", "-v", "--line-numbers"], capture_output=True, text=True)
            for line in res.stdout.splitlines():
                if "dpt:" in line:
                    parts = line.split()
                    num = parts[0] if parts and parts[0].isdigit() else str(rule_id)
                    act = "ALLOW" if "ACCEPT" in line else "BLOCK"
                    port_match = re.search(r"dpt:(\d+)", line)
                    port = port_match.group(1) if port_match else "Any"
                    rules.append({
                        "id": int(num),
                        "type": "iptables",
                        "name": f"IPTables Rule #{num}",
                        "port": port,
                        "proto": "TCP",
                        "action": act,
                        "iface": "All",
                        "details": "iptables",
                        "raw": num
                    })
                    rule_id += 1

    return rules


def show_firewall_status_table():
    """Parses system firewall status and displays a numbered Table."""
    rules = parse_firewall_rules()

    if HAS_RICH and console:
        table = Table(title="🛡️ Active Firewall Configuration & Rules", border_style="cyan", header_style="bold green")
        table.add_column("ID", style="bold yellow", justify="center")
        table.add_column("Rule / Service", style="bold white")
        table.add_column("Port", style="bright_yellow")
        table.add_column("Protocol", style="bright_cyan")
        table.add_column("Action", style="bold")
        table.add_column("Interface / Scope", style="magenta")
        table.add_column("Backend / Details", style="dim white")

        for r in rules:
            act = r["action"]
            act_styled = f"[bold green]{act}[/bold green]" if act == "ALLOW" else (f"[bold red]{act}[/bold red]" if act in ("BLOCK", "DENY") else f"[yellow]{act}[/yellow]")
            table.add_row(str(r["id"]), r["name"], r["port"], r["proto"], act_styled, r["iface"], r["details"])
        
        if not rules:
            table.add_row("-", "Default Profile", "All Ports", "ANY", "[bold green]ACTIVE[/bold green]", "All", "Active Daemon")
            
        console.print(table)
    else:
        print("\n--- 🛡️ Active Firewall Configuration & Rules ---")
        fmt = "{:<4} {:<24} {:<12} {:<10} {:<10} {:<18} {:<20}"
        print(fmt.format("ID", "Rule / Service", "Port", "Protocol", "Action", "Interface / Scope", "Backend / Details"))
        print("-" * 102)
        for r in rules:
            print(fmt.format(str(r["id"]), r["name"], r["port"], r["proto"], r["action"], r["iface"], r["details"]))
        print("-" * 102 + "\n")


import getpass


def ensure_sudo_privileges() -> bool:
    """Ensures host process has root/sudo rights; prompts user for sudo password if unauthenticated."""
    if IS_WINDOWS or (hasattr(os, "geteuid") and os.geteuid() == 0):
        return True

    try:
        res = subprocess.run(["sudo", "-n", "true"], capture_output=True)
        if res.returncode == 0:
            return True
    except Exception:
        pass

    try:
        res = subprocess.run(["sudo", "-v"], stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
        if res.returncode == 0:
            return True
    except Exception:
        pass

    try:
        pw = getpass.getpass("🔐 Enter root/sudo password: ")
        res = subprocess.run(["sudo", "-S", "-v"], input=f"{pw}\n", text=True, capture_output=True)
        if res.returncode == 0:
            return True
        else:
            print("❌ Sudo authentication failed. Incorrect password.")
            return False
    except Exception:
        return False


def sanitize_rule_name(name: str) -> str:
    """Removes any characters that are not alphanumeric, spaces, hyphens, underscores, or dots."""
    return re.sub(r'[^a-zA-Z0-9_\-\. ]', '', name)


def sanitize_port(port: str) -> str:
    """Removes any characters that are not digits, colons, hyphens, or commas."""
    return re.sub(r'[^0-9:\-,]', '', port)


def sanitize_proto(proto: str) -> str:
    """Restricts protocol to tcp, udp, or both."""
    proto_lower = proto.lower()
    if proto_lower in ("tcp", "udp", "both"):
        return proto_lower
    return "tcp"


def delete_rule_by_id(target_id: int) -> bool:
    """Deletes EXACTLY a single firewall rule identified by its table ID number."""
    if not ensure_sudo_privileges():
        return False

    rules = parse_firewall_rules()
    target_rule = None
    for r in rules:
        if r["id"] == target_id:
            target_rule = r
            break
            
    if not target_rule:
        print(f"Rule ID #{target_id} not found in active rules list.")
        return False

    rule_type = target_rule["type"]
    raw = target_rule["raw"]
    pfx_list = ["sudo"] if (not IS_WINDOWS and hasattr(os, "geteuid") and os.geteuid() != 0) else []

    if rule_type == "firewalld_rich":
        cmd1 = pfx_list + ["firewall-cmd", "--remove-rich-rule", raw, "--permanent"]
        cmd2 = pfx_list + ["firewall-cmd", "--reload"]
        res1 = subprocess.run(cmd1, capture_output=True, text=True)
        res2 = subprocess.run(cmd2, capture_output=True, text=True)
        return res1.returncode == 0 and res2.returncode == 0
    elif rule_type == "firewalld_port":
        cmd1 = pfx_list + ["firewall-cmd", "--remove-port", raw, "--permanent"]
        cmd2 = pfx_list + ["firewall-cmd", "--reload"]
        res1 = subprocess.run(cmd1, capture_output=True, text=True)
        res2 = subprocess.run(cmd2, capture_output=True, text=True)
        return res1.returncode == 0 and res2.returncode == 0
    elif rule_type == "firewalld_service":
        cmd1 = pfx_list + ["firewall-cmd", "--remove-service", raw, "--permanent"]
        cmd2 = pfx_list + ["firewall-cmd", "--reload"]
        res1 = subprocess.run(cmd1, capture_output=True, text=True)
        res2 = subprocess.run(cmd2, capture_output=True, text=True)
        return res1.returncode == 0 and res2.returncode == 0
    elif rule_type == "ufw":
        cmd = pfx_list + ["ufw", "delete"] + shlex.split(raw)
        res = subprocess.run(cmd, input="y\n", capture_output=True, text=True)
        return res.returncode == 0
    elif rule_type == "iptables":
        cmd = pfx_list + ["iptables", "-D", "INPUT"] + shlex.split(raw)
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode == 0
    elif rule_type == "win":
        cmd = ["powershell", "-NoProfile", "-Command", "Remove-NetFirewallRule -DisplayName $args[0]"]
        res = subprocess.run(cmd + [raw], capture_output=True, text=True)
        return res.returncode == 0

    return False


def run_firewall_rule(action: str, port: str, proto: str = "tcp", iface: str = "all", rule_name: str = "") -> bool:
    """Executes a firewall rule with action, port, protocol, interface, and rule name."""
    action = action.lower()
    proto = sanitize_proto(proto)
    port = sanitize_port(port)
    rule_name = sanitize_rule_name(rule_name)
    iface = sanitize_rule_name(iface)
    
    disp_name = rule_name if rule_name else f"Hopit-{action.capitalize()}-{port}"

    if not ensure_sudo_privileges():
        return False

    pfx_list = ["sudo"] if (not IS_WINDOWS and hasattr(os, "geteuid") and os.geteuid() != 0) else []

    if IS_WINDOWS:
        if action in ("delete", "remove"):
            cmd1 = ["powershell", "-NoProfile", "-Command", "Remove-NetFirewallRule -DisplayName $args[0] -ErrorAction SilentlyContinue"]
            cmd2 = ["powershell", "-NoProfile", "-Command", "Remove-NetFirewallRule -LocalPort $args[0] -ErrorAction SilentlyContinue"]
            res1 = subprocess.run(cmd1 + [disp_name], capture_output=True, text=True)
            res2 = subprocess.run(cmd2 + [port], capture_output=True, text=True)
            return res1.returncode == 0 and res2.returncode == 0
        else:
            act = "Allow" if action == "allow" else "Block"
            cmd = ["powershell", "-NoProfile", "-Command", 
                   "New-NetFirewallRule -DisplayName $args[0] -Direction Inbound -LocalPort $args[1] -Protocol $args[2] -Action $args[3]"]
            res = subprocess.run(cmd + [disp_name, port, proto.upper(), act], capture_output=True, text=True)
            return res.returncode == 0
    elif IS_MACOS:
        if action in ("delete", "remove"):
            return True
        pf_act = "pass" if action == "allow" else "block"
        rule = f"{pf_act} in proto {proto} from any to any port {port}\n"
        cmd = ["sudo", "pfctl", "-f", "-"]
        res = subprocess.run(cmd, input=rule, capture_output=True, text=True)
        return res.returncode == 0
    else:
        # Linux Auto-Detection
        if shutil.which("firewall-cmd"):
            if action in ("delete", "remove"):
                if port.isdigit():
                    return delete_rule_by_id(int(port))
                subprocess.run(pfx_list + ["firewall-cmd", "--remove-port=" + port + "/tcp", "--permanent"], capture_output=True)
                subprocess.run(pfx_list + ["firewall-cmd", "--remove-port=" + port + "/udp", "--permanent"], capture_output=True)
                subprocess.run(pfx_list + ["firewall-cmd", "--remove-rich-rule=rule family=\"ipv4\" port port=\"" + port + "\" protocol=\"tcp\" accept", "--permanent"], capture_output=True)
                subprocess.run(pfx_list + ["firewall-cmd", "--remove-rich-rule=rule family=\"ipv4\" port port=\"" + port + "\" protocol=\"tcp\" drop", "--permanent"], capture_output=True)
                res = subprocess.run(pfx_list + ["firewall-cmd", "--reload"], capture_output=True, text=True)
                return res.returncode == 0
            elif action == "allow":
                if rule_name:
                    rich_rule = f"rule family=\"ipv4\" port port=\"{port}\" protocol=\"{proto}\" comment=\"{rule_name}\" accept"
                    cmd1 = pfx_list + ["firewall-cmd", "--add-rich-rule=" + rich_rule, "--permanent"]
                else:
                    cmd1 = pfx_list + ["firewall-cmd", "--add-port=" + port + "/" + proto, "--permanent"]
                res1 = subprocess.run(cmd1, capture_output=True, text=True)
                res2 = subprocess.run(pfx_list + ["firewall-cmd", "--reload"], capture_output=True, text=True)
                return res1.returncode == 0 and res2.returncode == 0
            else: # block / deny
                if rule_name:
                    subprocess.run(pfx_list + ["firewall-cmd", "--remove-port=" + port + "/" + proto, "--permanent"], capture_output=True)
                    rich_rule = f"rule family=\"ipv4\" port port=\"{port}\" protocol=\"{proto}\" comment=\"{rule_name}\" drop"
                    cmd1 = pfx_list + ["firewall-cmd", "--add-rich-rule=" + rich_rule, "--permanent"]
                else:
                    subprocess.run(pfx_list + ["firewall-cmd", "--remove-port=" + port + "/" + proto, "--permanent"], capture_output=True)
                    rich_rule = f"rule family=\"ipv4\" port port=\"{port}\" protocol=\"{proto}\" comment=\"Block-{port}\" drop"
                    cmd1 = pfx_list + ["firewall-cmd", "--add-rich-rule=" + rich_rule, "--permanent"]
                res1 = subprocess.run(cmd1, capture_output=True, text=True)
                res2 = subprocess.run(pfx_list + ["firewall-cmd", "--reload"], capture_output=True, text=True)
                return res1.returncode == 0 and res2.returncode == 0

        elif shutil.which("ufw"):
            if action in ("delete", "remove"):
                if port.isdigit():
                    return delete_rule_by_id(int(port))
                subprocess.run(pfx_list + ["ufw", "delete", "allow", f"{port}/{proto}"], capture_output=True)
                res = subprocess.run(pfx_list + ["ufw", "delete", "deny", f"{port}/{proto}"], capture_output=True, text=True)
                return res.returncode == 0
            elif action == "allow":
                cmd = pfx_list + ["ufw", "allow", f"{port}/{proto}"]
                if rule_name:
                    cmd += ["comment", rule_name]
                res = subprocess.run(cmd, capture_output=True, text=True)
                return res.returncode == 0
            else:
                cmd = pfx_list + ["ufw", "deny", f"{port}/{proto}"]
                if rule_name:
                    cmd += ["comment", rule_name]
                res = subprocess.run(cmd, capture_output=True, text=True)
                return res.returncode == 0

        elif shutil.which("iptables"):
            if action in ("delete", "remove"):
                if port.isdigit():
                    return delete_rule_by_id(int(port))
                subprocess.run(pfx_list + ["iptables", "-D", "INPUT", "-p", proto, "--dport", port, "-j", "ACCEPT"], capture_output=True)
                res = subprocess.run(pfx_list + ["iptables", "-D", "INPUT", "-p", proto, "--dport", port, "-j", "DROP"], capture_output=True, text=True)
                return res.returncode == 0
            else:
                target_act = "ACCEPT" if action == "allow" else "DROP"
                cmd = pfx_list + ["iptables", "-A", "INPUT", "-p", proto, "--dport", port, "-j", target_act]
                res = subprocess.run(cmd, capture_output=True, text=True)
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
    """Launches interactive firewall wizard with arrow-key dropdowns and single-rule deletion by ID."""
    if HAS_RICH and console:
        console.print(Panel("[bold green]Hopit Interactive Firewall Wizard[/bold green]\nNavigate options using ↑/↓ arrow keys and press ENTER.", border_style="cyan"))
    else:
        print("\n--- Hopit Interactive Firewall Wizard ---")

    action = select_dropdown("Choose Action", ["allow", "block", "delete single rule", "status"], default_idx=0)
    
    if action == "status":
        show_firewall_status_table()
        return

    if action == "delete single rule":
        rules = parse_firewall_rules()
        if not rules:
            print("No active firewall rules to delete.")
            return
        
        choices = [f"Rule #{r['id']}: {r['name']} (Port {r['port']}/{r['proto']})" for r in rules]
        selected_choice = select_dropdown("Select Specific Rule to Delete", choices, default_idx=0)
        
        # Extract rule ID
        match = re.search(r"Rule #(\d+)", selected_choice)
        if match:
            target_id = int(match.group(1))
            if prompt_confirm(f"Are you sure you want to delete Rule #{target_id}?", default=True):
                success = delete_rule_by_id(target_id)
                if success:
                    print(f"✓ Successfully deleted Rule #{target_id}!")
                    show_firewall_status_table()
                else:
                    print(f"✗ Failed to delete Rule #{target_id}. Ensure root/sudo privileges.")
        return

    proto = select_dropdown("Select Protocol", ["tcp", "udp", "both"], default_idx=0)
    
    ifaces = get_network_interfaces()
    iface = select_dropdown("Select Adapter / Interface", ["all"] + ifaces, default_idx=0)
    
    port_choices = [
        "Custom (Or type any port number / range of your choosing)",
        "80 (HTTP Web Server)",
        "443 (HTTPS Secure Web)",
        "22 (SSH Remote Access)",
        "8080 (Web Alt / App Server)",
        "3306 (MySQL Database)",
        "5432 (PostgreSQL Database)",
    ]
    selected_port = select_dropdown("Select Target Port", port_choices, default_idx=0)
    
    if selected_port.startswith("Custom"):
        port = prompt_ask("Enter custom port number or range (e.g. 80, 443, 8080)")
    else:
        port = selected_port.split()[0]
        
    if not port:
        print("Port number is required.")
        return

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
            
        target = args[1]
        
        if sub in ("delete", "remove"):
            if target.isdigit():
                target_num = int(target)
                success = delete_rule_by_id(target_num)
                if not success:
                    success = run_firewall_rule("delete", target, "tcp", "all", "")
            else:
                success = run_firewall_rule("delete", target, "tcp", "all", "")
                
            if success:
                print(f"✓ Successfully deleted rule target '{target}'!")
                show_firewall_status_table()
            else:
                print(f"✗ Failed to delete rule target '{target}'. Ensure root/sudo privileges.")
            return

        port = target
        
        # If protocol was passed in CLI (e.g. firewall allow 22 udp), use it;
        # otherwise prompt with interactive Protocol Dropdown!
        if len(args) > 2 and args[2].lower() in ("tcp", "udp", "both"):
            proto = args[2].lower()
        else:
            proto = select_dropdown(f"Select Protocol for Port {port}", ["tcp", "udp", "both"], default_idx=0)

        iface = args[3] if len(args) > 3 else "all"
        rule_name = args[4] if len(args) > 4 else ""
        
        act_name = "allow" if sub == "allow" else "block"
        
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
