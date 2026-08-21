import os
import shlex
from hopit.config import IS_WINDOWS, IS_MACOS

# --- helpers used inside lambdas --------------------------------------------
def _q(s: str) -> str:
    """Shell-quote a single token (minimal, good enough for paths)."""
    if IS_WINDOWS:
        return f'"{s}"'
    return "'" + s.replace("'", "'\\''") + "'"

def _join(args): return ' '.join(_q(a) for a in args)
def _files(args): return ' '.join(_q(a) for a in args if not a.startswith('-'))
def _nval(args, flag, default="10"):
    """Extract value after -n / --lines flag."""
    for i, a in enumerate(args):
        if a in ("-n", "--lines", "-"+flag) and i+1 < len(args):
            return args[i+1]
    return default

# --- Linux/macOS -> Windows ------------------------------------------------
_UNIX_TO_WIN: dict = {
    # -- file ops ----------------------------------------------------------
    "cp":       lambda a: (f'xcopy /E /I /H /Y {_q(a[0])} {_q(a[1])}' if len(a)>=2 and os.path.isdir(a[0])
                           else 'copy ' + _join(a)),
    "mv":       lambda a: 'move ' + _join(a),
    "rm":       lambda a: ('rd /s /q ' if any(x in ('-r','-rf','-fr','-Rf') for x in a) else 'del /Q ')
                          + ' '.join(_q(x) for x in a if not x.startswith('-')),
    "ls":       lambda a: 'dir ' + ' '.join(a),
    "cat":      lambda a: 'type ' + _files(a),
    "touch":    lambda a: f'type nul > {_q(a[0])}' if a else 'type nul',
    "head":     lambda a: 'powershell -Command "Get-Content ' + _files(a) + ' -TotalCount ' + _nval(a,'n') + '"',
    "tail":     lambda a: 'powershell -Command "Get-Content ' + _files(a) + ' -Tail ' + _nval(a,'n') + '"',
    "wc":       lambda a: ('find /c /v "" ' + _files(a) if '-l' in a
                           else 'powershell -Command "(Get-Content ' + _files(a) + ' | Measure-Object -Word).Words"'),
    "diff":     lambda a: 'fc ' + _join(a),
    "stat":     lambda a: f'powershell -Command "Get-Item {_files(a)} | Format-List *"',
    "du":       lambda a: 'dir /s ' + _files(a),
    "df":       lambda a: 'powershell -Command "Get-PSDrive -PSProvider FileSystem | Format-Table"',
    "ln":       lambda a: ('mklink /D ' + _q(a[-1]) + ' ' + _q(a[-2])
                           if len(a)>=2 and '-s' in a and os.path.isdir(a[-2])
                           else 'mklink ' + _q(a[-1]) + ' ' + _q(a[-2])) if len(a)>=2 else '',
    "chmod":    lambda a: '',   # no equivalent; silently skip
    "chown":    lambda a: '',
    "chgrp":    lambda a: '',
    "less":     lambda a: 'more ' + _files(a),
    "more":     lambda a: 'more ' + _files(a),
    "sort":     lambda a: 'sort ' + ' '.join(a),
    "uniq":     lambda a: 'powershell -Command "Get-Content {_files(a)} | Sort-Object -Unique"',
    "tee":      lambda a: 'powershell -Command "Tee-Object -FilePath ' + (_q(a[-1]) if a else '"out.txt"') + '"',
    "zip":      lambda a: f'powershell -Command "Compress-Archive -Path {_q(a[0] if a else ".")} -DestinationPath {_q((a[1] if len(a)>1 else a[0]+".zip") if a else "archive.zip")}"',
    "unzip":    lambda a: f'powershell -Command "Expand-Archive -Path {_q(a[0] if a else ".")} -DestinationPath {_q(a[1] if len(a)>1 else ".")}"',
    "tar":      lambda a: 'tar ' + ' '.join(a),   # Windows 10+ ships tar
    # -- search ------------------------------------------------------------
    "grep":     lambda a: 'findstr ' + ' '.join(a),
    "find":     lambda a: 'dir /s /b ' + _files(a),
    "which":    lambda a: 'where ' + ' '.join(a),
    "locate":   lambda a: 'where /r . ' + (a[0] if a else ''),
    # -- processes ---------------------------------------------------------
    "ps":       lambda a: 'tasklist',
    "kill":     lambda a: (f'taskkill /PID {a[0]} /F' if a and a[0].lstrip('-').isdigit()
                           else 'taskkill /IM ' + (a[0] if a else '') + ' /F'),
    "killall":  lambda a: 'taskkill /IM ' + (a[0] if a else '') + ' /F',
    "pkill":    lambda a: 'taskkill /IM ' + (a[0] if a else '') + ' /F',
    "pgrep":    lambda a: 'tasklist | findstr /I ' + (a[0] if a else ''),
    "top":      lambda a: 'powershell -Command "Get-Process | Sort-Object CPU -Descending | Select-Object -First 20 | Format-Table -AutoSize"',
    "htop":     lambda a: 'powershell -Command "Get-Process | Sort-Object CPU -Descending | Select-Object -First 20 | Format-Table -AutoSize"',
    "nice":     lambda a: ' '.join(a),   # Windows scheduling is different
    # -- system ------------------------------------------------------------
    "uname":    lambda a: 'powershell -Command "[System.Environment]::OSVersion.VersionString"',
    "whoami":   lambda a: 'whoami',
    "hostname": lambda a: 'hostname',
    "uptime":   lambda a: 'powershell -Command "(Get-Date)-(gcim Win32_OperatingSystem).LastBootUpTime|Select Days,Hours,Minutes"',
    "free":     lambda a: 'powershell -Command "gcim Win32_OperatingSystem|Select TotalVisibleMemorySize,FreePhysicalMemory"',
    "lscpu":    lambda a: 'wmic cpu get Name,NumberOfCores,MaxClockSpeed',
    "lsblk":    lambda a: 'wmic diskdrive list brief',
    "lsusb":    lambda a: 'powershell -Command "Get-PnpDevice -Class USB | Format-Table"',
    "lspci":    lambda a: 'powershell -Command "Get-PnpDevice | Format-Table"',
    "env":      lambda a: 'set',
    "printenv": lambda a: ('echo %" + a[0] + "%') if a else 'set',
    "export":   lambda a: ('set ' + a[0]) if a else 'set',
    "history":  lambda a: 'doskey /history',
    "man":      lambda a: (' '.join(a) + ' --help') if a else 'help',
    "sudo":     lambda a: ' '.join(a),   # run without elevation (user must launch as admin)
    "su":       lambda a: 'runas /user:Administrator cmd',
    "date":     lambda a: 'powershell -Command "Get-Date"',
    "sleep":    lambda a: 'timeout /T ' + (a[0] if a else '1') + ' /NOBREAK',
    "reboot":   lambda a: 'shutdown /r /t 0',
    "shutdown": lambda a: 'shutdown /s /t 0',
    "halt":     lambda a: 'shutdown /s /t 0',
    # -- network -----------------------------------------------------------
    "ifconfig": lambda a: 'ipconfig /all',
    "ip":       lambda a: 'ipconfig /all',
    "traceroute": lambda a: 'tracert ' + ' '.join(a),
    "nslookup": lambda a: 'nslookup ' + ' '.join(a),
    "dig":      lambda a: 'nslookup ' + (a[0] if a else ''),
    "host":     lambda a: 'nslookup ' + (a[0] if a else ''),
    "wget":     lambda a: 'curl -L -O ' + (a[-1] if a else ''),
    "curl":     lambda a: 'curl ' + ' '.join(a),   # Windows 10+ ships curl
    "ssh":      lambda a: 'ssh ' + ' '.join(a),    # Windows 10+ ships OpenSSH
    "scp":      lambda a: 'scp ' + ' '.join(a),
    "netstat":  lambda a: 'netstat ' + ' '.join(a),
    "ss":       lambda a: 'netstat -ano',
    "nmap":     lambda a: 'nmap ' + ' '.join(a),
    "ping":     lambda a: 'ping ' + ' '.join(a),
    # -- text / misc -------------------------------------------------------
    "echo":     lambda a: 'echo ' + ' '.join(a),
    "clear":    lambda a: 'cls',
    "pwd":      lambda a: 'cd',
    "xdg-open": lambda a: 'start ' + (a[0] if a else '.'),
    "open":     lambda a: 'start ' + (a[0] if a else '.'),   # macOS open
    "xclip":    lambda a: 'clip',
    "xsel":     lambda a: 'clip',
    "strings":  lambda a: 'findstr /p ' + ' '.join(a),
    "base64":   lambda a: f'powershell -Command "[Convert]::ToBase64String([IO.File]::ReadAllBytes({_q(a[0])}))"' if a else '',
    "md5sum":   lambda a: f'powershell -Command "Get-FileHash {_files(a)} -Algorithm MD5 | Format-Table"',
    "sha256sum": lambda a: f'powershell -Command "Get-FileHash {_files(a)} -Algorithm SHA256 | Format-Table"',
    "crontab":  lambda a: 'schtasks ' + ' '.join(a),
    "service":  lambda a: ('sc start ' + a[1] if len(a)>=2 and a[1]=='start'
                           else 'sc stop ' + a[1] if len(a)>=2 and a[1]=='stop'
                           else 'sc query ' + (a[0] if a else '')),
    "systemctl": lambda a: ('sc start ' + a[1] if len(a)>=2 and a[0]=='start'
                             else 'sc stop ' + a[1] if len(a)>=2 and a[0]=='stop'
                             else 'sc query ' + (a[1] if len(a)>=2 else '')),
}

