import sys
import subprocess
from rich.console import Console

def run_git(args):
    try:
        res = subprocess.run(["git"] + args, capture_output=True, text=True)
        return res.returncode, res.stdout, res.stderr
    except FileNotFoundError:
        return -1, "", "git command not found on this system."

def main():
    if len(sys.argv) < 2:
        print("Usage: git <subcommand> [args...]")
        print("  Supported subcommands: status, log, branch, diff, etc.")
        sys.exit(1)

    subcmd = sys.argv[1].lower()
    args = sys.argv[1:]

    if subcmd == "commit":
        has_message_flag = any(flag in args for flag in ("-m", "--message", "-F", "--file", "-c", "--reedit-message", "-C", "--reuse-message"))
        if not has_message_flag and len(args) > 1:
            message = " ".join(args[1:])
            args = ["commit", "-m", message]

    code, stdout, stderr = run_git(args)
    console = Console()

    if code != 0:
        if stderr:
            console.print(f"[bold red]Git Error:[/bold red] {stderr.strip()}")
        if stdout:
            console.print(stdout.strip())
        if not stderr and not stdout:
            console.print(f"[bold red]Git command failed with exit code {code}[/bold red]")
        sys.exit(code)

    if not stdout.strip():
        if subcmd in ("add", "commit", "push", "pull", "checkout", "clone"):
            console.print("[bold green]Success![/bold green]")
        else:
            console.print("[dim](no output)[/dim]")
        return

    if subcmd == "status":
        lines = stdout.splitlines()
        for line in lines:
            if "modified:" in line:
                console.print(line.replace("modified:", "[bold yellow]modified:[/bold yellow]"))
            elif "new file:" in line:
                console.print(line.replace("new file:", "[bold green]new file:[/bold green]"))
            elif "deleted:" in line:
                console.print(line.replace("deleted:", "[bold red]deleted:[/bold red]"))
            elif "renamed:" in line:
                console.print(line.replace("renamed:", "[bold cyan]renamed:[/bold cyan]"))
            elif line.startswith("\t"):
                # Check context to style green for staged and red for untracked/unstaged
                staged = False
                for prev in lines[:lines.index(line)]:
                    if "Changes to be committed:" in prev:
                        staged = True
                    elif "Changes not staged for commit:" in prev or "Untracked files:" in prev:
                        staged = False
                if staged:
                    console.print(f"\t[green]{line.strip()}[/green]")
                else:
                    console.print(f"\t[red]{line.strip()}[/red]")
            elif "branch" in line.lower() or "up to date" in line.lower():
                console.print(f"[cyan]{line}[/cyan]")
            else:
                console.print(line)

    elif subcmd == "log":
        lines = stdout.splitlines()
        for line in lines:
            if line.startswith("commit "):
                console.print(f"[yellow]{line}[/yellow]")
            elif line.startswith("Author:"):
                console.print(f"[cyan]{line}[/cyan]")
            elif line.startswith("Date:"):
                console.print(f"[green]{line}[/green]")
            elif line.startswith("    "):
                console.print(f"  [bold white]{line.strip()}[/bold white]")
            else:
                console.print(line)

    elif subcmd == "branch":
        lines = stdout.splitlines()
        for line in lines:
            if line.startswith("*"):
                console.print(f"[bold green]{line}[/bold green]")
            else:
                console.print(f"[dim]{line}[/dim]")

    elif subcmd == "diff":
        lines = stdout.splitlines()
        for line in lines:
            if line.startswith("+") and not line.startswith("+++"):
                console.print(f"[green]{line}[/green]")
            elif line.startswith("-") and not line.startswith("---"):
                console.print(f"[red]{line}[/red]")
            elif line.startswith("@@"):
                console.print(f"[cyan]{line}[/cyan]")
            elif line.startswith("diff --git"):
                console.print(f"[bold white]{line}[/bold white]")
            else:
                console.print(line)
    else:
        print(stdout)

if __name__ == "__main__":
    main()
