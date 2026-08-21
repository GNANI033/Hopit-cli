# Hopit-CLI 🚀 *(Under Active Development)*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Platform Support](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg)](https://github.com/)

**Hopit-CLI** is a cross-platform, multi-backend, interactive system administration shell and developer CLI utility designed to bridge OS fragmentation, eliminate cryptic command flags, and bring intuitive, self-explanatory English commands to Linux, macOS, and Windows.

> ⚠️ **Note:** Hopit-CLI is currently **Under Development**. Core commands and translation layers are functional, with active expansion ongoing.

---

## 💡 The Problem & The Gap Hopit-CLI Fills

Managing multi-OS server environments, local dev setups, and firewalls requires constant context switching:

1. **OS & Backend Fragmentation:** 
   Configuring a firewall or checking active services requires learning totally different tools depending on the system: `ufw` on Ubuntu, `firewalld` (`firewall-cmd`) on Fedora/CentOS, `nftables`/`iptables` on bare metal, `pfctl` on macOS, and `Get-NetFirewallRule` on Windows PowerShell.
2. **Cryptic Flags & High Friction:**
   Memory overhead for daily admin tasks (killing a process bound to a port, inspecting SQLite databases, user permissions, networking setup) wastes developer time.
3. **Inconsistent Shell Utilities across Platforms:**
   Developers switching between Linux/macOS and Windows PowerShell often lose access to common POSIX aliases and workflow tools without heavy WSL overhead.

### 🌟 How Hopit-CLI Solves This
* **Multi-Backend Auto-Detection:** Enter standard English commands like `firewall allow 80` or `firewall status`. Hopit-CLI automatically detects the host OS and underlying firewall/network backend (`ufw`, `firewall-cmd`, `nftables`, `iptables`, `pfctl`, or Windows Defender Firewall) and translates the action into native OS execution.
* **Cisco IOS-Style Contextual Help (`?`):** Type `?` anywhere to discover available commands, search by prefix (e.g., `g?`), or get immediate positional argument assistance (e.g., `firewall ?` or `status ?`).
* **Cross-Platform POSIX Translation:** Native POSIX command mapping allows standard Linux utilities (`ls`, `grep`, `cat`, `ps`, `pkill`, `df`, `free`, `chmod`, `chown`) to execute transparently on Windows PowerShell/Cmd.
* **Interactive Setup Wizards & Rich UI:** Interactive wizard options guide step-by-step configuration for firewalls (`firewall`), network interface configurations (`netconfig`), formatted status reporting tables, and live process displays.

---

## ✨ Key Features

- 🛡️ **Universal Firewall Engine:** Interactive wizard or single-line commands to manage ports, protocols, and interfaces across 6 major backends.
- ⚡ **Interactive Terminal REPL:** Rich autocomplete using `prompt_toolkit`, custom prompt styling, history, and syntax highlighting.
- ❓ **Cisco IOS-Style Help (`?`):** Dynamic prefix-matching discovery and command positional assistance integrated into the parser loop.
- 🔌 **Developer Power Utilities:**
  - `killport <port>`: Instantly inspect and kill processes bound to any local port.
  - `gitsave [message]`: Smart one-line git add, commit, and push wrapper.
  - `sqlite <db_path>`: Quick interactive SQLite database shell.
  - `archive / download / search`: Multi-format archive manager, file downloader, and directory content searcher.
  - `user / group / permission`: Cross-platform user, group, and ACL permission setup.
  - `sysinfo / processes / containers`: Live host telemetry, process inspection, and container status reporting.
- 🔐 **Explicit Elevation Support:** Transparent handling of `sudo` prompts for privileged system administrative actions on Linux and macOS.

---

## 🛠️ Installation & Setup

### Prerequisites
- **Python 3.8+** installed on your system.

