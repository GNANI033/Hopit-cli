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

from hopit.main import main

if __name__ == "__main__":
    main()
