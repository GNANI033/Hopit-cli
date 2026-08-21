import subprocess
import platform
import sys

def main():
    system = platform.system()
    if system == "Windows":
        cmd = ["tasklist"]
    elif system == "Darwin":
        cmd = ["ps", "-cax", "-o", "pid,user,state,command"]
    else:
        # Linux / other Unix-like OS
        cmd = ["ps", "ax", "-o", "pid,user,stat,comm"]
        
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print(proc.stdout.strip())
    except Exception as e:
        print(f"Error fetching processes: {e}")

if __name__ == "__main__":
    main()
