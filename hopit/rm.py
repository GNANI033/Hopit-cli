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
            
    if not paths:
        console.print("[yellow]Usage: rm [-r] [-f] <path>...[/yellow]")
        sys.exit(1)
        
    recursive = 'r' in flags or 'R' in flags
    force = 'f' in flags
    
    for path in paths:
        path = os.path.expanduser(path)
        if not os.path.exists(path) and not os.path.islink(path):
            if not force:
                console.print(f"[red]rm: cannot remove '{path}': No such file or directory[/red]")
            continue
            
        if os.path.isdir(path) and not os.path.islink(path):
            if not recursive:
                console.print(f"[red]rm: cannot remove '{path}': Is a directory[/red]")
                continue
            try:
                shutil.rmtree(path)
            except Exception as e:
                console.print(f"[red]rm: cannot remove '{path}': {e}[/red]")
        else:
            try:
                os.remove(path)
            except Exception as e:
                try:
                    # try to unlink if it's a broken symlink or special file
                    os.unlink(path)
                except Exception:
                    console.print(f"[red]rm: cannot remove '{path}': {e}[/red]")

if __name__ == "__main__":
    main()
