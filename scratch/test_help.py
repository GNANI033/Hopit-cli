import sys
import os

# Ensure the root dir is in path
script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, script_dir)

from hopit.commands import build_commands
from hopit.main import show_context_help

# Mock names for build_commands
names = {
    "service": lambda: [],
    "installed_pkg": lambda: [],
    "available_pkg": lambda prefix="": [],
    "path": lambda word="": [],
    "adapter": lambda: [],
    "user": lambda: [],
    "group": lambda: [],
}

cmds = build_commands(None, names)

test_scenarios = [
    ["show"],
    ["show", "file"],
    ["show", "env"],
    ["processes"],
    ["ps"],
    ["kill"],
    ["pkill"],
    ["ping"],
    ["dns"],
    ["netconfig"],
    ["netconfig", "dhcp"],
    ["netconfig", "dhcp", "release"],
    ["firewall"],
    ["firewall", "allow"],
    ["firewall", "allow", "80"],
    ["firewall", "allow", "80", "tcp"],
    ["disk"],
    ["disk", "usage"],
    ["disk", "mount"],
    ["disk", "mount", "dev"],
    ["archive"],
    ["archive", "create"],
    ["archive", "create", "out.zip"],
    ["sessions"],
    ["sessions", "kill"],
    ["k8s"],
    ["kubectl"],
    ["less"],
    ["head"],
    ["head", "-n"],
]

for scenario in test_scenarios:
    print(f"=== HELP FOR: {' '.join(scenario)} ? ===")
    show_context_help(scenario, cmds)
    print("\n")
