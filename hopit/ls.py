import os
import sys
import datetime
import shutil
from hopit.config import console
from rich.markup import escape

def get_owner_group(path):
    if os.name != 'nt':
        import pwd, grp
        try:
            stat_info = os.stat(path)
            owner = pwd.getpwuid(stat_info.st_uid).pw_name
            group = grp.getgrgid(stat_info.st_gid).gr_name
            return owner, group
        except Exception:
            return "owner", "group"
    else:
        # On Windows
        try:
            import win32security
            sd = win32security.GetFileSecurity(path, win32security.OWNER_SECURITY_INFORMATION)
            owner_sid = sd.GetSecurityDescriptorOwner()
            name, domain, type = win32security.LookupAccountSid(None, owner_sid)
            return name, domain
        except Exception:
            import getpass
            user = getpass.getuser()
            return user, "None"

def get_unix_permissions(path):
    is_dir = os.path.isdir(path)
    try:
        stat_info = os.stat(path)
        mode = stat_info.st_mode
    except Exception:
        return "d---------" if is_dir else "----------"
        
    if os.name != 'nt':
        import stat
        return stat.filemode(mode)
        
    p = ['d' if is_dir else '-']
    r = 'r' if os.access(path, os.R_OK) else '-'
    w = 'w' if os.access(path, os.W_OK) else '-'
    
    _, ext = os.path.splitext(path.lower())
    is_exec = ext in ('.exe', '.bat', '.cmd', '.ps1', '.lnk') or is_dir
    x = 'x' if is_exec else '-'
    
    p.append(r)
    p.append(w)
    p.append(x)
    p.append(r)
    p.append('-')
    p.append(x if is_exec else '-')
    p.append(r)
    p.append('-')
    p.append(x if is_exec else '-')
    
    return "".join(p)

def is_hidden_file(path):
    name = os.path.basename(path)
    if name.startswith('.'):
        return True
    if os.name == 'nt':
        import ctypes
        try:
            attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
            if attrs != -1:
                return bool(attrs & (2 | 4))
        except Exception:
            pass
    return False

def format_size(size, human=False):
    if not human:
        return str(size)
    for unit in ['', 'K', 'M', 'G', 'T', 'P']:
        if size < 1024:
            if unit:
                return f"{size:.1f}{unit}".replace(".0", "")
            return f"{size}"
        size /= 1024.0
    return f"{size:.1f}E"

def format_time(mtime):
    try:
        mtime_dt = datetime.datetime.fromtimestamp(mtime)
        now = datetime.datetime.now()
        diff = now - mtime_dt
        if diff.days < 180 and diff.days >= -1:
            return mtime_dt.strftime("%b %d %H:%M")
        else:
            return mtime_dt.strftime("%b %d  %Y")
    except Exception:
        return "Jan 01  1970"

def get_styled_name(path, name, show_classify=False):
    is_dir = os.path.isdir(path)
    _, ext = os.path.splitext(name.lower())
    is_exec = ext in ('.exe', '.bat', '.cmd', '.ps1', '.lnk') and not is_dir
    
    escaped_name = escape(name)
    suffix = ""
    if show_classify:
        if is_dir:
            suffix = "/"
        elif is_exec:
            suffix = "*"
            
    if is_dir:
        return f"[bold blue]{escaped_name}{suffix}[/bold blue]"
    elif is_exec:
        return f"[bold green]{escaped_name}{suffix}[/bold green]"
    else:
        return f"{escaped_name}{suffix}"

