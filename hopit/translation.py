import os
import shlex
from hopit.config import IS_WINDOWS, IS_MACOS

def _q(s: str) -> str:
    """Shell-quote a single token (minimal, good enough for paths)."""
    if IS_WINDOWS:
        # Strip characters that can be used for command execution or quotes in cmd.exe
        safe = s.replace('"', '').replace('&', '').replace('|', '').replace('^', '').replace('(', '').replace(')', '').replace('<', '').replace('>', '').replace('%', '')
        return f'"{safe}"'
    return "'" + s.replace("'", "'\\''") + "'"

def _join(args): return ' '.join(_q(a) for a in args)
def _files(args): return ' '.join(_q(a) for a in args if not a.startswith('-'))
def _nval(args, flag, default="10"):
    """Extract value after -n / --lines flag."""
    for i, a in enumerate(args):
        if a in ("-n", "--lines", "-"+flag) and i+1 < len(args):
            return args[i+1]
    return default

def translate_chmod_to_windows(args: list[str]) -> str:
    recursive = False
    perms = ""
    path = ""
    for a in args:
        if a in ('-R', '--recursive'):
            recursive = True
        elif a.startswith('-'):
            continue
        elif not perms:
            perms = a
        else:
            path = a
            
    if not perms or not path:
        return ""
        
    is_octal = perms.isdigit() and len(perms) in (3, 4)
    cmd = "icacls " + _q(path)
    inh = "(OI)(CI)" if os.path.isdir(path) else ""
    rec_flag = " /t" if recursive else ""
    
    if is_octal:
        oct_str = perms[-3:]
        u, g, o = int(oct_str[0]), int(oct_str[1]), int(oct_str[2])
        
        def to_win_perm(val: int) -> str:
            if val >= 7: return "F"
            if val >= 6: return "M"
            if val >= 5: return "RX"
            if val >= 4: return "R"
            if val >= 2: return "W"
            if val >= 1: return "RX"
            return ""
            
        u_perm = to_win_perm(u)
        g_perm = to_win_perm(g)
        o_perm = to_win_perm(o)
        
        parts = []
        if u_perm:
            parts.append(f"/grant:r *S-1-5-32-544:{inh}({u_perm})")
        if g_perm:
            parts.append(f"/grant:r *S-1-5-32-545:{inh}({g_perm})")
        if o_perm:
            parts.append(f"/grant:r *S-1-1-0:{inh}({o_perm})")
            
        if parts:
            return f"{cmd} {' '.join(parts)}{rec_flag}"
            
    elif "+" in perms or "-" in perms or "=" in perms:
        action = "+" if "+" in perms else "-" if "-" in perms else "="
        who, rights = perms.split(action, 1)
        
        win_rights = ""
        if "r" in rights: win_rights += "R"
        if "w" in rights: win_rights += "W"
        if "x" in rights: win_rights += "X"
        
        if win_rights == "X": win_rights = "RX"
        if not win_rights: win_rights = "R"
        
        target = "*S-1-1-0"
        if "u" in who:
            target = "*S-1-5-32-544"
        elif "g" in who:
            target = "*S-1-5-32-545"
            
        if action == "+":
            return f"{cmd} /grant:r {target}:{inh}({win_rights}){rec_flag}"
        elif action == "-":
            return f"{cmd} /remove {target}{rec_flag}"
            
    return ""


def translate_chown_to_windows(args: list[str]) -> str:
    recursive = False
    owner = ""
    path = ""
    for a in args:
        if a in ('-R', '--recursive'):
            recursive = True
        elif a.startswith('-'):
            continue
        elif not owner:
            owner = a
        else:
            path = a
    if not owner or not path:
        return ""
    if ":" in owner:
        owner = owner.split(":")[0]
    rec_flag = " /t" if recursive else ""
    return f"icacls {_q(path)} /setowner {_q(owner)}{rec_flag}"


def translate_chgrp_to_windows(args: list[str]) -> str:
    recursive = False
    group = ""
    path = ""
    for a in args:
        if a in ('-R', '--recursive'):
            recursive = True
        elif a.startswith('-'):
            continue
        elif not group:
            group = a
        else:
            path = a
    if not group or not path:
        return ""
    rec_flag = " /t" if recursive else ""
    return f"icacls {_q(path)} /setowner {_q(group)}{rec_flag}"


