# Hopit-CLI: Command Inventory & Phase-by-Phase Roadmap

This document records the complete status of commands in **Hopit-CLI**. It provides an inventory of all implemented universal self-explanatory English commands, multi-backend auto-detecting security/storage utilities, cross-platform translated commands, interactive setup wizards, and detailed context-sensitive help.

---

## 1. Executive Summary & Inventory Status

| Status | Count | Description |
| :--- | :---: | :--- |
| **Universal English Commands** | **55+ Core Commands** | Self-explanatory English command interfaces (`status`, `start`, `stop`, `restart`, `logs`, `live`, `enable`, `disable`, `firewall`, `disk`, `archive`, `download`, `search`, `killport`, `user`, `group`, `permission`, `sysinfo`, `processes`, `sqlite`, `containers`, `netconfig`, etc.). |
| **Interactive Setup Wizards** | **Firewall & Network Wizards** | Interactive setup wizards (`firewall`, `firewall config`, `netconfig`) prompt for actions, ports, protocols, and network interfaces step-by-step. |
| **Rich Status Reporting** | **Formatted Tables** | `firewall status` parses firewall state into rich colorized tables with columns for Rule/Service, Port, Protocol, Action, Interface/Scope, and Backend/Details. |
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
