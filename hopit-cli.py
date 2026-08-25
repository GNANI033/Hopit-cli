#!/usr/bin/env python3
import os
import sys

# Ensure the directory containing this script is in python path
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

# Ensure the parent directory containing the hopit package is in PYTHONPATH so subprocesses can locate hopit
existing_pythonpath = os.environ.get("PYTHONPATH", "")
if script_dir not in existing_pythonpath.split(os.pathsep):
    if existing_pythonpath:
        os.environ["PYTHONPATH"] = f"{script_dir}{os.pathsep}{existing_pythonpath}"
    else:
        os.environ["PYTHONPATH"] = script_dir

# Force UTF-8 encoding globally to prevent Windows UnicodeEncodeError crashes in child processes
os.environ["PYTHONUTF8"] = "1"

# Also force the current process to use UTF-8 for standard output (fixes '?' replacing emojis)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from hopit.main import main

if __name__ == "__main__":
    main()