def translate_useradd_to_windows(args: list[str]) -> str:
    username = ""
    password = ""
    for a in args:
        if a.startswith('-'):
            continue
        if not username:
            username = a
        elif not password:
            password = a
    if not username:
        return ""
    if password:
        return f"net user {_q(username)} {_q(password)} /add"
    return f"net user {_q(username)} /add"


def translate_userdel_to_windows(args: list[str]) -> str:
    username = next((a for a in args if not a.startswith('-')), "")
    if not username:
        return ""
    return f"net user {_q(username)} /delete"


def translate_passwd_to_windows(args: list[str]) -> str:
    username = next((a for a in args if not a.startswith('-')), "")
    if not username:
        return "net user %USERNAME% *"
    if len(args) >= 2:
        password = args[1]
        return f"net user {_q(username)} {_q(password)}"
    return f"net user {_q(username)} *"


def translate_usermod_to_windows(args: list[str]) -> str:
    if not args:
        return ""
    if '-L' in args:
        username = next((a for a in args if a != '-L'), "")
        if username:
            return f"net user {_q(username)} /active:no"
    if '-U' in args:
        username = next((a for a in args if a != '-U'), "")
        if username:
            return f"net user {_q(username)} /active:yes"
    group = ""
    username = ""
    for i, a in enumerate(args):
        if a in ('-aG', '-G', '--groups', '--append'):
            if i + 1 < len(args):
                group = args[i+1]
        elif a.startswith('-'):
            continue
        else:
            if not group or a != group:
                username = a
    if group and username:
        if "," in group:
            group = group.split(",")[0]
        return f"net localgroup {_q(group)} {_q(username)} /add"
    return ""


def translate_usermod_to_mac(args: list[str]) -> str:
    if not args:
        return ""
    if '-L' in args:
        username = next((a for a in args if a != '-L'), "")
        if username:
            return f"dscl . -create /Users/{_q(username)} UserShell /usr/bin/false"
    if '-U' in args:
        username = next((a for a in args if a != '-U'), "")
        if username:
            return f"dscl . -create /Users/{_q(username)} UserShell /bin/bash"
    group = ""
    username = ""
    for i, a in enumerate(args):
        if a in ('-aG', '-G', '--groups', '--append'):
            if i + 1 < len(args):
                group = args[i+1]
        elif a.startswith('-'):
            continue
        else:
            if not group or a != group:
                username = a
    if group and username:
        if "," in group:
            group = group.split(",")[0]
        return f"dseditgroup -o edit -a {_q(username)} -t user {_q(group)}"
    return ""


def translate_groupadd_to_windows(args: list[str]) -> str:
    group = next((a for a in args if not a.startswith('-')), "")
    if not group:
        return ""
    return f"net localgroup {_q(group)} /add"


def translate_groupdel_to_windows(args: list[str]) -> str:
    group = next((a for a in args if not a.startswith('-')), "")
    if not group:
        return ""
    return f"net localgroup {_q(group)} /delete"


