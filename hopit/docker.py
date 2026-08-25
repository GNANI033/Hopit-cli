"""
hopit/docker.py
────────────────────────────────────────────────────────────────────────────
Docker & Docker Compose support for hopit-cli.

Provides:
  1. Universal command parser (similar to k8s_cmd in kubernetes.py).
  2. Formatting functions to render docker outputs into beautiful Rich tables.
  3. Dynamic loaders for docker autocompletions (containers, images, compose services).
"""

import os
import sys
import shlex
import shutil
import subprocess
import json
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markup import escape
from hopit.config import console, IS_WINDOWS

# ─────────────────────────────────────────────────────────────────────────────
# CLI Availability Helpers
# ─────────────────────────────────────────────────────────────────────────────

_needs_sudo_cache = None

def docker_available() -> bool:
    return shutil.which("docker") is not None

def docker_needs_sudo() -> bool:
    global _needs_sudo_cache
    if _needs_sudo_cache is not None:
        return _needs_sudo_cache
    if not docker_available():
        _needs_sudo_cache = False
        return False
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, text=True, errors="ignore", timeout=2)
        if r.returncode == 0:
            _needs_sudo_cache = False
            return False
        err = (r.stderr or "") + (r.stdout or "")
        if "permission denied" in err.lower():
            _needs_sudo_cache = True
            return True
    except Exception:
        pass
    _needs_sudo_cache = False
    return False

def daemon_running() -> bool:
    if not docker_available():
        return False
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, text=True, errors="ignore", timeout=3)
        if r.returncode == 0:
            return True
        err = (r.stderr or "") + (r.stdout or "")
        if "permission denied" in err.lower():
            # If we get permission denied, it means the daemon is running, we just need sudo!
            return True
    except Exception:
        pass
    return False

def get_docker_compose_cmd() -> list[str]:
    """Detect whether 'docker compose' (modern) or 'docker-compose' (legacy) is available."""
    if shutil.which("docker") is not None:
        try:
            cmd = ["docker", "compose", "version"]
            if docker_needs_sudo():
                cmd = ["sudo", "-n"] + cmd
            r = subprocess.run(cmd, capture_output=True, text=True, errors="ignore", timeout=2)
            if r.returncode == 0:
                return ["docker", "compose"]
        except Exception:
            pass
    if shutil.which("docker-compose") is not None:
        return ["docker-compose"]
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Completion Constants
# ─────────────────────────────────────────────────────────────────────────────

DOCKER_TOP_COMPLETIONS = [
    ("list", "List containers (running & stopped)"),
    ("containers", "List containers (running & stopped)"),
    ("images", "List available local images"),
    ("volumes", "List docker volumes"),
    ("networks", "List docker networks"),
    ("stats", "Show container resource usage stats"),
    ("usage", "Show container resource usage stats"),
    ("start", "Start a container"),
    ("stop", "Stop a container"),
    ("restart", "Restart a container"),
    ("remove", "Remove a container"),
    ("rm", "Remove a container"),
    ("delete-image", "Delete a local image"),
    ("rmi", "Delete a local image"),
    ("logs", "View container logs (last 100 lines)"),
    ("follow", "Follow logs in real-time"),
    ("tail", "Follow logs in real-time"),
    ("exec", "Open interactive shell inside container"),
    ("shell", "Open interactive shell inside container"),
    ("run", "Run a container in the background"),
    ("prune", "Prune unused docker resources"),
    ("compose", "Run compose commands"),
]

