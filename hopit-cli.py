#!/usr/bin/env python3
import os
import sys

# Ensure the directory containing this script is in python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hopit.main import main

if __name__ == "__main__":
    main()
