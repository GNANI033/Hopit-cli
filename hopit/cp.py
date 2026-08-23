import os
import sys
import shutil
from hopit.config import console

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
            
    if len(paths) < 2:
        console.print("[yellow]Usage: cp [-r] [-f] <source>... <destination>[/yellow]")
        sys.exit(1)
        
    recursive = 'r' in flags or 'R' in flags
    force = 'f' in flags
    
    sources = paths[:-1]
    dest = os.path.expanduser(paths[-1])
    
    dest_is_dir = os.path.isdir(dest)
    
    if len(sources) > 1 and not dest_is_dir:
        console.print(f"[red]cp: target '{dest}' is not a directory[/red]")
        sys.exit(1)
        
    for src in sources:
        src = os.path.expanduser(src)
        if not os.path.exists(src):
            console.print(f"[red]cp: cannot stat '{src}': No such file or directory[/red]")
            continue
            
        # Determine the target path
        if dest_is_dir:
            target = os.path.join(dest, os.path.basename(src))
        else:
            target = dest
            
        if os.path.isdir(src):
            if not recursive:
                console.print(f"[red]cp: -r not specified; omitting directory '{src}'[/red]")
                continue
            try:
                if os.path.exists(target):
                    if force:
                        shutil.rmtree(target)
                    else:
                        console.print(f"[red]cp: destination '{target}' already exists. Use -f to overwrite.[/red]")
                        continue
                shutil.copytree(src, target)
            except Exception as e:
                console.print(f"[red]cp: error copying directory '{src}': {e}[/red]")
        else:
            try:
                if os.path.exists(target) and not force and not dest_is_dir:
                    # if it's the exact same file
                    if os.path.abspath(src) == os.path.abspath(target):
                        console.print(f"[red]cp: '{src}' and '{target}' are the same file[/red]")
                        continue
                shutil.copy2(src, target)
            except Exception as e:
                console.print(f"[red]cp: error copying file '{src}': {e}[/red]")

if __name__ == "__main__":
    main()
