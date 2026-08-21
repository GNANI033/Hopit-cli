# ============================================================================
#  install-windows.ps1  --  Full automated lazyctl setup for Windows
#  Run via:  install-windows.bat   (or directly in PowerShell)
#
#  What this does, automatically:
#   1. Ensures Python 3.10+ is installed (via winget if missing)
#   2. Installs Windows Terminal         (via winget if missing)
#   3. Downloads & installs CascadiaCode Nerd Font (user-level, no UAC)
#   4. Configures Windows Terminal to use the Nerd Font
#   5. pip-installs lazyctl dependencies
#   6. Creates a lazyctl.bat launcher and adds it to the user PATH
# ============================================================================

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# -- Helpers -----------------------------------------------------------------
function Write-Step  { param($n, $msg) Write-Host "  [$n/6] $msg" -ForegroundColor Cyan }
function Write-Ok    { param($msg)     Write-Host "   [OK] $msg"   -ForegroundColor Green }
function Write-Warn  { param($msg)     Write-Host " [WARN] $msg"   -ForegroundColor Yellow }
function Write-Err   { param($msg)     Write-Host "  [ERR] $msg"   -ForegroundColor Red }
function Write-Info  { param($msg)     Write-Host "        $msg"   -ForegroundColor DarkGray }

function Refresh-Path {
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH","User")
}

function Get-WingetId { param($id)
    winget list --id $id --accept-source-agreements 2>$null | Select-String $id
}

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Magenta
Write-Host "   lazyctl  -  Windows Automated Setup"        -ForegroundColor Magenta
Write-Host "  ============================================" -ForegroundColor Magenta
Write-Host ""

# -- 1. Python ----------------------------------------------------------------
Write-Step 1 "Python 3.10+"

$py = Get-Command python -ErrorAction SilentlyContinue
$needPython = $true
if ($py) {
    $ver = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    if ($ver -and ([version]$ver -ge [version]"3.10")) {
        Write-Ok "Python $ver already installed"
        $needPython = $false
    } else {
        Write-Warn "Python $ver found but 3.10+ required -- upgrading"
    }
}

