# Hopit-CLI: Command Inventory & Phase-by-Phase Roadmap

This document records the complete status of commands in **Hopit-CLI**. It provides an inventory of all implemented universal self-explanatory English commands, multi-backend auto-detecting security/storage utilities, cross-platform translated commands, and detailed context-sensitive help.

---

## 1. Executive Summary & Inventory Status

| Status | Count | Description |
| :--- | :---: | :--- |
| **Universal English Commands** | **55+ Core Commands** | Self-explanatory English command interfaces (`status`, `start`, `stop`, `restart`, `logs`, `live`, `enable`, `disable`, `firewall`, `disk`, `archive`, `download`, `search`, `killport`, `user`, `group`, `permission`, `sysinfo`, `processes`, `sqlite`, `containers`, `netconfig`, etc.). |
| **Multi-Backend Auto-Detection** | **Zero Hardcoded Binaries** | `firewall` automatically detects `firewall-cmd` (Fedora/RHEL/CentOS), `ufw` (Ubuntu/Debian), `nft` (nftables), `iptables`, `pfctl` (macOS), `Get-NetFirewallRule` (Windows). `disk` auto-detects `lsblk`/`df`/`diskutil`/`mountvol`. `killport` auto-detects `lsof`/`fuser`/`ss`/`Get-NetTCPConnection`. |
| **Cross-Platform Translated** | **70+ Aliases/Translations** | Native Linux, macOS, and Windows shell commands translated on the fly (`ls`, `dir`, `cat`, `type`, `ps`, `tasklist`, `kill`, `taskkill`, `chmod`, `icacls`, `grep`, `findstr`, etc.). |

---

## 2. Implemented Universal Commands (`hopit/commands.py`)

### A. Direct Service Control Commands

| Command | Usage | Description | OS Support |
| :--- | :--- | :--- | :--- |
| `status` | `status <service>` | Check if a service is active/running | Linux, macOS, Windows |
| `start` | `start <service>` | Start a stopped service | Linux, macOS, Windows |
| `stop` | `stop <service>` | Stop a running service | Linux, macOS, Windows |
| `restart` | `restart <service>` | Restart a service | Linux, macOS, Windows |
| `logs` | `logs <service>` | View recent service log output | Linux, macOS, Windows |
| `live` | `live <service>` | Stream service log output in real-time | Linux, macOS, Windows |
| `enable` | `enable <service>` | Enable a service to start automatically on system boot | Linux, macOS, Windows |
| `disable` | `disable <service>` | Disable a service from starting on boot | Linux, macOS, Windows |

### B. Core Administrative & Utility Commands

| Command | Subcommands / Syntax | Auto-Detected Backends / Description | OS Support |
| :--- | :--- | :--- | :--- |
| `firewall` | `status`, `allow <port>`, `block <port>` | `firewall-cmd`, `ufw`, `nftables`, `iptables`, `pfctl`, `netsh` | Linux, macOS, Windows |
| `disk` / `drive` | `list`, `usage`, `mount`, `unmount`, `check` | `lsblk`, `df`, `diskutil`, `fsck`, `chkdsk`, `mountvol` | Linux, macOS, Windows |
| `archive` / `compress` | `create <out.zip> <path>`, `extract <arch> [dest]` | Pure Python `zipfile` & `tarfile` (zero dependencies) | Linux, macOS, Windows |
| `download` | `download <url> [destination]` | Pure Python `urllib` with live progress & speed meter | Linux, macOS, Windows |
| `search` | `search <pattern> [path]` | Pure Python regex file & filename search engine | Linux, macOS, Windows |
| `killport` | `killport <port>` | `ss`, `lsof`, `fuser`, `Get-NetTCPConnection` | Linux, macOS, Windows |
| `user` | `add`, `remove`, `passwd`, `join`, `list` | User account management | Linux, macOS, Windows |
| `group` | `add`, `remove`, `list` | Group account management | Linux, macOS, Windows |
| `permission` | `set`, `owner`, `group` | File/folder permission & ownership | Linux, macOS, Windows |
| `copy` / `move` / `remove` / `mkdir` | `<src> <dest>` / `<path>` | Universal file system operations | Linux, macOS, Windows |
| `ip` / `netconfig` / `port` | `<adapter>` / `<port>` | Network interface & port lookup | Linux, macOS, Windows |
| `containers` | `containers` | Auto-detect Docker, Proxmox LXC/VM, ESXi, WSL | Linux, macOS, Windows |
| `sysinfo` / `processes` / `sqlite` | `sysinfo` / `processes` / `sqlite <db>` | System diagnostics & DB inspection | Linux, macOS, Windows |
| `install` / `uninstall` / `update` | `<package>` | Package management (`dnf`, `apt`, `brew`, `winget`, etc.) | Linux, macOS, Windows |

---

## 3. Context-Sensitive Help System (`?` & `help <cmd>`)

All commands render rich parameter panels instead of generic placeholders:
- `firewall ?` → renders a structured subcommand table listing `status`, `allow <port>`, `block <port>`, `deny <port>`.
- `firewall block ?` → renders `<port> Specify the port number to allow/block`.
- `disk ?` → renders a table listing `list`, `usage`, `mount`, `unmount`, `check`.
- `archive ?` → renders `create <out.zip> <path>` and `extract <archive> [dest]`.
