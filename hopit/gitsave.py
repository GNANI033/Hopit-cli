import sys
import subprocess
from hopit.config import console

def main():
    if len(sys.argv) < 2:
        console.print("[bold red]Error:[/bold red] Please provide a commit message. Example: [yellow]gitsave fixed layout bug[/yellow]")
        sys.exit(1)

    # Combine all trailing arguments into a single commit message
    commit_message = " ".join(sys.argv[1:])

    console.print("[bold cyan]Step 1/3: Staging all changes...[/bold cyan]")
    res = subprocess.run(["git", "add", "."], capture_output=True, text=True, errors="ignore")
    if res.returncode != 0:
        console.print(f"[bold red]Failed to stage changes:[/bold red] {res.stderr.strip() or res.stdout.strip()}")
        sys.exit(res.returncode)

    console.print(f"[bold cyan]Step 2/3: Committing with message: '{commit_message}'...[/bold cyan]")
    res = subprocess.run(["git", "commit", "-m", commit_message], capture_output=True, text=True, errors="ignore")
    if res.returncode != 0:
        # If there are no changes to commit, print success/notice and proceed or exit
        output = res.stderr.strip() or res.stdout.strip()
        if "nothing to commit" in output.lower() or "no changes added to commit" in output.lower():
            console.print("[yellow]Nothing to commit, working tree clean. Proceeding to push anyway...[/yellow]")
        else:
            console.print(f"[bold red]Failed to commit:[/bold red] {output}")
            sys.exit(res.returncode)
    else:
        if res.stdout:
            console.print(res.stdout.strip())

    console.print("[bold cyan]Step 3/3: Pushing to remote...[/bold cyan]")
    res = subprocess.run(["git", "push"], capture_output=True, text=True, errors="ignore")
    if res.returncode != 0:
        console.print(f"[bold red]Failed to push changes:[/bold red] {res.stderr.strip() or res.stdout.strip()}")
        sys.exit(res.returncode)
    if res.stdout:
        console.print(res.stdout.strip())
    if res.stderr:
        # Sometimes git push prints progress/fetch info to stderr, which is normal.
        # Only treat it as error if exit code was non-zero. Since it is zero, we just print it.
        console.print(res.stderr.strip())

    console.print("[bold green]Successfully staged, committed, and pushed all changes![/bold green]")

if __name__ == "__main__":
    main()