def translate_win_net_to_unix(args: list[str]) -> str:
    if not args:
        return "net"
    sub = args[0].lower()
    
    def has_flag(flag: str) -> bool:
        return any(flag.lower() in x.lower() for x in args)
        
    if sub == "user":
        if len(args) == 1:
            if IS_MACOS:
                return "dscl . list /Users"
            return "cut -d: -f1 /etc/passwd"
        username = args[1]
        if username.startswith('/') or username.startswith('-'):
            return "net user"
        if has_flag("/delete") or has_flag("-delete"):
            if IS_MACOS:
                return f"sysadminctl -deleteUser {_q(username)}"
            return f"userdel -r {_q(username)}"
        if has_flag("/add") or has_flag("-add"):
            password = ""
            if len(args) >= 3 and not args[2].startswith('/') and not args[2].startswith('-'):
                password = args[2]
            if IS_MACOS:
                pw_part = f" -password {_q(password)}" if password else ""
                return f"sysadminctl -addUser {_q(username)}{pw_part}"
            if password:
                return f"useradd -m {_q(username)} && echo '{username}:{password}' | chpasswd"
            return f"useradd -m {_q(username)}"
        if any("/active:no" in x.lower() for x in args):
            if IS_MACOS:
                return f"dscl . -create /Users/{_q(username)} UserShell /usr/bin/false"
            return f"usermod -L {_q(username)}"
        if any("/active:yes" in x.lower() for x in args):
            if IS_MACOS:
                return f"dscl . -create /Users/{_q(username)} UserShell /bin/bash"
            return f"usermod -U {_q(username)}"
        if len(args) == 2:
            return f"id {_q(username)}"
            
    elif sub == "localgroup":
        if len(args) == 1:
            if IS_MACOS:
                return "dscl . list /Groups"
            return "cut -d: -f1 /etc/group"
        groupname = args[1]
        if groupname.startswith('/') or groupname.startswith('-'):
            return "net localgroup"
        if len(args) == 2:
            if IS_MACOS:
                return f"dscl . -read /Groups/{_q(groupname)} GroupMembership"
            return f"getent group {_q(groupname)}"
        member = args[2]
        if member.startswith('/') or member.startswith('-'):
            if has_flag("/add") or has_flag("-add"):
                if IS_MACOS:
                    return f"dseditgroup -o create {_q(groupname)}"
                return f"groupadd {_q(groupname)}"
            if has_flag("/delete") or has_flag("-delete"):
                if IS_MACOS:
                    return f"dseditgroup -o delete {_q(groupname)}"
                return f"groupdel {_q(groupname)}"
        else:
            if has_flag("/add") or has_flag("-add"):
                if IS_MACOS:
                    return f"dseditgroup -o edit -a {_q(member)} -t user {_q(groupname)}"
                return f"usermod -aG {_q(groupname)} {_q(member)}"
            if has_flag("/delete") or has_flag("-delete"):
                if IS_MACOS:
                    return f"dseditgroup -o edit -d {_q(member)} -t user {_q(groupname)}"
                return f"gpasswd -d {_q(member)} {_q(groupname)}"
                
    if len(args) >= 2 and args[0].lower() == 'start':
        return 'systemctl start ' + args[1]
    if len(args) >= 2 and args[0].lower() == 'stop':
        return 'systemctl stop ' + args[1]
    return ' '.join(args)


