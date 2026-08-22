import sys
import shutil
from hopit.config import console

def main():
    if len(sys.argv) < 2:
        console.print("[yellow]Usage: which <command_name>[/yellow]")
        sys.exit(1)
    
    cmd = sys.argv[1]
    path = shutil.which(cmd)
    if path:
        console.print(f"[green]Found command at:[/green] {path}")
    else:
        console.print(f"[red]Command '{cmd}' not found on PATH.[/red]")
        sys.exit(1)

if __name__ == "__main__":
    main()
