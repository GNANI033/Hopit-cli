# Hopit-CLI: Command Inventory & Phase-by-Phase Roadmap

This document records the complete status of commands in **Hopit-CLI**. It provides an inventory of all implemented universal self-explanatory English commands, multi-backend auto-detecting security/storage utilities, cross-platform translated commands, interactive setup wizards, and detailed context-sensitive help.

---

## 1. Executive Summary & Inventory Status

| Status | Count | Description |
| :--- | :---: | :--- |
| **Universal English Commands** | **75+ Core Commands** | Self-explanatory English command interfaces (`status`, `start`, `stop`, `restart`, `logs`, `live`, `enable`, `disable`, `firewall`, `disk`, `archive`, `download`, `search`, `killport`, `user`, `group`, `permission`, `sysinfo`, `processes`, `sqlite`, `containers`, `netconfig`, etc.). |
| **Interactive Setup Wizards** | **Firewall & Network Wizards** | Interactive setup wizards (`firewall`, `firewall config`, `netconfig`) prompt for actions, ports, protocols, and network interfaces step-by-step. |
| **Rich Status Reporting** | **Formatted Tables** | `firewall status`, `processes`, `connections`, `mac`, and `resources` parse system and network state into rich colorized tables and dashboards. |
| **Multi-Backend Auto-Detection** | **Zero Hardcoded Binaries** | `firewall` automatically detects `firewall-cmd` (Fedora/RHEL/CentOS), `ufw` (Ubuntu/Debian), `nft` (nftables), `iptables`, `pfctl` (macOS), `Get-NetFirewallRule` (Windows). |

---

## 2. Implemented Universal Commands (`hopit/commands.py`)

### A. Firewall Commands & Interactive Wizard (`hopit/firewall.py`)

| Syntax | Action / Mode | Description |
| :--- | :--- | :--- |
| `firewall` / `firewall interactive` | Interactive Wizard | Prompt-driven setup asking for Action (Allow/Block), Port number/range, Protocol (TCP/UDP/Both), and Adapter/Interface. |
| `firewall status` | Rich Table Output | Displays a structured table of all active rules, allowed services, open ports, rich drop rules, interfaces, and backends. |
| `firewall allow <port> [proto] [iface]` | Single-line Command | Allows inbound traffic on specified port, protocol (TCP/UDP/Both), and network interface. |
| `firewall block <port> [proto] [iface]` | Single-line Command | Blocks inbound traffic on specified port, protocol (TCP/UDP/Both), and network interface. |

### B. Network Utilities

| Syntax | Description | Mode |
| :--- | :--- | :--- |
| `ping <host>` | Ping a remote host to check network connectivity. | Stream |
| `traceroute <host>` | Trace the route packets take to reach a host (tracert on Windows). | Stream |
| `dns <host>` | Perform detailed DNS resolution lookup showing IPv4, IPv6, MX, and TXT records. | Capture |
| `nslookup <host>` | Query Internet name servers interactively or perform standard lookups. | Stream |
| `route [args]` | View or configure the system network routing table cross-platform. | Capture |
| `arp [args]` | View and manage the system Address Resolution Protocol (ARP) table. | Capture |
| `netstat [args]` | Display network connections and protocol statistics. | Stream |
| `connections` | Display active network connections with process PID/name in a styled table. | Capture |
| `hostname [new_name]` | View or change the system's host name. | Capture |
| `gateway` | Query and display system default network gateway IP address. | Capture |
| `mac` | Query and display hardware MAC addresses of active network interfaces. | Capture |
| `curl <url> [args]` | Transfer data from or to a server using curl. | Stream |
| `wget <url> [args]` | Non-interactive network downloader (uses curl wrapper on Windows). | Stream |
| `ssh <user@host>` | OpenSSH SSH client (remote login). | Stream |
| `scp <src> <dest>` | Secure copy files. | Stream |
| `sftp <user@host>` | Secure file transfer program. | Stream |

### C. Process Management Utilities

| Syntax | Description | Mode |
| :--- | :--- | :--- |
| `processes [sort_by]` | List running processes in a clean, sorted table (cpu/mem/name/pid). | Capture |
| `ps [sort_by]` | Alias of `processes` to list running processes. | Capture |
| `process <pid_or_name>` | Inspect a specific process with comprehensive metrics, parent/child info, and active connections. | Capture |
| `kill <pid_or_name>` | Terminate a process by PID or image name. | Stream |
| `pkill <name>` | Terminate processes by name pattern matching. | Stream |
| `top` | Live-updating top-20 CPU consuming processes (Ctrl-C to exit). | Stream |
| `resources` | Live-updating system resource dashboard (CPU, RAM, Disks, Network). | Capture |