# --- Linux/macOS -> Windows ------------------------------------------------
_UNIX_TO_WIN: dict = {
    # -- file ops ----------------------------------------------------------
    "cp":       lambda a: (f'xcopy /E /I /H /Y {_q(a[0])} {_q(a[1])}' if len(a)>=2 and os.path.isdir(a[0])
                           else 'copy ' + _join(a)),
    "mv":       lambda a: 'move ' + _join(a),
    "rm":       lambda a: ('rd /s /q ' if any(x in ('-r','-rf','-fr','-Rf') for x in a) else 'del /Q ')
                           + ' '.join(_q(x) for x in a if not x.startswith('-')),
    "ls":       lambda a: 'dir ' + _join(a),
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
    "chmod":    translate_chmod_to_windows,
    "chown":    translate_chown_to_windows,
    "chgrp":    translate_chgrp_to_windows,
    "useradd":  translate_useradd_to_windows,
    "adduser":  translate_useradd_to_windows,
    "userdel":  translate_userdel_to_windows,
    "deluser":  translate_userdel_to_windows,
    "passwd":   translate_passwd_to_windows,
    "usermod":  translate_usermod_to_windows,
    "groupadd": translate_groupadd_to_windows,
    "addgroup": translate_groupadd_to_windows,
    "groupdel": translate_groupdel_to_windows,
    "delgroup": translate_groupdel_to_windows,
    "less":     lambda a: 'more ' + _files(a),
    "more":     lambda a: 'more ' + _files(a),
    "sort":     lambda a: 'sort ' + _join(a),
    "uniq":     lambda a: 'powershell -Command "Get-Content {_files(a)} | Sort-Object -Unique"',
    "tee":      lambda a: 'powershell -Command "Tee-Object -FilePath ' + (_q(a[-1]) if a else '"out.txt"') + '"',
    "zip":      lambda a: f'powershell -Command "Compress-Archive -Path {_q(a[0] if a else ".")} -DestinationPath {_q((a[1] if len(a)>1 else a[0]+".zip") if a else "archive.zip")}"',
    "unzip":    lambda a: f'powershell -Command "Expand-Archive -Path {_q(a[0] if a else ".")} -DestinationPath {_q(a[1] if len(a)>1 else ".")}"',
    "tar":      lambda a: 'tar ' + _join(a),   # Windows 10+ ships tar
    # -- search ------------------------------------------------------------
    "grep":     lambda a: 'findstr ' + _join(a),
    "find":     lambda a: 'dir /s /b ' + _files(a),
    "which":    lambda a: 'where ' + _join(a),
    "locate":   lambda a: 'where /r . ' + (_q(a[0]) if a else ''),
    # -- processes ---------------------------------------------------------
    "ps":       lambda a: 'tasklist',
    "kill":     lambda a: (f'taskkill /PID {a[0]} /F' if a and a[0].lstrip('-').isdigit()
                           else 'taskkill /IM ' + (_q(a[0]) if a else '') + ' /F'),
    "killall":  lambda a: 'taskkill /IM ' + (_q(a[0]) if a else '') + ' /F',
    "pkill":    lambda a: 'taskkill /IM ' + (_q(a[0]) if a else '') + ' /F',
    "pgrep":    lambda a: 'tasklist | findstr /I ' + (_q(a[0]) if a else ''),
    "top":      lambda a: 'powershell -Command "Get-Process | Sort-Object CPU -Descending | Select-Object -First 20 | Format-Table -AutoSize"',
    "htop":     lambda a: 'powershell -Command "Get-Process | Sort-Object CPU -Descending | Select-Object -First 20 | Format-Table -AutoSize"',
    "nice":     lambda a: _join(a),   # Windows scheduling is different
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
    "printenv": lambda a: ('echo %' + a[0].replace('%','') + '%') if a else 'set',
    "export":   lambda a: ('set ' + a[0].replace('&','').replace('|','').replace('^','')) if a else 'set',
    "history":  lambda a: 'doskey /history',
    "man":      lambda a: (_join(a) + ' --help') if a else 'help',
    "sudo":     lambda a: _join(a),   # run without elevation (user must launch as admin)
    "su":       lambda a: 'runas /user:Administrator cmd',
    "date":     lambda a: 'powershell -Command "Get-Date"',
    "sleep":    lambda a: 'timeout /T ' + (_q(a[0]) if a else '1') + ' /NOBREAK',
    "reboot":   lambda a: 'shutdown /r /t 0',
    "shutdown": lambda a: 'shutdown /s /t 0',
    "halt":     lambda a: 'shutdown /s /t 0',
    # -- network -----------------------------------------------------------
    "ifconfig": lambda a: 'ipconfig /all',
    "ip":       lambda a: 'ipconfig /all',
    "traceroute": lambda a: 'tracert ' + _join(a),
    "nslookup": lambda a: 'nslookup ' + _join(a),
    "dig":      lambda a: 'nslookup ' + (_q(a[0]) if a else ''),
    "host":     lambda a: 'nslookup ' + (_q(a[0]) if a else ''),
    "wget":     lambda a: 'curl -L -O ' + (_q(a[-1]) if a else ''),
    "curl":     lambda a: 'curl ' + _join(a),   # Windows 10+ ships curl
    "ssh":      lambda a: 'ssh ' + _join(a),    # Windows 10+ ships OpenSSH
    "scp":      lambda a: 'scp ' + _join(a),
    "netstat":  lambda a: 'netstat ' + _join(a),
    "ss":       lambda a: 'netstat -ano',
    "nmap":     lambda a: 'nmap ' + _join(a),
    "ping":     lambda a: 'ping ' + _join(a),
    # -- text / misc -------------------------------------------------------
    "echo":     lambda a: 'echo ' + ' '.join(s.replace('&','').replace('|','').replace('^','').replace('<','').replace('>','').replace('%','') for s in a),
    "clear":    lambda a: 'cls',
    "pwd":      lambda a: 'cd',
    "xdg-open": lambda a: 'start ' + (_q(a[0]) if a else '.'),
    "open":     lambda a: 'start ' + (_q(a[0]) if a else '.'),   # macOS open
    "xclip":    lambda a: 'clip',
    "xsel":     lambda a: 'clip',
    "strings":  lambda a: 'findstr /p ' + _join(a),
    "base64":   lambda a: f'powershell -Command "[Convert]::ToBase64String([IO.File]::ReadAllBytes({_q(a[0])}))"' if a else '',
    "md5sum":   lambda a: f'powershell -Command "Get-FileHash {_files(a)} -Algorithm MD5 | Format-Table"',
    "sha256sum": lambda a: f'powershell -Command "Get-FileHash {_files(a)} -Algorithm SHA256 | Format-Table"',
    "service":  lambda a: ('sc start ' + _q(a[1]) if len(a)>=2 and a[1]=='start'
                           else 'sc stop ' + _q(a[1]) if len(a)>=2 and a[1]=='stop'
                           else 'sc query ' + (_q(a[0]) if a else '')),
    "systemctl": lambda a: ('sc start ' + _q(a[1]) if len(a)>=2 and a[0]=='start'
                             else 'sc stop ' + _q(a[1]) if len(a)>=2 and a[0]=='stop'
                             else 'sc query ' + (_q(a[1]) if len(a)>=2 else '')),
    "alias":    lambda a: 'doskey ' + ' '.join(a).replace("='", "=").replace("'", " $*") if a else 'doskey /macros',
}

