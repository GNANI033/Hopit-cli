import sys
import subprocess
import shlex
from hopit.config import IS_WINDOWS, IS_MACOS

def kill_process_on_port(port: str):
    if not port.isdigit():
        print(f"Error: Port must be a numeric value, got '{port}'.")
        sys.exit(1)
        
    port_num = int(port)
    print(f"Searching for process listening on port {port_num}...")
    
    pids = set()
    
    if IS_WINDOWS:
        cmd = [
            "powershell", "-NoProfile", "-Command",
            f"Get-NetTCPConnection -LocalPort {port_num} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess"
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            for line in res.stdout.splitlines():
                if line.strip().isdigit():
                    pids.add(line.strip())
        except Exception:
            pass
    else:
        # Linux or macOS
        try:
            res = subprocess.run(["lsof", "-i", f":{port_num}", "-t"], capture_output=True, text=True)
            for line in res.stdout.splitlines():
                if line.strip().isdigit():
                    pids.add(line.strip())
        except Exception:
            pass
            
        if not pids and not IS_MACOS:
            try:
                res = subprocess.run(["fuser", f"{port_num}/tcp"], capture_output=True, text=True)
                out = res.stdout.strip() or res.stderr.strip()
                for token in out.split():
                    clean = token.rstrip("/tcp").strip()
                    if clean.isdigit():
                        pids.add(clean)
            except Exception:
                pass

    if not pids:
        print(f"No process found running on port {port_num}.")
        sys.exit(0)
        
    print(f"Found process PID(s) running on port {port_num}: {', '.join(pids)}")
    
    for pid in pids:
        if IS_WINDOWS:
            kill_cmd = ["taskkill", "/PID", pid, "/F"]
        else:
            kill_cmd = ["kill", "-9", pid]
            
        try:
            res = subprocess.run(kill_cmd, capture_output=True, text=True)
            if res.returncode == 0:
                print(f"Successfully terminated process PID {pid}.")
            else:
                print(f"Failed to terminate PID {pid}: {res.stderr.strip()}")
        except Exception as e:
            print(f"Error terminating PID {pid}: {e}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m hopit.killport <port_number>")
        sys.exit(1)
        
    kill_process_on_port(sys.argv[1])


if __name__ == "__main__":
    main()
