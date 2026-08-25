import os
import sys
import platform
import socket
import struct
import subprocess
from rich.console import Console
from rich.panel import Panel

def get_gateway():
    system = platform.system()
    if system == "Linux":
        try:
            with open("/proc/net/route") as fh:
                for line in fh:
                    fields = line.strip().split()
                    if len(fields) > 8 and fields[1] == '00000000' and fields[8] == '00000000':
                        return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
        except Exception:
            pass
        # fallback to ip route
        try:
            out = subprocess.run(["ip", "route"], capture_output=True, text=True, errors="ignore")
            for line in out.stdout.splitlines():
                if "default via" in line:
                    return line.split("via")[1].strip().split()[0]
        except Exception:
            pass

    elif system == "Darwin":
        try:
            out = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True, errors="ignore")
            for line in out.stdout.splitlines():
                if "gateway:" in line:
                    return line.split("gateway:")[1].strip()
        except Exception:
            pass

    elif system == "Windows":
        try:
            out = subprocess.run(["ipconfig"], capture_output=True, text=True, errors="ignore")
            for line in out.stdout.splitlines():
                if "Default Gateway" in line and ":" in line:
                    gw = line.split(":", 1)[1].strip()
                    if gw:
                        return gw
        except Exception:
            pass
            
    return "Unknown"

def main():
    console = Console()
    gw = get_gateway()
    if gw == "Unknown":
        console.print("[red]Could not determine default gateway.[/red]")
    else:
        console.print(Panel(f"Default Gateway: [bold green]{gw}[/bold green]", border_style="cyan", expand=False))

if __name__ == "__main__":
    main()