# --- Windows -> Linux/macOS ------------------------------------------------
_WIN_TO_UNIX: dict = {
    # -- file ops ----------------------------------------------------------
    "del":      lambda a: 'rm ' + _join([x for x in a if not x.startswith('/')]),
    "rd":       lambda a: 'rm -rf ' + _join([x for x in a if not x.startswith('/')]),
    "rmdir":    lambda a: 'rmdir ' + _join(a),
    "dir":      lambda a: 'ls -la ' + _join(a),
    "type":     lambda a: 'cat ' + _join(a),
    "xcopy":    lambda a: 'cp -r ' + _join(a),
    "robocopy": lambda a: 'rsync -av ' + _join(a[:2]) if len(a)>=2 else 'rsync -av ' + _join(a),
    "md":       lambda a: 'mkdir -p ' + _join(a),
    "ren":      lambda a: 'mv ' + _join(a),
    "attrib":   lambda a: '',    # no Unix equivalent; skip
    "fc":       lambda a: 'diff ' + _join(a),
    "comp":     lambda a: 'diff ' + _join(a),
    "more":     lambda a: 'less ' + _join(a),
    "tree":     lambda a: ('find ' + (_q(a[0]) if a else '.') + ' | sort'),
    "compact":  lambda a: '',    # NTFS-only; skip
    # -- processes ---------------------------------------------------------
    "tasklist": lambda a: 'ps aux',
    "taskkill": lambda a: ('kill ' + _q(next((a[i+1] for i,x in enumerate(a) if x.upper()=='/PID'), ''))
                           if any(x.upper()=='/PID' for x in a)
                           else 'killall ' + _q(next((a[i+1] for i,x in enumerate(a) if x.upper()=='/IM'), ''))),
    "tskill":   lambda a: 'kill ' + (_q(a[0]) if a else ''),
    "start":    lambda a: 'xdg-open ' + (_q(a[0]) if a else '.'),
    # -- system ------------------------------------------------------------
    "cls":      lambda a: 'clear',
    "ver":      lambda a: 'uname -r',
    "systeminfo": lambda a: 'uname -a && cat /proc/cpuinfo | head -20',
    "set":      lambda a: ('echo "$' + a[0].split('=')[0].replace('$','').replace('"','') + '"' if a and '=' not in a[0] else 'env'),
    "where":    lambda a: 'which ' + _join(a),
    "whoami":   lambda a: 'whoami',
    "hostname":  lambda a: 'hostname',
    "date":     lambda a: 'date',
    "time":     lambda a: 'date +%T',
    "timeout":  lambda a: 'sleep ' + _q(next((a[i+1] for i,x in enumerate(a) if x.upper()=='/T'), a[0] if a else '1')),
    "waitfor":  lambda a: 'sleep 60',
    "runas":    lambda a: 'sudo ' + _join(a),
    "chdir":    lambda a: 'pwd' if not a else 'cd ' + (_q(a[0]) if a else ''),
    "path":     lambda a: 'echo $PATH',
    "help":     lambda a: ('man ' + _q(a[0]) if a else 'help'),
    "assoc":    lambda a: 'xdg-mime query default ' + (_q(a[0]) if a else ''),
    "ipconfig": lambda a: 'ip a',
    "tracert":  lambda a: 'traceroute ' + _join(a),
    "nslookup": lambda a: 'nslookup ' + _join(a),
    "netstat":  lambda a: 'netstat ' + _join(a),
    "ping":     lambda a: 'ping ' + _join(a),
    "nmap":     lambda a: 'nmap ' + _join(a),
    "curl":     lambda a: 'curl ' + _join(a),
    "ssh":      lambda a: 'ssh ' + _join(a),
    "scp":      lambda a: 'scp ' + _join(a),
    "echo":     lambda a: 'echo ' + _join(a),
    "reg":      lambda a: '',    # Windows registry; no Unix equivalent
    "regedit":  lambda a: '',
    "msiexec":  lambda a: '',
    "wmic":     lambda a: '',
    "sc":       lambda a: ('systemctl start ' + _q(a[1]) if len(a)>=2 and a[0]=='start'
                           else 'systemctl stop ' + _q(a[1]) if len(a)>=2 and a[0]=='stop'
                           else 'systemctl status ' + _q(a[1]) if len(a)>=2 and a[0]=='query'
                           else 'systemctl ' + _join(a)),
    "net":      translate_win_net_to_unix,
    "doskey":   lambda a: (
        'alias ' + ' '.join(x for x in a if x not in ('/macros', '/history')).replace(' $*', '').replace('=', "='", 1) + "'"
        if any('=' in x for x in a)
        else ('history' if '/history' in a else 'alias')
    ),
}