### 🐧 Linux & 🍎 macOS Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/Hopit-cli.git
   cd Hopit-cli
   ```

2. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run Hopit-CLI:**
   ```bash
   python3 hopit-cli.py
   ```
   *Optional: Make it executable system-wide:*
   ```bash
   chmod +x hopit-cli.py
   sudo ln -s $(pwd)/hopit-cli.py /usr/local/bin/hopit-cli
   ```

---

### 🪟 Windows Setup

On Windows, Hopit-CLI includes a batch installer for automated setup.

1. **Clone or download the repository.**
2. **Run the installation batch file:**
   Double-click `install-windows.bat` or run it from Command Prompt / PowerShell:
   ```cmd
   install-windows.bat
   ```
3. **Launch Hopit-CLI:**
   ```cmd
   hopit-cli
   ```
   *(Or execute directly with `python hopit-cli.py`)*

---

## 📖 Usage & Examples

### 1. Interactive Shell Mode
Launch the interactive shell by running `hopit-cli`. You'll be greeted with the Hopit command prompt:

```bash
$ hopit-cli
hopit> 
```

### 2. Context-Sensitive Help (`?`)
Never guess command syntax or flags again:

- **Search commands starting with a letter:**
  ```text
  hopit> g?
  Available commands starting with 'g':
    git       - Git repository management commands
    gitsave   - One-command git commit and push
  ```

- **Inspect specific command arguments:**
  ```text
  hopit> firewall ?
  Usage: firewall <action> [port] [protocol] [interface]
  Actions:
    status    - Display current active firewall rules and allowed ports
    allow     - Allow traffic on a port/protocol
    block     - Block traffic on a port/protocol
    config    - Launch interactive firewall setup wizard
  ```

### 3. Unified Firewall Commands
```bash
# Display rich colorized table of active rules across any OS backend
hopit> firewall status

# Allow HTTP and HTTPS traffic
hopit> firewall allow 80 tcp
hopit> firewall allow 443 tcp

# Block a port on a specific adapter
hopit> firewall block 8080 tcp eth0

# Launch step-by-step interactive setup wizard
hopit> firewall
```

### 4. Developer Productivity Utilities
```bash
# Kill whatever is running on port 3000
hopit> killport 3000

# Quick commit & push current changes
hopit> gitsave "feat: add network adapter support"

# Open interactive SQLite inspector
hopit> sqlite app.db

# Cross-platform process and system monitoring
hopit> sysinfo
hopit> processes
```

---

## 📂 Project Structure

```text
Hopit-cli/
├── hopit/
│   ├── main.py           # Core REPL loop, prompt rendering, and initialization
│   ├── commands.py       # Central command registry and execution handlers
│   ├── firewall.py       # Multi-backend firewall abstraction (UFW, Firewalld, PF, Windows)
│   ├── translation.py    # Cross-platform POSIX/Windows command translation engine
│   ├── ui.py             # Rich formatting, color palettes, and interactive wizards
│   ├── loaders.py        # Async data loaders and execution helper threads
│   ├── sysinfo.py        # Host system metrics and telemetry
│   ├── killport.py       # Port-to-process inspection and termination
│   ├── gitsave.py        # Automated git workflow helper
│   ├── sqlite.py         # Embedded SQLite inspector
│   ├── config.py         # Global configuration & state storage
│   ├── config_cmd.py     # CLI configuration management
│   ├── archive.py        # Zip/Tar archive utility
│   ├── search.py         # File & text search engine
│   └── download.py       # File downloader helper
├── COMMANDS_ROADMAP.md   # Complete command inventory and phase-by-phase roadmap
├── hopit-cli.py          # Main executable entry point
├── install-windows.bat   # Windows automated batch installer
├── install-windows.ps1   # Windows PowerShell setup script
└── requirements.txt      # Python package dependencies (prompt_toolkit, psutil, rich)
```

---

## 📜 License

Hopit-CLI is open-source software released under the **[MIT License](LICENSE)**. Feel free to use, modify, and distribute it in both open-source and commercial projects.
