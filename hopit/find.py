import os
import sys
import fnmatch
from rich.console import Console

def main():
    console = Console()
    args = sys.argv[1:]
    
    if not args:
        console.print("[yellow]Usage: find <name_pattern> [search_directory][/yellow]")
        sys.exit(1)
        
    pattern = args[0]
    search_dir = args[1] if len(args) > 1 else "."
    search_dir = os.path.abspath(os.path.expanduser(search_dir))
    
    if not os.path.isdir(search_dir):
        console.print(f"[red]Error: '{search_dir}' is not a directory.[/red]")
        sys.exit(1)
        
    console.print(f"Searching for files matching '[bold yellow]{pattern}[/bold yellow]' in '[bold cyan]{search_dir}[/bold cyan]'...\n")
    
    matches = 0
    for root, dirs, files in os.walk(search_dir):
        dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', 'node_modules', 'venv', '.venv')]
        
        for name in dirs + files:
            if fnmatch.fnmatch(name.lower(), pattern.lower()) or pattern.lower() in name.lower():
                full_path = os.path.join(root, name)
                rel_path = os.path.relpath(full_path, search_dir)
                if os.path.isdir(full_path):
                    console.print(f"📁 [bold blue]{rel_path}[/bold blue]")
                else:
                    console.print(f"📄 [green]{rel_path}[/green]")
                matches += 1
                
    if matches == 0:
        console.print("No files found matching the criteria.")
    else:
        console.print(f"\n[bold green]Found {matches} match(es).[/bold green]")

if __name__ == "__main__":
    main()