# --- Linux-specific -> macOS equivalent (applied on macOS only) -----------
_LINUX_TO_MAC: dict = {
    "xdg-open":  lambda a: 'open ' + (_q(a[0]) if a else '.'),
    "xclip":     lambda a: ('pbcopy' if not any('paste' in x for x in a) else 'pbpaste'),
    "xsel":      lambda a: ('pbpaste' if '--output' in a or '-o' in a else 'pbcopy'),
    "update-alternatives": lambda a: '',
    "apt":       lambda a: 'brew ' + _join(a),
    "apt-get":   lambda a: 'brew ' + _join(a),
    "dpkg":      lambda a: 'brew ' + _join(a),
    "yum":       lambda a: 'brew ' + _join(a),
    "dnf":       lambda a: 'brew ' + _join(a),
    "pacman":    lambda a: 'brew ' + _join(a),
    "systemctl": lambda a: ('brew services start ' + _q(a[1]) if len(a)>=2 and a[0]=='start'
                            else 'brew services stop ' + _q(a[1]) if len(a)>=2 and a[0]=='stop'
                            else 'brew services restart ' + _q(a[1]) if len(a)>=2 and a[0]=='restart'
                            else 'brew services info ' + _q(a[1]) if len(a)>=2 and a[0] in ('status','is-active')
                            else 'brew services list' if a and a[0]=='list' else 'launchctl ' + _join(a)),
    "journalctl": lambda a: ('log stream' if '-f' in a else 'log show --last 1h') + (
                             ' --predicate \'process == "' + _q(next((a[i+1] for i,x in enumerate(a) if x=='-u'), '')).replace("'","") + '"\''),
    "service":   lambda a: ('brew services start ' + _q(a[0]) if len(a)>=2 and a[1]=='start'
                            else 'brew services stop ' + _q(a[0]) if len(a)>=2 and a[1]=='stop'
                            else 'brew services info ' + (_q(a[0]) if a else '')),
    "ss":        lambda a: ('lsof -i :' + _q(next((x for x in a if x.isdigit()),'')).replace("'","") if any(x.isdigit() for x in a) else 'lsof -i -n -P'),
    "ip":        lambda a: 'ifconfig',
    "netstat":   lambda a: 'netstat ' + _join(a),
    "free":      lambda a: 'vm_stat',
    "lscpu":     lambda a: 'sysctl -a | grep machdep.cpu',
    "lsblk":     lambda a: 'diskutil list',
    "lsusb":     lambda a: 'system_profiler SPUSBDataType',
    "lspci":     lambda a: 'system_profiler SPPCIDataType',
    "nproc":     lambda a: 'sysctl -n hw.logicalcpu',
    "uname":     lambda a: 'uname ' + _join(a),   # works on macOS too
    "fuser":     lambda a: 'lsof ' + _join(a),
    "useradd":   lambda a: 'sysadminctl -addUser ' + _q(a[0]) + (' -password ' + _q(a[1]) if len(a)>=2 else ''),
    "adduser":   lambda a: 'sysadminctl -addUser ' + _q(a[0]) + (' -password ' + _q(a[1]) if len(a)>=2 else ''),
    "userdel":   lambda a: 'sysadminctl -deleteUser ' + _q(a[0]),
    "deluser":   lambda a: 'sysadminctl -deleteUser ' + _q(a[0]),
    "usermod":   translate_usermod_to_mac,
    "groupadd":  lambda a: 'dseditgroup -o create ' + _q(a[0]),
    "addgroup":  lambda a: 'dseditgroup -o create ' + _q(a[0]),
    "groupdel":  lambda a: 'dseditgroup -o delete ' + _q(a[0]),
    "delgroup":  lambda a: 'dseditgroup -o delete ' + _q(a[0]),
}