# --- Windows -> Linux/macOS ------------------------------------------------
_WIN_TO_UNIX: dict = {
    # -- file ops ----------------------------------------------------------
    "del":      lambda a: 'rm ' + _join([x for x in a if not x.startswith('/')]),
    "rd":       lambda a: 'rm -rf ' + _join([x for x in a if not x.startswith('/')]),
    "rmdir":    lambda a: 'rmdir ' + _join(a),
    "dir":      lambda a: 'ls -la ' + ' '.join(a),
    "type":     lambda a: 'cat ' + _join(a),
    "xcopy":    lambda a: 'cp -r ' + _join(a),
    "robocopy": lambda a: 'rsync -av ' + _join(a[:2]) if len(a)>=2 else 'rsync -av ' + _join(a),
    "md":       lambda a: 'mkdir -p ' + _join(a),
    "ren":      lambda a: 'mv ' + _join(a),
    "attrib":   lambda a: '',    # no Unix equivalent; skip
    "fc":       lambda a: 'diff ' + _join(a),
    "comp":     lambda a: 'diff ' + _join(a),
    "more":     lambda a: 'less ' + _join(a),
    "tree":     lambda a: ('find ' + (a[0] if a else '.') + ' | sort'),
    "compact":  lambda a: '',    # NTFS-only; skip
    # -- processes ---------------------------------------------------------
    "tasklist": lambda a: 'ps aux',
    "taskkill": lambda a: ('kill ' + next((a[i+1] for i,x in enumerate(a) if x.upper()=='/PID'), '')
                           if any(x.upper()=='/PID' for x in a)
                           else 'killall ' + next((a[i+1] for i,x in enumerate(a) if x.upper()=='/IM'), '')),
    "tskill":   lambda a: 'kill ' + (a[0] if a else ''),
    "start":    lambda a: 'xdg-open ' + (a[0] if a else '.'),
    # -- system ------------------------------------------------------------
    "cls":      lambda a: 'clear',
    "ver":      lambda a: 'uname -r',
    "systeminfo": lambda a: 'uname -a && cat /proc/cpuinfo | head -20',
    "set":      lambda a: ('echo "$' + a[0].split('=')[0] + '"' if a and '=' not in a[0] else 'env'),
    "where":    lambda a: 'which ' + ' '.join(a),
    "whoami":   lambda a: 'whoami',
    "hostname":  lambda a: 'hostname',
    "date":     lambda a: 'date',
    "time":     lambda a: 'date +%T',
    "timeout":  lambda a: 'sleep ' + next((a[i+1] for i,x in enumerate(a) if x.upper()=='/T'), a[0] if a else '1'),
    "waitfor":  lambda a: 'sleep 60',
    "runas":    lambda a: 'sudo ' + ' '.join(a),
    "chdir":    lambda a: 'pwd' if not a else 'cd ' + (a[0] if a else ''),
    "path":     lambda a: 'echo $PATH',
    "help":     lambda a: ('man ' + a[0] if a else 'help'),
    "assoc":    lambda a: 'xdg-mime query default ' + (a[0] if a else ''),
    "schtasks": lambda a: 'crontab -l',
    "ipconfig": lambda a: 'ip a',
    "tracert":  lambda a: 'traceroute ' + ' '.join(a),
    "nslookup": lambda a: 'nslookup ' + ' '.join(a),
    "netstat":  lambda a: 'netstat ' + ' '.join(a),
    "ping":     lambda a: 'ping ' + ' '.join(a),
    "nmap":     lambda a: 'nmap ' + ' '.join(a),
    "curl":     lambda a: 'curl ' + ' '.join(a),
    "ssh":      lambda a: 'ssh ' + ' '.join(a),
    "scp":      lambda a: 'scp ' + ' '.join(a),
    "echo":     lambda a: 'echo ' + ' '.join(a),
    "reg":      lambda a: '',    # Windows registry; no Unix equivalent
    "regedit":  lambda a: '',
    "msiexec":  lambda a: '',
    "wmic":     lambda a: '',
    "sc":       lambda a: ('systemctl start ' + a[1] if len(a)>=2 and a[0]=='start'
                           else 'systemctl stop ' + a[1] if len(a)>=2 and a[0]=='stop'
                           else 'systemctl status ' + a[1] if len(a)>=2 and a[0]=='query'
                           else 'systemctl ' + ' '.join(a)),
    "net":      lambda a: ('systemctl start ' + a[1] if len(a)>=2 and a[0].lower()=='start'
                           else 'systemctl stop ' + a[1] if len(a)>=2 and a[0].lower()=='stop'
                           else ' '.join(a)),
}

