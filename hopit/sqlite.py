import sys
import sqlite3
from hopit.config import console
from rich.table import Table

from hopit.config import safe_entrypoint

@safe_entrypoint
def main():
    if len(sys.argv) < 2:
        print("Usage: sqlite <database_file> [SQL query]")
        print("  If no query is provided, lists all tables.")
        sys.exit(1)

    db_path = sys.argv[1]
    query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else None

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        if not query:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = cursor.fetchall()
            
            if not tables:
                console.print(f"[yellow]No tables found in '{db_path}'.[/yellow]")
                return

            table = Table(title=f"Tables in '{db_path}'", show_lines=True)
            table.add_column("Table Name", style="bold cyan")
            table.add_column("Row Count", justify="right")
            
            for t_name in tables:
                name = t_name[0]
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM `{name}`")
                    count = cursor.fetchone()[0]
                except Exception:
                    count = "N/A"
                table.add_row(name, str(count))
            
            console.print(table)
        else:
            cursor.execute(query)
            if query.strip().lower().startswith("select") or "returning" in query.strip().lower():
                rows = cursor.fetchall()
                if not rows:
                    console.print("[yellow]Query returned 0 rows.[/yellow]")
                    return
                
                cols = [desc[0] for desc in cursor.description]
                
                table = Table(title="Query Results", show_lines=True)
                for col in cols:
                    table.add_column(col, style="bold cyan")
                
                for row in rows:
                    table.add_row(*(str(val) if val is not None else "NULL" for val in row))
                
                console.print(table)
            else:
                conn.commit()
                console.print(f"[bold green]Query executed successfully. Rows affected: {cursor.rowcount}[/bold green]")
        
        conn.close()
    except Exception as e:
        console.print(f"[bold red]SQLite Error:[/bold red] {e}")

if __name__ == "__main__":
    main()
