# Hopit-CLI: Cross-Platform Command Compatibility Checklist

This checklist documents the cross-platform compatibility of Hopit-CLI commands across Windows, Linux, and macOS. With the latest updates, standard Unix filesystem utilities and logon session management are emulated natively in Python to ensure identical inputs, outputs, and flags across all platforms.

---

## 1. File Operations & System Utilities

| Command | Linux | macOS | Windows | Status / Emulation Method | Tested |
| :--- | :---: | :---: | :---: | :--- | :---: |
| `ls` / `ls -la` | Native | Native | Emulated | Custom Python script `hopit.ls` parses Unix flags and displays matching formats and colors. | Yes |
| `rm` / `rm -rf` | Native | Native | Emulated | Custom Python script `hopit.rm` handles recursive file/directory removal without OS-specific errors. | Yes |
| `cp` / `cp -r` | Native | Native | Emulated | Custom Python script `hopit.cp` maps folder and file copies recursively using `shutil`. | Yes |
| `mv` | Native | Native | Emulated | Custom Python script `hopit.mv` handles renaming and moving cross-platform safely. | Yes |
| `mkdir` / `mkdir -p` | Native | Native | Emulated | Custom Python script `hopit.mkdir` supports parent directory creation flag `-p` consistently. | Yes |
| `list` / `list all` | Native | Native | Native | Built-in CLI command, maps to native `dir` on Windows and `ls` on Unix. | Yes |
| `cd` / `back` | Native | Native | Native | Shell context directory traversal managed inside the CLI loop. | Yes |
| `cat` | Native | Native | Emulated | Custom Python script `hopit.cat` with syntax highlighting and unicode safe fallbacks. | Yes |
| `touch` | Native | Native | Emulated | Custom Python script `hopit.touch` creates files or updates modification times. | Yes |
| `head` | Native | Native | Emulated | Custom Python script `hopit.head` displays top lines. | Yes |
| `tail` | Native | Native | Emulated | Custom Python script `hopit.tail` displays bottom lines. | Yes |
| `less` | Native | Native | Emulated | Custom Python script `hopit.less` pages files. | Yes |
| `tree` | Native | Native | Emulated | Custom Python script `hopit.tree` prints folder hierarchies. | Yes |

---

## 2. Session Management

The `sessions` command suite has been upgraded to support Windows Home Edition (where `query user` is missing) via progressive fallback logic.

| Command | Linux | macOS | Windows | Status / Emulation Method | Tested |
| :--- | :---: | :---: | :---: | :--- | :---: |
| `sessions list` | Native | Native | Emulated | Queries `query user` -> `qwinsta` -> WMI/CIM via PowerShell -> `getpass` fallback. | Yes |
| `sessions kill <id>` | Native | Native | Native | Terminates logon sessions via `logoff <id>` (Windows) or terminal multiplexer endpoints. | Yes |
| `w` / `who` | Native | Native | Emulated | Translates transparently to `python -m hopit.sessions list` on Windows. | Yes |
| `quser` / `qwinsta` | Native | Native | Emulated | Mapped to unified Python session list on all platforms. | Yes |
| `query user` | Native | Native | Emulated | Mapped to unified Python session list on all platforms. | Yes |
| `logoff` | Native | Native | Emulated | Translates transparently to `python -m hopit.sessions kill` on Windows. | Yes |
| `loginctl` | Native | Native | Emulated | Mapped to unified python session manager commands. | Yes |

---

## 3. Network & Diagnostics

| Command | Linux | macOS | Windows | Status / Emulation Method | Tested |
| :--- | :---: | :---: | :---: | :--- | :---: |
| `ping` | Native | Native | Native | Pass-through to system ping. | Yes |
| `traceroute` | Native | Native | Native | Maps to `tracert` on Windows and `traceroute` on Unix. | Yes |
| `dns` / `lookup` | Native | Native | Native | Handled via Python `dns` resolution / diagnostics modules. | Yes |
| `netconfig` | Native | Native | Native | Handles DHCP release/renew and static IP assignment cross-platform. | Yes |
| `connections` | Native | Native | Native | Formats network connections with PID/owner details into tables. | Yes |

---

## 4. Process Management

| Command | Linux | macOS | Windows | Status / Emulation Method | Tested |
| :--- | :---: | :---: | :---: | :--- | :---: |
| `processes` / `ps` | Native | Native | Native | Custom Python module `hopit.processes` lists and sorts processes. | Yes |
| `process <pid>` | Native | Native | Native | Inspects specific process details. | Yes |
| `kill` / `pkill` | Native | Native | Native | Translates to `taskkill` on Windows and standard `kill` on Unix. | Yes |
| `top` / `resources` | Native | Native | Native | Displays resource dashboards and active metrics. | Yes |
