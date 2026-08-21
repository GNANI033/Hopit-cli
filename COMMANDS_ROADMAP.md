# Hopit-CLI: Command Inventory & Phase-by-Phase Roadmap

This document records the complete status of commands in **Hopit-CLI**. It provides an inventory of all implemented universal self-explanatory English commands, cross-platform translated commands, and future platform extensions.

---

## 1. Executive Summary & Inventory Status

| Status | Count | Description |
| :--- | :---: | :--- |
| **Universal English Commands** | **55+ Core Commands** | Self-explanatory English command interfaces (`status`, `start`, `stop`, `restart`, `logs`, `live`, `enable`, `disable`, `firewall`, `disk`, `archive`, `download`, `search`, `killport`, `user`, `group`, `permission`, `sysinfo`, `processes`, `sqlite`, `containers`, `netconfig`, etc.). |
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

| Command | Subcommands / Syntax | Job / Description | OS Support |
| :--- | :--- | :--- | :--- |
| `firewall` | `status`, `allow <port>`, `block <port>` | Manage network firewall rules | Linux, macOS, Windows |
| `disk` / `drive` | `list`, `usage`, `mount`, `unmount`, `check` | Physical disk, volume & storage management | Linux, macOS, Windows |
| `archive` / `compress` | `create <out.zip> <path>`, `extract <arch> [dest]` | Universal file compression & extraction | Linux, macOS, Windows |
| `download` | `download <url> [destination]` | Download files with progress bar | Linux, macOS, Windows |
| `search` | `search <pattern> [path]` | Search text inside files & search filenames | Linux, macOS, Windows |
| `killport` | `killport <port>` | Terminate process listening on port | Linux, macOS, Windows |
| `user` | `add`, `remove`, `passwd`, `join`, `list` | User account management | Linux, macOS, Windows |
| `group` | `add`, `remove`, `list` | Group account management | Linux, macOS, Windows |
| `permission` | `set`, `owner`, `group` | File/folder permission & ownership | Linux, macOS, Windows |
| `copy` / `move` / `remove` / `mkdir` | `<src> <dest>` / `<path>` | Universal file system operations | Linux, macOS, Windows |
| `ip` / `netconfig` / `port` | `<adapter>` / `<port>` | Network interface & port lookup | Linux, macOS, Windows |
| `containers` | `containers` | Auto-detect Docker, Proxmox LXC/VM, ESXi, WSL | Linux, macOS, Windows |
| `sysinfo` / `processes` / `sqlite` | `sysinfo` / `processes` / `sqlite <db>` | System diagnostics & DB inspection | Linux, macOS, Windows |
| `install` / `uninstall` / `update` | `<package>` | Package management (`apt`, `brew`, `winget`, etc.) | Linux, macOS, Windows |

---

## 3. Cross-Platform Translated Commands (`hopit/translation.py`)

| Category | Unix / macOS Command | Windows Command | Translation Behavior |
| :--- | :--- | :--- | :--- |
| **File Ops** | `cp`, `mv`, `rm`, `ls`, `cat`, `touch`, `head`, `tail`, `wc`, `diff`, `stat`, `du`, `df`, `ln`, `less`, `more`, `sort`, `uniq`, `tee` | `copy`, `move`, `del`, `rd`, `dir`, `type`, `xcopy`, `robocopy`, `md`, `ren`, `fc`, `comp`, `tree` | Bi-directional automatic syntax translation across Windows `cmd`/PowerShell and POSIX shells. |
| **Search** | `grep`, `find`, `which`, `locate` | `findstr`, `dir /s /b`, `where` | Maps search flags and patterns across patterns. |
| **Processes** | `ps`, `kill`, `killall`, `pkill`, `pgrep`, `top`, `htop`, `nice` | `tasklist`, `taskkill`, `tskill`, `start` | PID and Process name termination/query translation. |
| **System Info** | `uname`, `whoami`, `hostname`, `uptime`, `free`, `lscpu`, `lsblk`, `lsusb`, `lspci`, `env`, `printenv`, `export`, `date`, `sleep` | `ver`, `whoami`, `hostname`, `wmic`, `set`, `timeout`, `systeminfo` | Normalizes platform environment and diagnostic output commands. |
| **Networking** | `ifconfig`, `ip`, `traceroute`, `nslookup`, `dig`, `host`, `wget`, `curl`, `ssh`, `scp`, `netstat`, `ss`, `nmap`, `ping` | `ipconfig`, `tracert`, `nslookup`, `netstat` | Cross-translates networking tools and arguments. |
