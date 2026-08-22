import os
import sys
from hopit.config import console
from rich.tree import Tree
from rich.filesize import decimal

def make_tree(directory_path, tree_node, current_depth=0, max_depth=3):
    if current_depth >= max_depth:
        return
    try:
        entries = sorted(os.scandir(directory_path), key=lambda e: (not e.is_dir(), e.name.lower()))
    except Exception:
        return
        
    for entry in entries:
        if entry.name.startswith('.') or entry.name in ('__pycache__', 'node_modules', 'venv', '.venv', '.git'):
            continue
        if entry.is_dir():
            style = "bold blue"
            branch = tree_node.add(f"📁 [bold blue]{entry.name}[/bold blue]", style=style)
            make_tree(entry.path, branch, current_depth + 1, max_depth)
        else:
            style = "green"
            size = ""
            try:
                stat = entry.stat()
                size = f" ({decimal(stat.st_size)})"
            except Exception:
                pass
            tree_node.add(f"📄 {entry.name}{size}", style=style)

def main():
    target_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    target_dir = os.path.abspath(os.path.expanduser(target_dir))
    
    if not os.path.isdir(target_dir):
        console.print(f"[red]Error: '{target_dir}' is not a directory.[/red]")
        sys.exit(1)
        
    max_depth = 3
    if len(sys.argv) > 2:
        try:
            max_depth = int(sys.argv[2])
        except ValueError:
            pass
    elif len(sys.argv) == 2:
        try:
            max_depth = int(sys.argv[1])
            target_dir = "."
        except ValueError:
            pass
            
    tree = Tree(f"📂 [bold yellow]{os.path.basename(target_dir) or target_dir}[/bold yellow]")
    make_tree(target_dir, tree, 0, max_depth)
    console.print(tree)

if __name__ == "__main__":
    main()
