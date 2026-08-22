import os
import sys
from rich.console import Console
from rich.syntax import Syntax

def main():
    console = Console()
    args = sys.argv[1:]
    if not args:
        console.print("[yellow]Usage: head [-n lines] <file_path>[/yellow]")
        sys.exit(1)
        
    lines_to_show = 10
    file_paths = []
    
    # Parse args
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "-n" and i + 1 < len(args):
            try:
                lines_to_show = int(args[i+1])
                i += 2
                continue
            except ValueError:
                pass
        elif arg.startswith("-n") and arg[2:].isdigit():
            lines_to_show = int(arg[2:])
            i += 1
            continue
        elif arg.lstrip("-").isdigit():
            lines_to_show = int(arg.lstrip("-"))
            i += 1
            continue
        
        file_paths.append(arg)
        i += 1

    if not file_paths:
        console.print("[yellow]Usage: head [-n lines] <file_path>[/yellow]")
        sys.exit(1)
        
    for path in file_paths:
        path = os.path.expanduser(path)
        if not os.path.isfile(path):
            console.print(f"[red]Error: '{path}' is not a file.[/red]")
            continue
            
        try:
            lines = []
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for _ in range(lines_to_show):
                    line = f.readline()
                    if not line:
                        break
                    lines.append(line)
                    
            content = "".join(lines)
            _, ext = os.path.splitext(path)
            lexer = ext.lstrip(".") if ext else "text"
            
            if len(file_paths) > 1:
                console.print(f"\n[bold magenta]=== {path} ===[/bold magenta]")
                
            try:
                syntax = Syntax(content, lexer, theme="monokai", line_numbers=True)
                console.print(syntax)
            except Exception:
                console.print(content)
        except Exception as e:
            console.print(f"[red]Error reading '{path}': {e}[/red]")

if __name__ == "__main__":
    main()