if ($needPython) {
    Write-Info "Installing Python 3.12 via winget..."
    winget install --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements -e --silent
    Refresh-Path
    $ver = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    if ($ver) { Write-Ok "Python $ver installed" }
    else {
        Write-Err "Python install failed. Please install manually from https://python.org"
        Write-Info "Make sure to tick 'Add Python to PATH', then re-run this script."
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# -- 2. Windows Terminal -------------------------------------------------------
Write-Host ""
Write-Step 2 "Windows Terminal"

$wtInstalled = (Get-Command wt -ErrorAction SilentlyContinue) -or
               (Test-Path "$env:LOCALAPPDATA\Microsoft\WindowsApps\wt.exe")

if ($wtInstalled) {
    Write-Ok "Windows Terminal already installed"
} else {
    Write-Info "Installing Windows Terminal via winget..."
    winget install --id Microsoft.WindowsTerminal --accept-package-agreements --accept-source-agreements -e --silent
    Refresh-Path
    Write-Ok "Windows Terminal installed"
}

# -- 3. CascadiaCode Nerd Font -------------------------------------------------
Write-Host ""
Write-Step 3 "CascadiaCode Nerd Font (powerline arrows + icons)"

$fontDir  = "$env:LOCALAPPDATA\Microsoft\Windows\Fonts"
$fontFile = "$fontDir\CascadiaCodeNFM-Regular.ttf"
$fontRegPath = "HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts"
$fontRegName = "CaskaydiaCove Nerd Font Mono Regular (TrueType)"

if (Test-Path $fontFile) {
    Write-Ok "Font already installed"
} else {
    $fontUrl    = "https://github.com/ryanoasis/nerd-fonts/releases/download/v3.3.0/CascadiaCode.zip"
    $zipDest    = "$env:TEMP\lazyctl_CascadiaCode.zip"
    $extractDir = "$env:TEMP\lazyctl_CascadiaCode"

    Write-Info "Downloading CascadiaCode Nerd Font (~5 MB)..."
    try {
        # Use faster TLS and progress-free download
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $wc = New-Object System.Net.WebClient
        $wc.DownloadFile($fontUrl, $zipDest)

        Write-Info "Extracting..."
        Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue
        Expand-Archive -Path $zipDest -DestinationPath $extractDir -Force

        # Find the Regular weight TTF (avoid Bold/Italic)
        $ttf = Get-ChildItem $extractDir -Filter "CascadiaCodeNFM-Regular.ttf" -Recurse |
               Select-Object -First 1

        if ($ttf) {
            New-Item -ItemType Directory -Force -Path $fontDir | Out-Null
            Copy-Item $ttf.FullName $fontFile -Force

            # Register in HKCU so apps (including WT) see it without a reboot
            if (-not (Test-Path $fontRegPath)) {
                New-Item -Path $fontRegPath -Force | Out-Null
            }
            New-ItemProperty -Path $fontRegPath -Name $fontRegName `
                             -Value $fontFile -PropertyType String -Force | Out-Null

            Write-Ok "Font installed (user-level, no admin required)"
        } else {
            Write-Warn "TTF not found in archive -- font skipped"
            Write-Info "Arrows will show as boxes; everything else still works."
        }
    } catch {
        Write-Warn "Font download failed: $_"
        Write-Info "Colors still work perfectly -- only the arrow separators will be plain '>'."
        Write-Info "Manual install later: https://github.com/ryanoasis/nerd-fonts/releases"
    } finally {
        Remove-Item $zipDest    -ErrorAction SilentlyContinue
        Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# -- 4. Configure Windows Terminal to use the font ----------------------------
Write-Host ""
Write-Step 4 "Configuring Windows Terminal font"

# WT stores settings in a Store app package folder; check both stable and preview
$wtSettingsCandidates = @(
    "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json",
    "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe\LocalState\settings.json"
)

$fontSet = $false
foreach ($settingsPath in $wtSettingsCandidates) {
    if (-not (Test-Path $settingsPath)) { continue }
    try {
        $raw  = Get-Content $settingsPath -Raw -Encoding UTF8
        # Strip // line comments so ConvertFrom-Json doesn't choke on JSONC
        $json = $raw -replace '(?m)^\s*//[^\r\n]*', '' `
                     -replace '(?m),(\s*[\}\]])', '$1'   # trailing commas after strip

        $cfg = $json | ConvertFrom-Json

        # Ensure the nested path exists
        if (-not $cfg.PSObject.Properties['profiles']) {
            $cfg | Add-Member -NotePropertyName profiles -NotePropertyValue ([PSCustomObject]@{}) -Force
        }
        if (-not $cfg.profiles.PSObject.Properties['defaults']) {
            $cfg.profiles | Add-Member -NotePropertyName defaults -NotePropertyValue ([PSCustomObject]@{}) -Force
        }
        $fontObj = [PSCustomObject]@{ face = "CaskaydiaCove Nerd Font Mono" }
        if (-not $cfg.profiles.defaults.PSObject.Properties['font']) {
            $cfg.profiles.defaults | Add-Member -NotePropertyName font -NotePropertyValue $fontObj -Force
        } else {
            $cfg.profiles.defaults.font.face = "CaskaydiaCove Nerd Font Mono"
        }

        $cfg | ConvertTo-Json -Depth 20 | Set-Content $settingsPath -Encoding UTF8
        Write-Ok "Font set in Windows Terminal settings"
        $fontSet = $true
        break
    } catch {
        Write-Warn "Could not patch settings.json: $_"
    }
}

if (-not $fontSet) {
    Write-Warn "Windows Terminal settings not found yet."
    Write-Info "Launch Windows Terminal once, close it, then re-run this script to set the font."
    Write-Info "(Everything else is already set up -- this is just the font step.)"
}

# -- 5. pip install Python packages --------------------------------------------
Write-Host ""
Write-Step 5 "Installing Python packages (prompt_toolkit, rich, colorama)"

python -m pip install --upgrade pip --quiet
python -m pip install -r "$ScriptDir\requirements.txt" --quiet

if ($LASTEXITCODE -eq 0) {
    Write-Ok "Packages installed"
} else {
    Write-Err "pip install failed -- check your internet connection"
    Read-Host "Press Enter to exit"
    exit 1
}

# -- 6. Create launcher + add to user PATH ------------------------------------
Write-Host ""
Write-Step 6 "Creating lazyctl launcher"

$lazyctlDir = "$env:LOCALAPPDATA\lazyctl"
New-Item -ItemType Directory -Force -Path $lazyctlDir | Out-Null

$launcher = "$lazyctlDir\lazyctl.bat"
"@echo off`r`npython `"$ScriptDir\lazyctl-v4.py`" %*" | Set-Content $launcher -Encoding ASCII

$userPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath -notlike "*$lazyctlDir*") {
    [System.Environment]::SetEnvironmentVariable("PATH", "$userPath;$lazyctlDir", "User")
    Write-Ok "lazyctl added to your user PATH"
} else {
    Write-Ok "Already in PATH"
}

# -- Done ---------------------------------------------------------------------
Write-Host ""
Write-Host "  ============================================" -ForegroundColor Green
Write-Host "   All done!  Open Windows Terminal and run:" -ForegroundColor Green
Write-Host ""
Write-Host "       lazyctl" -ForegroundColor White
Write-Host ""
Write-Host "   (Open a NEW terminal window for PATH to apply)" -ForegroundColor DarkGray
Write-Host "  ============================================" -ForegroundColor Green
Write-Host ""
