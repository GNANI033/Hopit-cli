import os
import sys
import json
from rich.table import Table

CONFIG_PATH = os.path.expanduser("~/.hopit-config.json")

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_config(config):
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=4)
        return True
    except Exception:
        return False

def main():
    from rich.console import Console
    console = Console()
    config = load_config()

    if len(sys.argv) < 2:
        table = Table(title="hopit-cli Configuration", show_lines=True)
        table.add_column("Setting", style="bold cyan")
        table.add_column("Value", style="green")
        table.add_column("Status", style="yellow")

        from hopit.config import detect_editor, detect_package_manager
        
        custom_editor = config.get("editor")
        custom_pkg = config.get("package_manager")

        table.add_row(
            "editor",
            custom_editor or "Not set",
            f"Active: {detect_editor()}" + (" (override)" if custom_editor else " (auto-detected)")
        )
        table.add_row(
            "package_manager",
            custom_pkg or "Not set",
            f"Active: {detect_package_manager()}" + (" (override)" if custom_pkg else " (auto-detected)")
        )

        console.print(table)
        console.print(f"[dim]Config file: {CONFIG_PATH}[/dim]")
        console.print("[dim]Use 'config set <setting> <value>' to modify, or 'config reset' to clear custom settings.[/dim]")
        return

    action = sys.argv[1].lower()

    if action == "set":
        if len(sys.argv) < 4:
            console.print("[red]Usage: config set <setting> <value>[/red]")
            sys.exit(1)
        setting = sys.argv[2].lower()
        value = sys.argv[3]

        if setting not in ("editor", "package_manager"):
            console.print(f"[red]Unknown setting: {setting}. Valid settings: editor, package_manager[/red]")
            sys.exit(1)

        config[setting] = value
        if save_config(config):
            console.print(f"[bold green]Successfully set '{setting}' to '{value}'.[/bold green]")
        else:
            console.print("[bold red]Failed to save configuration.[/bold red]")

    elif action == "reset":
        if os.path.exists(CONFIG_PATH):
            try:
                os.remove(CONFIG_PATH)
                console.print("[bold green]Configuration reset to defaults successfully.[/bold green]")
            except Exception as e:
                console.print(f"[bold red]Failed to delete config file:[/bold red] {e}")
        else:
            console.print("[yellow]No custom configuration file found.[/yellow]")
    else:
        console.print(f"[red]Unknown action: {action}. Valid actions: set, reset[/red]")

if __name__ == "__main__":
    main()
