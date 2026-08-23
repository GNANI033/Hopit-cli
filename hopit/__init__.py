# hopit package
import os
import sys
import signal

def _setup_clean_sigint():
    # Only apply to submodules being executed directly, not the main hopit-cli shell
    is_main_shell = False
    if sys.argv:
        basename = os.path.basename(sys.argv[0])
        if basename in ("hopit-cli.py", "hopit-cli", "main.py"):
            is_main_shell = True
            
    if not is_main_shell:
        def sigint_handler(signum, frame):
            sys.exit(130)  # Standard exit code for Ctrl-C
        try:
            signal.signal(signal.SIGINT, sigint_handler)
        except ValueError:
            # signal only works in main thread; ignore if imported elsewhere
            pass

_setup_clean_sigint()
