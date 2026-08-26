import os
import sys
from hopit.config import console

from hopit.config import safe_entrypoint

@safe_entrypoint
def main():
    args = sys.argv[1:]
    flags = set()
    paths = []
    
    for arg in args:
        if arg.startswith('-') and arg != '-':
            if arg.startswith('--'):
                if arg == '--parents':
                    flags.add('p')
            else:
                for char in arg[1:]:
                    flags.add(char)
        else:
            paths.append(arg)
            
    if not paths:
        console.print("[yellow]Usage: mkdir [-p] <directory>...[/yellow]")
        sys.exit(1)
        
    parents = 'p' in flags
    
    for path in paths:
        path = os.path.expanduser(path)
        if parents:
            try:
                os.makedirs(path, exist_ok=True)
            except Exception as e:
                console.print(f"[red]mkdir: cannot create directory '{path}': {e}[/red]")
        else:
            # Check parent directory
            parent = os.path.dirname(path)
            if parent and not os.path.exists(parent):
                console.print(f"[red]mkdir: cannot create directory '{path}': No such file or directory[/red]")
                continue
            if os.path.exists(path):
                console.print(f"[red]mkdir: cannot create directory '{path}': File exists[/red]")
                continue
            try:
                os.mkdir(path)
            except Exception as e:
                console.print(f"[red]mkdir: cannot create directory '{path}': {e}[/red]")

if __name__ == "__main__":
    main()