# --- Linux-specific -> macOS equivalent (applied on macOS only) -----------
_LINUX_TO_MAC: dict = {
    "xdg-open":  lambda a: 'open ' + (a[0] if a else '.'),
    "xclip":     lambda a: ('pbcopy' if not any('paste' in x for x in a) else 'pbpaste'),
    "xsel":      lambda a: ('pbpaste' if '--output' in a or '-o' in a else 'pbcopy'),
    "update-alternatives": lambda a: '',
    "apt":       lambda a: 'brew ' + ' '.join(a),
    "apt-get":   lambda a: 'brew ' + ' '.join(a),
    "dpkg":      lambda a: 'brew ' + ' '.join(a),
    "yum":       lambda a: 'brew ' + ' '.join(a),
    "dnf":       lambda a: 'brew ' + ' '.join(a),
    "pacman":    lambda a: 'brew ' + ' '.join(a),
    "systemctl": lambda a: ('brew services start ' + a[1] if len(a)>=2 and a[0]=='start'
                            else 'brew services stop ' + a[1] if len(a)>=2 and a[0]=='stop'
                            else 'brew services restart ' + a[1] if len(a)>=2 and a[0]=='restart'
                            else 'brew services info ' + a[1] if len(a)>=2 and a[0] in ('status','is-active')
                            else 'brew services list' if a and a[0]=='list' else 'launchctl ' + ' '.join(a)),
    "journalctl": lambda a: ('log stream' if '-f' in a else 'log show --last 1h') + (
                             ' --predicate \'process == "' + next((a[i+1] for i,x in enumerate(a) if x=='-u'), '') + '"\''),
    "service":   lambda a: ('brew services start ' + a[0] if len(a)>=2 and a[1]=='start'
                            else 'brew services stop ' + a[0] if len(a)>=2 and a[1]=='stop'
                            else 'brew services info ' + (a[0] if a else '')),
    "ss":        lambda a: ('lsof -i :' + next((x for x in a if x.isdigit()),'') if any(x.isdigit() for x in a) else 'lsof -i -n -P'),
    "ip":        lambda a: 'ifconfig',
    "netstat":   lambda a: 'netstat ' + ' '.join(a),
    "free":      lambda a: 'vm_stat',
    "lscpu":     lambda a: 'sysctl -a | grep machdep.cpu',
    "lsblk":     lambda a: 'diskutil list',
    "lsusb":     lambda a: 'system_profiler SPUSBDataType',
    "lspci":     lambda a: 'system_profiler SPPCIDataType',
    "nproc":     lambda a: 'sysctl -n hw.logicalcpu',
    "uname":     lambda a: 'uname ' + ' '.join(a),   # works on macOS too
    "fuser":     lambda a: 'lsof ' + ' '.join(a),
}