COMPOSE_TOP_COMPLETIONS = [
    ("up", "Start compose services in background"),
    ("down", "Stop and remove compose services"),
    ("list", "List compose containers"),
    ("ps", "List compose containers"),
    ("logs", "View compose services logs"),
    ("restart", "Restart compose services"),
    ("build", "Build compose services"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic Autocomplete Loaders
# ─────────────────────────────────────────────────────────────────────────────

def load_docker_containers(all_containers: bool = True) -> list[str]:
    """Return list of docker container names."""
    if not docker_available():
        return []
    try:
        cmd = ["docker", "ps"]
        if all_containers:
            cmd.append("-a")
        cmd += ["--format", "{{.Names}}"]
        if docker_needs_sudo():
            cmd = ["sudo", "-n"] + cmd
        r = subprocess.run(cmd, capture_output=True, text=True, errors="ignore", timeout=3)
        if r.returncode != 0:
            return []
        return [l.strip() for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        return []

def load_docker_images() -> list[str]:
    """Return list of docker images (repo:tag)."""
    if not docker_available():
        return []
    try:
        cmd = ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"]
        if docker_needs_sudo():
            cmd = ["sudo", "-n"] + cmd
        r = subprocess.run(
            cmd,
            capture_output=True, text=True, errors="ignore", timeout=3
        )
        if r.returncode != 0:
            return []
        # Filter out <none>:<none>
        lines = []
        for l in r.stdout.splitlines():
            l = l.strip()
            if l and not l.startswith("<none>"):
                lines.append(l)
        return lines
    except Exception:
        return []

def load_compose_services() -> list[str]:
    """Return list of compose services configured in the current directory."""
    compose_cmd = get_docker_compose_cmd()
    if not compose_cmd:
        return []
    try:
        # Check if compose file exists first to avoid verbose error messages
        files = os.listdir(".")
        has_compose = any(f in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml") for f in files)
        if not has_compose:
            return []
        cmd = compose_cmd + ["config", "--services"]
        if docker_needs_sudo():
            cmd = ["sudo", "-n"] + cmd
        r = subprocess.run(cmd, capture_output=True, text=True, errors="ignore", timeout=3)
        if r.returncode == 0:
            return [l.strip() for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        pass
    return []

# ─────────────────────────────────────────────────────────────────────────────
# Rich Formatting Renderers
# ─────────────────────────────────────────────────────────────────────────────

def show_containers_table(json_str: str):
    table = Table(title="Docker Containers", border_style="cyan")
    table.add_column("Container ID", style="dim cyan")
    table.add_column("Name", style="bold green")
    table.add_column("Image", style="magenta")
    table.add_column("Status", style="yellow")
    table.add_column("Ports", style="blue")
    table.add_column("State", style="bold")
    
    lines = [l.strip() for l in json_str.splitlines() if l.strip()]
    if not lines:
        console.print("[yellow]No containers found.[/yellow]")
        return
        
    for line in lines:
        try:
            data = json.loads(line)
            cid = data.get("ID", "N/A")[:12]
            name = data.get("Names", "N/A")
            image = data.get("Image", "N/A")
            status = data.get("Status", "N/A")
            ports = data.get("Ports", "N/A") or "N/A"
            state = data.get("State", "N/A")
            
            state_colored = f"[green]{state}[/green]" if state == "running" else f"[red]{state}[/red]"
            table.add_row(cid, name, image, status, ports, state_colored)
        except Exception:
            pass
            
    console.print(table)

def show_images_table(json_str: str):
    table = Table(title="Docker Images", border_style="cyan")
    table.add_column("Image ID", style="dim cyan")
    table.add_column("Repository", style="bold green")
    table.add_column("Tag", style="magenta")
    table.add_column("Created", style="yellow")
    table.add_column("Size", style="blue")
    
    lines = [l.strip() for l in json_str.splitlines() if l.strip()]
    if not lines:
        console.print("[yellow]No images found.[/yellow]")
        return
        
    for line in lines:
        try:
            data = json.loads(line)
            # Try to grab ID from either 'ID' or 'UniqueID'
            iid = (data.get("ID") or data.get("UniqueID") or "N/A")[:12]
            repo = data.get("Repository", "N/A")
            tag = data.get("Tag", "N/A")
            created = data.get("CreatedAt", "N/A")
            size = data.get("Size", "N/A")
            
            table.add_row(iid, repo, tag, created, size)
        except Exception:
            pass
            
    console.print(table)

def show_volumes_table(json_str: str):
    table = Table(title="Docker Volumes", border_style="cyan")
    table.add_column("Driver", style="magenta")
    table.add_column("Volume Name", style="bold green")
    table.add_column("Scope", style="yellow")
    
    lines = [l.strip() for l in json_str.splitlines() if l.strip()]
    if not lines:
        console.print("[yellow]No volumes found.[/yellow]")
        return
        
    for line in lines:
        try:
            data = json.loads(line)
            driver = data.get("Driver", "N/A")
            name = data.get("Name", "N/A")
            scope = data.get("Scope", "N/A")
            
            table.add_row(driver, name, scope)
        except Exception:
            pass
            
    console.print(table)

def show_networks_table(json_str: str):
    table = Table(title="Docker Networks", border_style="cyan")
    table.add_column("Network ID", style="dim cyan")
    table.add_column("Name", style="bold green")
    table.add_column("Driver", style="magenta")
    table.add_column("Scope", style="yellow")
    
    lines = [l.strip() for l in json_str.splitlines() if l.strip()]
    if not lines:
        console.print("[yellow]No networks found.[/yellow]")
        return
        
    for line in lines:
        try:
            data = json.loads(line)
            nid = data.get("ID", "N/A")[:12]
            name = data.get("Name", "N/A")
            driver = data.get("Driver", "N/A")
            scope = data.get("Scope", "N/A")
            
            table.add_row(nid, name, driver, scope)
        except Exception:
            pass
            
    console.print(table)

def show_stats_table(json_str: str):
    table = Table(title="Container Resource Usage", border_style="cyan")
    table.add_column("Container", style="bold green")
    table.add_column("CPU %", style="magenta")
    table.add_column("Mem Usage / Limit", style="yellow")
    table.add_column("Mem %", style="blue")
    table.add_column("Net I/O", style="cyan")
    table.add_column("Block I/O", style="dim")
    table.add_column("PIDs", style="red")
    
    lines = [l.strip() for l in json_str.splitlines() if l.strip()]
    if not lines:
        console.print("[yellow]No container stats found.[/yellow]")
        return
        
    for line in lines:
        try:
            data = json.loads(line)
            name = data.get("Name", "N/A")
            cpu = data.get("CPUPerc", "N/A")
            mem = data.get("MemUsage", "N/A")
            memp = data.get("MemPerc", "N/A")
            net = data.get("NetIO", "N/A")
            blk = data.get("BlockIO", "N/A")
            pids = data.get("PIDs", "N/A")
            
            table.add_row(name, cpu, mem, memp, net, blk, pids)
        except Exception:
            pass
            
    console.print(table)

def show_compose_ps_table(json_str: str):
    table = Table(title="Compose Services", border_style="cyan")
    table.add_column("Name", style="bold green")
    table.add_column("Service", style="magenta")
    table.add_column("Status", style="yellow")
    table.add_column("State", style="bold")
    table.add_column("Ports", style="blue")
    
    json_str_stripped = json_str.strip()
    items = []
    if json_str_stripped.startswith("[") and json_str_stripped.endswith("]"):
        try:
            items = json.loads(json_str_stripped)
        except Exception:
            pass
    
    if not items:
        for line in json_str_stripped.splitlines():
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except Exception:
                    pass
                    
    if not items:
        console.print("[yellow]No compose containers found.[/yellow]")
        return
        
    for item in items:
        name = item.get("Name", "N/A")
        service = item.get("Service", "N/A")
        status = item.get("Status", "N/A")
        state = item.get("State", "N/A")
        
        ports_val = item.get("Publishers", "N/A")
        ports_str = ""
        if isinstance(ports_val, list):
            ports_list = []
            for p in ports_val:
                lp = p.get("URL", "") or f"{p.get('PublishedPort', '')}->{p.get('TargetPort', '')}/{p.get('Protocol', '')}"
                ports_list.append(lp)
            ports_str = ", ".join(ports_list)
        else:
            ports_str = str(ports_val)
            
        state_colored = f"[green]{state}[/green]" if state == "running" else f"[red]{state}[/red]"
        table.add_row(name, service, status, state_colored, ports_str)
        
    console.print(table)

# ─────────────────────────────────────────────────────────────────────────────
# Execution Handlers (Universal logic)
# ─────────────────────────────────────────────────────────────────────────────

def run_query_or_fallback(cmd: list[str], fallback_cmd: list[str], parse_fn):
    """Run cmd to parse JSON output. If it fails or returns non-zero, runs fallback_cmd natively."""
    actual_cmd = list(cmd)
    actual_fallback = list(fallback_cmd)
    if docker_needs_sudo():
        actual_cmd = ["sudo"] + actual_cmd
        actual_fallback = ["sudo"] + actual_fallback
    try:
        r = subprocess.run(actual_cmd, capture_output=True, text=True, errors="ignore", timeout=8)
        if r.returncode == 0:
            parse_fn(r.stdout)
            return
    except Exception:
        pass
    # Fallback
    subprocess.run(actual_fallback)

def run_action_with_panel(cmd: list[str], success_msg: str, fail_msg: str):
    """Run a container action and show success/fail panel."""
    actual_cmd = list(cmd)
    if docker_needs_sudo():
        actual_cmd = ["sudo"] + actual_cmd
    try:
        r = subprocess.run(actual_cmd, capture_output=True, text=True, errors="ignore")
        if r.returncode == 0:
            console.print(Panel(success_msg, style="bold green"))
        else:
            err = r.stderr.strip() or r.stdout.strip()
            console.print(Panel(f"{fail_msg}\n[dim]Error: {escape(err)}[/dim]", style="bold red"))
    except Exception as e:
        console.print(Panel(f"{fail_msg}\n[dim]Error: {escape(str(e))}[/dim]", style="bold red"))

def run_docker_logs(container: str):
    """Show container logs beautifully."""
    cmd = ["docker", "logs", "--tail", "100", container]
    if docker_needs_sudo():
        cmd = ["sudo"] + cmd
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            console.print(Panel(escape(out.rstrip()), title=f"Logs: {container}", border_style="cyan"))
        else:
            console.print(f"[red]Failed to get logs for '{container}': {r.stderr.strip()}[/red]")
    except Exception as e:
        console.print(f"[red]Error fetching logs: {e}[/red]")

# ─────────────────────────────────────────────────────────────────────────────
# Command Dispatchers
# ─────────────────────────────────────────────────────────────────────────────

def print_docker_help(title: str = None):
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_row("[bold magenta]# Container Queries (Optimized Tables)[/bold magenta]", "")
    table.add_row("[green]docker list[/green]", "List containers (running & stopped)")
    table.add_row("[green]docker images[/green]", "List available local images")
    table.add_row("[green]docker volumes[/green]", "List docker volumes")
    table.add_row("[green]docker networks[/green]", "List docker networks")
    table.add_row("[green]docker stats[/green]", "Show live-like stats table")
    table.add_row("[bold magenta]# Container Actions[/bold magenta]", "")
    table.add_row("[green]docker start <name>[/green]", "Start a container")
    table.add_row("[green]docker stop <name>[/green]", "Stop a container")
    table.add_row("[green]docker restart <name>[/green]", "Restart a container")
    table.add_row("[green]docker remove <name>[/green]", "Remove a container")
    table.add_row("[green]docker delete-image <img_name>[/green]", "Delete a local image")
    table.add_row("[green]docker logs <name>[/green]", "View container logs (100 lines)")
    table.add_row("[green]docker follow <name>[/green]", "Follow logs in real-time")
    table.add_row("[green]docker exec <name>[/green]", "Open interactive bash/sh inside container")
    table.add_row("[green]docker run <image>[/green]", "Run a container in the background")
    table.add_row("[green]docker prune[/green]", "Prune unused docker resources")
    table.add_row("[green]docker compose ...[/green]", "Run compose commands")
    
    if title is None:
        title = "[bold green]🐳 docker — Universal Docker Commands[/bold green]"
    console.print(Panel(
        table,
        title=title,
        subtitle="[dim]Note: Any standard docker commands (e.g. 'docker run -it') work natively[/dim]",
        border_style="cyan", expand=False
    ))

def print_compose_help(title: str = None):
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_row("[bold magenta]# Compose Actions[/bold magenta]", "")
    table.add_row("[green]compose up[/green]", "Start compose services in background")
    table.add_row("[green]compose down[/green]", "Stop and remove compose services")
    table.add_row("[green]compose list[/green]", "List compose containers (Optimized Table)")
    table.add_row("[green]compose logs[/green]", "View compose services logs")
    table.add_row("[green]compose restart[/green]", "Restart compose services")
    table.add_row("[green]compose build[/green]", "Build compose services")
    
    if title is None:
        title = "[bold green]🐙 compose — Universal Docker Compose Commands[/bold green]"
    console.print(Panel(
        table,
        title=title,
        subtitle="[dim]Also accepts: docker-compose <verb>  |  Native commands run natively[/dim]",
        border_style="cyan", expand=False
    ))

def handle_docker(args: list[str]):
    if not docker_available():
        console.print("[bold red]docker not found.[/bold red] Please install Docker to use this command.")
        return
    if not daemon_running():
        console.print("[bold yellow]Warning:[/bold yellow] Docker daemon is not running. Please start Docker service.")
        return

    if not args:
        print_docker_help()
        return

    sub = args[0].lower()
    
    # Handle universal compose via 'docker compose ...'
    if sub == "compose":
        handle_compose(args[1:])
        return

    # Check universal subcommands
    if sub in ("list", "containers"):
        run_query_or_fallback(
            ["docker", "ps", "-a", "--format", "{{json .}}"],
            ["docker", "ps", "-a"],
            show_containers_table
        )
    elif sub == "images":
        run_query_or_fallback(
            ["docker", "images", "--format", "{{json .}}"],
            ["docker", "images"],
            show_images_table
        )
    elif sub == "volumes":
        run_query_or_fallback(
            ["docker", "volume", "ls", "--format", "{{json .}}"],
            ["docker", "volume", "ls"],
            show_volumes_table
        )
    elif sub == "networks":
        run_query_or_fallback(
            ["docker", "network", "ls", "--format", "{{json .}}"],
            ["docker", "network", "ls"],
            show_networks_table
        )
    elif sub in ("stats", "usage"):
        run_query_or_fallback(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
            ["docker", "stats", "--no-stream"],
            show_stats_table
        )
    elif sub == "start":
        if len(args) < 2:
            console.print("[yellow]Usage: docker start <container_name>[/yellow]")
        else:
            container = args[1]
            run_action_with_panel(
                ["docker", "start", container],
                f"Container '{container}' started successfully.",
                f"Failed to start container '{container}'"
            )
    elif sub == "stop":
        if len(args) < 2:
            console.print("[yellow]Usage: docker stop <container_name>[/yellow]")
        else:
            container = args[1]
            run_action_with_panel(
                ["docker", "stop", container],
                f"Container '{container}' stopped successfully.",
                f"Failed to stop container '{container}'"
            )
    elif sub == "restart":
        if len(args) < 2:
            console.print("[yellow]Usage: docker restart <container_name>[/yellow]")
        else:
            container = args[1]
            run_action_with_panel(
                ["docker", "restart", container],
                f"Container '{container}' restarted successfully.",
                f"Failed to restart container '{container}'"
            )
    elif sub in ("remove", "rm"):
        if len(args) < 2:
            console.print("[yellow]Usage: docker remove <container_name>[/yellow]")
        else:
            container = args[1]
            run_action_with_panel(
                ["docker", "rm", container],
                f"Container '{container}' removed successfully.",
                f"Failed to remove container '{container}'"
            )
    elif sub in ("delete-image", "rmi"):
        if len(args) < 2:
            console.print("[yellow]Usage: docker delete-image <image_name>[/yellow]")
        else:
            img = args[1]
            run_action_with_panel(
                ["docker", "rmi", img],
                f"Image '{img}' deleted successfully.",
                f"Failed to delete image '{img}'"
            )
    elif sub == "logs":
        if len(args) < 2:
            console.print("[yellow]Usage: docker logs <container_name>[/yellow]")
        else:
            run_docker_logs(args[1])
    elif sub in ("follow", "tail"):
        if len(args) < 2:
            console.print("[yellow]Usage: docker follow <container_name>[/yellow]")
        else:
            cmd = ["docker", "logs", "-f", "--tail", "100", args[1]]
            if docker_needs_sudo():
                cmd = ["sudo"] + cmd
            subprocess.run(cmd)
    elif sub in ("exec", "shell"):
        if len(args) < 2:
            console.print("[yellow]Usage: docker exec <container_name>[/yellow]")
        else:
            # Try running bash first, fallback to sh
            container = args[1]
            cmd_base = ["docker", "exec", "-it", container]
            if docker_needs_sudo():
                cmd_base = ["sudo"] + cmd_base
            try:
                # We need to run interactively, let's execute bash directly
                r = subprocess.run(cmd_base + ["/bin/bash"])
                if r.returncode != 0:
                    subprocess.run(cmd_base + ["/bin/sh"])
            except Exception as e:
                console.print(f"[red]Error starting interactive shell: {e}[/red]")
    elif sub == "run":
        if len(args) < 2:
            console.print("[yellow]Usage: docker run <image>[/yellow]")
        else:
            image = args[1]
            run_action_with_panel(
                ["docker", "run", "-d", image],
                f"Container running in background for image '{image}'.",
                f"Failed to run container for image '{image}'"
            )
    elif sub == "prune":
        run_action_with_panel(
            ["docker", "system", "prune", "-f"],
            "System pruned successfully.",
            "Pruning failed"
        )
    else:
        # Native commands passthrough
        cmd = ["docker"] + args
        if docker_needs_sudo():
            cmd = ["sudo"] + cmd
        subprocess.run(cmd)

def handle_compose(args: list[str]):
    compose_cmd = get_docker_compose_cmd()
    if not compose_cmd:
        console.print("[bold red]docker compose / docker-compose not found.[/bold red] Please install docker-compose to use this command.")
        return
    if not daemon_running():
        console.print("[bold yellow]Warning:[/bold yellow] Docker daemon is not running. Please start Docker service.")
        return

    if not args:
        print_compose_help()
        return

    sub = args[0].lower()
    
    # Universal subcommands
    if sub == "up":
        run_action_with_panel(
            compose_cmd + ["up", "-d"],
            "Compose services started successfully.",
            "Failed to start compose services"
        )
    elif sub == "down":
        run_action_with_panel(
            compose_cmd + ["down"],
            "Compose services stopped and removed successfully.",
            "Failed to stop compose services"
        )
    elif sub in ("list", "ps"):
        # docker compose ps JSON support detection
        run_query_or_fallback(
            compose_cmd + ["ps", "-a", "--format", "json"],
            compose_cmd + ["ps", "-a"],
            show_compose_ps_table
        )
    elif sub == "logs":
        # Stream logs
        cmd = compose_cmd + ["logs", "--tail", "100"]
        if docker_needs_sudo():
            cmd = ["sudo"] + cmd
        subprocess.run(cmd)
    elif sub == "restart":
        run_action_with_panel(
            compose_cmd + ["restart"],
            "Compose services restarted successfully.",
            "Failed to restart compose services"
        )
    elif sub == "build":
        cmd = compose_cmd + ["build"]
        if docker_needs_sudo():
            cmd = ["sudo"] + cmd
        subprocess.run(cmd)
    else:
        # Native commands passthrough
        cmd = compose_cmd + args
        if docker_needs_sudo():
            cmd = ["sudo"] + cmd
        subprocess.run(cmd)

# ─────────────────────────────────────────────────────────────────────────────
# Entry point when called via `python -m hopit.docker`
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_docker_help()
        sys.exit(0)
        
    mode = sys.argv[1].lower()
    args = sys.argv[2:]
    
    try:
        if mode == "docker":
            handle_docker(args)
        elif mode == "compose":
            handle_compose(args)
        else:
            # Fallback to docker
            handle_docker(sys.argv[1:])
    except KeyboardInterrupt:
        sys.exit(0)
