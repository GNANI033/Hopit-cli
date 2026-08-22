import os
import sys
from hopit.config import console
from rich.syntax import Syntax

def main():
    if len(sys.argv) < 2:
        console.print("[yellow]Usage: cat <file_path>[/yellow]")
        sys.exit(1)
        
    for arg in sys.argv[1:]:
        path = os.path.expanduser(arg)
        if not os.path.isfile(path):
            console.print(f"[red]Error: '{path}' is not a file.[/red]")
            continue
            
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            
            _, ext = os.path.splitext(path)
            lexer = ext.lstrip(".") if ext else "text"
            
            try:
                from hopit.config import get_syntax_theme
                syntax = Syntax(content, lexer, theme=get_syntax_theme(), line_numbers=True)
                console.print(syntax)
            except Exception:
                console.print(content)
        except Exception as e:
            console.print(f"[red]Error reading '{path}': {e}[/red]")

if __name__ == "__main__":
    main()
