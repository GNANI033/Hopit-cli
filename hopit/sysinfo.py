import os
import sys
import platform
import socket
import shutil
from datetime import datetime, timedelta

def get_uptime():
    try:
        if platform.system() == "Linux":
            with open("/proc/uptime", "r") as f:
                uptime_seconds = float(f.readline().split()[0])
                return str(timedelta(seconds=int(uptime_seconds)))
        elif platform.system() == "Darwin":
            import subprocess
            out = subprocess.check_output(["sysctl", "-n", "kern.boottime"], text=True)
            if "sec =" in out:
                sec = int(out.split("sec =")[1].split(",")[0].strip())
                import time
                uptime_seconds = time.time() - sec
                return str(timedelta(seconds=int(uptime_seconds)))
        elif platform.system() == "Windows":
            import subprocess
            out = subprocess.check_output(["wmic", "os", "get", "lastbootuptime"], text=True)
            lines = [l.strip() for l in out.splitlines() if l.strip()]
            if len(lines) > 1:
                boot_str = lines[1].split(".")[0]
                boot_time = datetime.strptime(boot_str, "%Y%m%d%H%M%S")
                uptime_seconds = (datetime.now() - boot_time).total_seconds()
                return str(timedelta(seconds=int(uptime_seconds)))
    except Exception:
        pass
    return "Unknown"

def get_cpu_info():
    try:
        system = platform.system()
        if system == "Linux":
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        model = line.split(":", 1)[1].strip()
                        cores = os.cpu_count()
                        return f"{model} ({cores} cores)"
        elif system == "Darwin":
            import subprocess
            model = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
            cores = subprocess.check_output(["sysctl", "-n", "hw.ncpu"], text=True).strip()
            return f"{model} ({cores} cores)"
        elif system == "Windows":
            import subprocess
            model = subprocess.check_output(["wmic", "cpu", "get", "Name"], text=True).splitlines()
            model = [m.strip() for m in model if m.strip()]
            cores = os.cpu_count()
            if len(model) > 1:
                return f"{model[1]} ({cores} cores)"
    except Exception:
        pass
    return f"{platform.processor()} ({os.cpu_count()} cores)"

def get_mem_info():
    try:
        system = platform.system()
        if system == "Linux":
            with open("/proc/meminfo", "r") as f:
                meminfo = {}
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        meminfo[parts[0].strip()] = int(parts[1].split()[0])
                total = meminfo.get("MemTotal", 0) * 1024
                free = meminfo.get("MemAvailable", meminfo.get("MemFree", 0)) * 1024
                used = total - free
                return total, free, used
        elif system == "Darwin":
            import subprocess
            total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip())
            vm = subprocess.check_output(["vm_stat"], text=True).splitlines()
            page_size = 4096
            for line in vm:
                if "page size of" in line:
                    page_size = int(line.split("page size of")[1].split("bytes")[0].strip())
                    break
            free_pages = 0
            for line in vm:
                if "Pages free:" in line:
                    free_pages = int(line.split("Pages free:")[1].strip().strip("."))
                    break
            free = free_pages * page_size
            used = total - free
            return total, free, used
        elif system == "Windows":
            import subprocess
            out = subprocess.check_output(["wmic", "OS", "get", "TotalVisibleMemorySize,FreePhysicalMemory", "/value"], text=True)
            meminfo = {}
            for line in out.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    meminfo[k.strip()] = int(v.strip())
            total = meminfo.get("TotalVisibleMemorySize", 0) * 1024
            free = meminfo.get("FreePhysicalMemory", 0) * 1024
            used = total - free
            return total, free, used
    except Exception:
        pass
    return 0, 0, 0

def format_size(bytes_val):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} PB"

def main():
    hostname = socket.gethostname()
    os_name = platform.system()
    if os_name == "Linux":
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        os_name = line.split("=", 1)[1].strip().strip('"')
                        break
        except Exception:
            os_name = "Linux"
    elif os_name == "Darwin":
        os_name = "macOS"
    
    kernel = f"{platform.system()} {platform.release()}"
    uptime = get_uptime()
    cpu = get_cpu_info()
    
    total_mem, free_mem, used_mem = get_mem_info()
    mem_str = "Unknown"
    if total_mem > 0:
        pct = (used_mem / total_mem) * 100
        mem_str = f"{format_size(used_mem)} / {format_size(total_mem)} ({pct:.1f}% used)"
        
    total_disk, used_disk, free_disk = shutil.disk_usage(".")
    pct_disk = (used_disk / total_disk) * 100
    disk_str = f"{format_size(used_disk)} / {format_size(total_disk)} ({pct_disk:.1f}% used)"

    from hopit.config import console
    from rich.table import Table
    from rich.panel import Panel

    table = Table.grid(padding=(0, 2))
    table.add_column("Key", style="bold cyan")
    table.add_column("Val")
    
    table.add_row("OS", os_name)
    table.add_row("Kernel", kernel)
    table.add_row("Hostname", hostname)
    table.add_row("Uptime", uptime)
    table.add_row("CPU", cpu)
    table.add_row("Memory", mem_str)
    table.add_row("Disk", disk_str)
    
    console.print(Panel(table, title="[bold green]💻 System Information[/bold green]", border_style="cyan", expand=False))

if __name__ == "__main__":
    main()