def list_directory(target_path, flags):
    show_all = 'a' in flags
    show_almost_all = 'A' in flags
    long_format = 'l' in flags
    human_sizes = 'h' in flags
    sort_time = 't' in flags
    sort_size = 'S' in flags
    reverse_sort = 'r' in flags
    classify = 'F' in flags
    single_column = '1' in flags
    recursive = 'R' in flags

    if not os.path.exists(target_path):
        console.print(f"[red]ls: cannot access '{target_path}': No such file or directory[/red]")
        return False

    if not os.path.isdir(target_path):
        # Just list the single file
        files = [os.path.basename(target_path)]
        parent_dir = os.path.dirname(target_path) or "."
    else:
        parent_dir = target_path
        try:
            files = os.listdir(parent_dir)
        except Exception as e:
            console.print(f"[red]ls: cannot open directory '{target_path}': {e}[/red]")
            return False

    # Filter hidden files
    filtered = []
    
    # Add "." and ".." if -a
    if show_all and os.path.isdir(target_path):
        filtered.append((".", os.path.join(parent_dir, ".")))
        filtered.append(("..", os.path.join(parent_dir, "..")))
        
    for f in files:
        full = os.path.join(parent_dir, f)
        hidden = is_hidden_file(full)
        if hidden and not (show_all or show_almost_all):
            continue
        filtered.append((f, full))

    # Gather stats for sorting
    stats = []
    for f, full in filtered:
        try:
            stat_info = os.stat(full)
            mtime = stat_info.st_mtime
            size = stat_info.st_size
            nlink = stat_info.st_nlink
        except Exception:
            mtime = 0
            size = 0
            nlink = 1
        stats.append((f, full, mtime, size, nlink))

    # Sorting
    if sort_time:
        stats.sort(key=lambda x: x[2], reverse=True)
    elif sort_size:
        stats.sort(key=lambda x: x[3], reverse=True)
    else:
        # Default alphabetical sorting
        stats.sort(key=lambda x: x[0].lower())

    if reverse_sort:
        stats.reverse()

    if long_format:
        total_blocks = sum((s[3] // 1024 + (1 if s[3] % 1024 else 0)) for s in stats if os.path.isfile(s[1]))
        if os.path.isdir(target_path):
            console.print(f"total {total_blocks}")
            
        rows = []
        max_nlink = 1
        max_owner = 1
        max_group = 1
        max_size = 1
        
        # Resolve owner and group first and find max widths
        resolved_stats = []
        for f, full, mtime, size, nlink in stats:
            perms = get_unix_permissions(full)
            owner, group = get_owner_group(full)
            sz_str = format_size(size, human_sizes)
            max_nlink = max(max_nlink, len(str(nlink)))
            max_owner = max(max_owner, len(owner))
            max_group = max(max_group, len(group))
            max_size = max(max_size, len(sz_str))
            resolved_stats.append((perms, nlink, owner, group, sz_str, mtime, f, full))
            
        for perms, nlink, owner, group, sz_str, mtime, f, full in resolved_stats:
            time_str = format_time(mtime)
            styled_name = get_styled_name(full, f, classify)
            console.print(f"{perms} {nlink:>{max_nlink}} {owner:<{max_owner}} {group:<{max_group}} {sz_str:>{max_size}} {time_str} {styled_name}")
    else:
        if single_column:
            for f, full, _, _, _ in stats:
                console.print(get_styled_name(full, f, classify))
        else:
            # Column layout
            rendered = [get_styled_name(full, f, classify) for f, full, _, _, _ in stats]
            if rendered:
                from rich.columns import Columns
                console.print(Columns(rendered, equal=True, expand=False))

    if recursive and os.path.isdir(target_path):
        for f, full, _, _, _ in stats:
            if f in (".", ".."):
                continue
            if os.path.isdir(full):
                console.print(f"\n{full}:")
                list_directory(full, flags)
                
    return True

def main():
    args = sys.argv[1:]
    flags = set()
    paths = []
    
    for arg in args:
        if arg.startswith('-') and arg != '-':
            for char in arg[1:]:
                flags.add(char)
        else:
            paths.append(arg)
            
    if not paths:
        paths = ["."]
        
    for i, path in enumerate(paths):
        if len(paths) > 1:
            console.print(f"{path}:")
        list_directory(os.path.expanduser(path), flags)
        if i < len(paths) - 1:
            console.print()

if __name__ == "__main__":
    main()
