import os
import sys
from hopit.config import console

def main():
    if len(sys.argv) < 2:
        console.print("[yellow]Usage: touch <file_path>[/yellow]")
        sys.exit(1)
        
    path = os.path.expanduser(sys.argv[1])
    try:
        if os.path.exists(path):
            os.utime(path, None)
            console.print(f"[green]Updated timestamps for:[/green] {path}")
        else:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, 'a'):
                os.utime(path, None)
            console.print(f"[green]Created empty file:[/green] {path}")
    except Exception as e:
        console.print(f"[red]Error touching file '{path}': {e}[/red]")
        sys.exit(1)

if __name__ == "__main__":
    main()