# --- macOS-specific -> Linux equivalent (applied on Linux only) -----------
_MAC_TO_LINUX: dict = {
    "open":      lambda a: 'xdg-open ' + (a[0] if a else '.'),
    "pbcopy":    lambda a: 'xclip -selection clipboard',
    "pbpaste":   lambda a: 'xclip -selection clipboard -o',
    "say":       lambda a: 'espeak ' + ' '.join(a),
    "sw_vers":   lambda a: 'cat /etc/os-release',
    "launchctl": lambda a: ('systemctl start ' + a[1] if len(a)>=2 and a[0]=='load'
                            else 'systemctl stop ' + a[1] if len(a)>=2 and a[0]=='unload'
                            else 'systemctl ' + ' '.join(a)),
    "brew":      lambda a: ('apt-get install ' + ' '.join(a[1:]) if a and a[0]=='install'
                            else 'apt-get remove ' + ' '.join(a[1:]) if a and a[0]=='remove'
                            else 'apt-get update && apt-get upgrade' if a and a[0]=='upgrade'
                            else 'apt-get ' + ' '.join(a)),
    "diskutil":  lambda a: 'lsblk',
    "networksetup": lambda a: 'nmcli ' + ' '.join(a),
    "caffeinate": lambda a: '',
    "osascript": lambda a: '',
    "defaults":  lambda a: '',
    "plutil":    lambda a: '',
    "dscacheutil": lambda a: '',
    "airport":   lambda a: 'iwconfig',
}


def translate_cross_platform(tokens: list[str]) -> str | None:
    """Translate a foreign-OS command to the current OS's equivalent.

    Tables used per platform:
      Windows  : _UNIX_TO_WIN   (Linux/macOS commands -> Windows)
      macOS    : _WIN_TO_UNIX + _LINUX_TO_MAC (Windows + Linux-specific -> macOS)
      Linux    : _WIN_TO_UNIX + _MAC_TO_LINUX (Windows + macOS-specific -> Linux)
    """
    if not tokens:
        return None
    cmd = tokens[0].lower()
    args = tokens[1:]

    if IS_WINDOWS:
        table = _UNIX_TO_WIN
    elif IS_MACOS:
        table = {**_WIN_TO_UNIX, **_LINUX_TO_MAC}
    else:
        table = {**_WIN_TO_UNIX, **_MAC_TO_LINUX}

    fn = table.get(cmd)
    if fn is None:
        return None
    try:
        translated = fn(args)
    except Exception:
        return None
    return translated.strip() if translated and translated.strip() else None
