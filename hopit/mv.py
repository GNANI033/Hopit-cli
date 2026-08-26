import os
import sys
import shutil
from hopit.config import console

from hopit.config import safe_entrypoint

@safe_entrypoint
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
        console.print("[yellow]Usage: mv [-f] <source>... <destination>[/yellow]")
        sys.exit(1)
        
    force = 'f' in flags
    
    sources = paths[:-1]
    dest = os.path.expanduser(paths[-1])
    
    dest_is_dir = os.path.isdir(dest)
    
    if len(sources) > 1 and not dest_is_dir:
        console.print(f"[red]mv: target '{dest}' is not a directory[/red]")
        sys.exit(1)
        
    for src in sources:
        src = os.path.expanduser(src)
        if not os.path.exists(src):
            console.print(f"[red]mv: cannot stat '{src}': No such file or directory[/red]")
            continue
            
        if dest_is_dir:
            target = os.path.join(dest, os.path.basename(src))
        else:
            target = dest
            
        if os.path.abspath(src) == os.path.abspath(target):
            console.print(f"[red]mv: '{src}' and '{target}' are the same file[/red]")
            continue
            
        try:
            if os.path.exists(target) and not force:
                # Ask or error
                console.print(f"[red]mv: destination '{target}' already exists. Use -f to overwrite.[/red]")
                continue
            if os.path.exists(target) and force:
                if os.path.isdir(target):
                    shutil.rmtree(target)
                else:
                    os.remove(target)
            shutil.move(src, target)
        except Exception as e:
            console.print(f"[red]mv: error moving '{src}' to '{target}': {e}[/red]")

if __name__ == "__main__":
    main()
