import os
import sys
from hopit.config import console
from rich.syntax import Syntax

from hopit.config import safe_entrypoint

@safe_entrypoint
def main():
    if len(sys.argv) < 2:
        console.print("[yellow]Usage: less <file_path>[/yellow]")
        sys.exit(1)
        
    path = os.path.expanduser(sys.argv[1])
    if not os.path.isfile(path):
        console.print(f"[red]Error: '{path}' is not a file.[/red]")
        sys.exit(1)
        
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            
        _, ext = os.path.splitext(path)
        lexer = ext.lstrip(".") if ext else "text"
        
        try:
            from hopit.config import get_syntax_theme
            syntax = Syntax(content, lexer, theme=get_syntax_theme(), line_numbers=True)
            with console.pager():
                console.print(syntax)
        except Exception:
            with console.pager():
                console.print(content)
    except Exception as e:
        console.print(f"[red]Error paging file '{path}': {e}[/red]")
        sys.exit(1)

if __name__ == "__main__":
    main()