# --- macOS-specific -> Linux equivalent (applied on Linux only) -----------
_MAC_TO_LINUX: dict = {
    "open":      lambda a: 'xdg-open ' + (_q(a[0]) if a else '.'),
    "pbcopy":    lambda a: 'xclip -selection clipboard',
    "pbpaste":   lambda a: 'xclip -selection clipboard -o',
    "say":       lambda a: 'espeak ' + _join(a),
    "sw_vers":   lambda a: 'cat /etc/os-release',
    "launchctl": lambda a: ('systemctl start ' + _q(a[1]) if len(a)>=2 and a[0]=='load'
                            else 'systemctl stop ' + _q(a[1]) if len(a)>=2 and a[0]=='unload'
                            else 'systemctl ' + _join(a)),
    "brew":      lambda a: ('apt-get install ' + _join(a[1:]) if a and a[0]=='install'
                            else 'apt-get remove ' + _join(a[1:]) if a and a[0]=='remove'
                            else 'apt-get update && apt-get upgrade' if a and a[0]=='upgrade'
                            else 'apt-get ' + _join(a)),
    "diskutil":  lambda a: 'lsblk',
    "networksetup": lambda a: 'nmcli ' + _join(a),
    "caffeinate": lambda a: '',
    "osascript": lambda a: '',
    "defaults":  lambda a: '',
    "plutil":    lambda a: '',
    "dscacheutil": lambda a: '',
    "airport":   lambda a: 'iwconfig',
    "sysadminctl": lambda a: (
        f"userdel -r {_q(a[1])}" if len(a) >= 2 and a[0] == "-deleteUser"
        else f"useradd -m {_q(a[1])}" + (f" && echo '{a[1]}:{a[3]}' | chpasswd" if len(a) >= 4 and a[2] == "-password" else "")
        if len(a) >= 2 and a[0] == "-addUser"
        else "sysadminctl " + _join(a)
    ),
    "dseditgroup": lambda a: (
        f"groupadd {_q(a[2])}" if len(a) >= 3 and a[0] == "-o" and a[1] == "create"
        else f"groupdel {_q(a[2])}" if len(a) >= 3 and a[0] == "-o" and a[1] == "delete"
        else f"usermod -aG {_q(a[6])} {_q(a[3])}" if len(a) >= 7 and a[0] == "-o" and a[1] == "edit" and a[2] == "-a" and a[5] == "user"
        else f"gpasswd -d {_q(a[3])} {_q(a[6])}" if len(a) >= 7 and a[0] == "-o" and a[1] == "edit" and a[2] == "-d" and a[5] == "user"
        else "dseditgroup " + _join(a)
    ),
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
