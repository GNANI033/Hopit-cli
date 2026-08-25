import os
import sys
import re
from hopit.config import console

def main():
    args = sys.argv[1:]
    if len(args) < 1:
        console.print("[yellow]Usage: grep <pattern> [file_or_directory][/yellow]")
        sys.exit(1)
        
    pattern_str = args[0]
    target = args[1] if len(args) > 1 else "."
    target = os.path.abspath(os.path.expanduser(target))
    
    try:
        pattern = re.compile(pattern_str, re.IGNORECASE)
    except re.error as e:
        console.print(f"[red]Error: Invalid regular expression '{pattern_str}': {e}[/red]")
        sys.exit(1)
        
    console.print(f"Searching for pattern '[bold yellow]{pattern_str}[/bold yellow]' in '[bold cyan]{target}[/bold cyan]'...\n")
    
    matches = 0
    
    def search_in_file(file_path, display_name):
        nonlocal matches
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    if pattern.search(line):
                        snippet = line.strip()
                        if len(snippet) > 150:
                            snippet = snippet[:147] + "..."
                        snippet_esc = snippet.replace("[", "\\[").replace("]", "\\]")
                        highlighted = pattern.sub(lambda m: f"[bold red]{m.group(0)}[/bold red]", snippet_esc)
                        console.print(f"[bold magenta]{display_name}[/bold magenta]:[yellow]{line_no}[/yellow]: {highlighted}")
                        matches += 1
        except Exception:
            pass

    if os.path.isfile(target):
        search_in_file(target, os.path.basename(target))
    elif os.path.isdir(target):
        for root, dirs, files in os.walk(target):
            dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', 'node_modules', 'venv', '.venv')]
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, target)
                search_in_file(full_path, rel_path)
    else:
        console.print(f"[red]Error: target '{target}' does not exist.[/red]")
        sys.exit(1)
        
    if matches == 0:
        console.print("No matching text found.")
    else:
        console.print(f"\n[bold green]Found {matches} match(es).[/bold green]")

if __name__ == "__main__":
    main()
