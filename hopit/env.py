import os
import sys
from hopit.config import console
from rich.table import Table

from hopit.config import safe_entrypoint

@safe_entrypoint
def main():
    query = sys.argv[1].lower() if len(sys.argv) > 1 else None
    
    table = Table(title="Environment Variables", show_header=True, header_style="bold magenta")
    table.add_column("Variable", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")

    for key in sorted(os.environ.keys()):
        val = os.environ[key]
        if query and query not in key.lower() and query not in val.lower():
            continue
        table.add_row(key, val)

    console.print(table)

if __name__ == "__main__":
    main()
