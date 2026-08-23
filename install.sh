#!/bin/sh
# ============================================================================
#  install.sh  --  Automated production installer for hopit-cli
#  Supports Linux and macOS (Intel & Apple Silicon) with zero external python dependencies
# ============================================================================

set -e

# Colored text utilities
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

print_step() {
    printf "  [${CYAN}%d/6${NC}] %s...\n" "$1" "$2"
}

print_ok() {
    printf "   ${GREEN}[OK]${NC} %s\n" "$1"
}

print_warn() {
    printf " ${YELLOW}[WARN]${NC} %s\n" "$1"
}

print_err() {
    printf "  ${RED}[ERR]${NC} %s\n" "$1"
}

# Clear screen for a neat setup view
clear || true

echo "  ============================================"
echo "   hopit-cli  -  Linux & macOS Setup"
echo "  ============================================"
echo ""

# -- 1. OS & Architecture Detection ------------------------------------------
print_step 1 "Detecting System Environment"

OS=$(uname -s)
ARCH=$(uname -m)

TRIPLE=""
if [ "$OS" = "Darwin" ]; then
    if [ "$ARCH" = "x86_64" ]; then
        TRIPLE="x86_64-apple-darwin"
    elif [ "$ARCH" = "arm64" ]; then
        TRIPLE="aarch64-apple-darwin"
    fi
elif [ "$OS" = "Linux" ]; then
    if [ "$ARCH" = "x86_64" ]; then
        TRIPLE="x86_64-unknown-linux-gnu"
    elif [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
        TRIPLE="aarch64-unknown-linux-gnu"
    fi
fi

if [ -z "$TRIPLE" ]; then
    print_err "Unsupported OS or Architecture: $OS $ARCH"
    exit 1
fi

print_ok "Detected Environment: $OS ($ARCH)"

# -- 2. Check for Compatible System Python / Setup Runtime -------------------
print_step 2 "Preparing Python Environment"

INSTALL_DIR="$HOME/.local/share/hopit-cli"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

SYS_PYTHON=""
if command -v python3 >/dev/null 2>&1; then
    SYS_PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    SYS_PYTHON="python"
fi

USE_SYSTEM=0
if [ -n "$SYS_PYTHON" ]; then
    VER=$($SYS_PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
    MAJOR=$(echo "$VER" | cut -d. -f1)
    MINOR=$(echo "$VER" | cut -d. -f2)
    if [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 10 ] || [ "$MAJOR" -gt 3 ]; then
        USE_SYSTEM=1
    fi
fi

PYTHON_EXEC=""
if [ $USE_SYSTEM -eq 1 ]; then
    print_ok "Found compatible system Python (v$VER). Creating virtual environment."
    if $SYS_PYTHON -m venv "$INSTALL_DIR/venv" 2>/dev/null; then
        PYTHON_EXEC="$INSTALL_DIR/venv/bin/python"
    else
        print_warn "Failed to create virtual environment. Falling back to standalone runtime."
        USE_SYSTEM=0
    fi
fi

if [ $USE_SYSTEM -eq 0 ]; then
    print_warn "No compatible system Python (>= 3.10) found. Preparing standalone runtime."
    printf "        Downloading standalone Python (approx. 13 MB)...\n"
    
    RELEASE_TAG="20260814"
    PYTHON_VERSION="3.12.14"
    ASSET_NAME="cpython-${PYTHON_VERSION}+${RELEASE_TAG}-${TRIPLE}-install_only_stripped.tar.gz"
    URL="https://github.com/astral-sh/python-build-standalone/releases/download/${RELEASE_TAG}/${ASSET_NAME}"

    if command -v curl >/dev/null 2>&1; then
        curl -L -s "$URL" | tar -C "$INSTALL_DIR" -xzf -
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- "$URL" | tar -C "$INSTALL_DIR" -xzf -
    else
        print_err "Neither curl nor wget found. Please install one of them and re-run."
        exit 1
    fi

    if [ ! -d "$INSTALL_DIR/python" ]; then
        print_err "Extraction failed. Standalone python not found at $INSTALL_DIR/python."
        exit 1
    fi
    PYTHON_EXEC="$INSTALL_DIR/python/bin/python3"
    print_ok "Python standalone runtime downloaded and extracted"
fi

# -- 3. Copy Source Files ----------------------------------------------------
print_step 3 "Deploying application source files"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$INSTALL_DIR/src"

cp -R "$SCRIPT_DIR/hopit" "$INSTALL_DIR/src/"
cp "$SCRIPT_DIR/hopit-cli.py" "$INSTALL_DIR/src/"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/src/"

print_ok "Application source files copied to sandboxed directory"

# -- 4. Install Dependencies -------------------------------------------------
print_step 4 "Installing required Python dependencies"

# Upgrade pip first
"$PYTHON_EXEC" -m pip install --upgrade pip --quiet 2>/dev/null || true

# Install requirements
"$PYTHON_EXEC" -m pip install -r "$INSTALL_DIR/src/requirements.txt" --quiet

print_ok "Python dependencies installed successfully"

# -- 5. Create Shell Launcher ------------------------------------------------
print_step 5 "Creating PATH launcher"

LAUNCHER_DIR="$HOME/.local/bin"
mkdir -p "$LAUNCHER_DIR"

LAUNCHER="$LAUNCHER_DIR/hopit-cli"
rm -f "$LAUNCHER"

cat << EOF > "$LAUNCHER"
#!/bin/sh
# hopit-cli sandboxed launcher
exec "$PYTHON_EXEC" "$INSTALL_DIR/src/hopit-cli.py" "\$@"
EOF

chmod +x "$LAUNCHER"
print_ok "Launcher created at $LAUNCHER"

# -- 6. PATH Verification ----------------------------------------------------
print_step 6 "Verifying PATH configuration"

PATH_OK=0
case ":$PATH:" in
    *:"$LAUNCHER_DIR":*) PATH_OK=1 ;;
esac

echo ""
echo "  ============================================"
if [ $PATH_OK -eq 1 ]; then
    printf "   ${GREEN}All done! Open a new terminal and run:${NC}\n"
    echo ""
    echo "       hopit-cli"
    echo ""
else
    printf "   ${YELLOW}Almost done! One manual step required:${NC}\n"
    echo ""
    echo "   $LAUNCHER_DIR is not in your PATH."
    echo "   Please add the following line to your shell profile"
    echo "   (e.g., ~/.bashrc, ~/.zshrc, or ~/.bash_profile):"
    echo ""
    echo "       export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
    printf "   Then, restart your terminal and run: ${CYAN}hopit-cli${NC}\n"
fi
echo "  ============================================"
echo ""
