@echo off
goto :Batch

<#
:Batch
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -LiteralPath $args[0] | Select-Object -Skip 8 | Out-String | iex" "%~f0"
exit /b %errorlevel%
#>

# ============================================================================
#  install-windows.bat  --  Unified production installer for hopit-cli
#  Checks for system Python (>= 3.10) to create a venv, or downloads portable CPython
#  Supports Windows x64 and ARM64
# ============================================================================

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# -- Helpers -----------------------------------------------------------------
function Write-Step  { param($n, $msg) Write-Host "  [$n/6] $msg..." -ForegroundColor Cyan }
function Write-Ok    { param($msg)     Write-Host "   [OK] $msg"   -ForegroundColor Green }
function Write-Warn  { param($msg)     Write-Host " [WARN] $msg"   -ForegroundColor Yellow }
function Write-Err   { param($msg)     Write-Host "  [ERR] $msg"   -ForegroundColor Red }
function Write-Info  { param($msg)     Write-Host "        $msg"   -ForegroundColor DarkGray }

function Refresh-Path {
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH","User")
}

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Magenta
Write-Host "   hopit-cli  -  Windows Automated Setup"        -ForegroundColor Magenta
Write-Host "  ============================================" -ForegroundColor Magenta
Write-Host ""

# -- 1. OS & Architecture Detection ------------------------------------------
Write-Step 1 "Detecting System Environment"

$arch = $env:PROCESSOR_ARCHITECTURE
$triple = "x86_64-pc-windows-msvc"

if ($arch -eq "ARM64") {
    $triple = "aarch64-pc-windows-msvc"
}

Write-Ok "Detected Architecture: $arch ($triple)"

# -- 2. Check for compatible System Python -----------------------------------
Write-Step 2 "Checking for compatible local Python (>= 3.10)"

$useSystem = $false
$verOut = ""
try {
    $verOut = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    if ($verOut) {
        $parts = $verOut.Split('.')
        $major = [int]$parts[0]
        $minor = [int]$parts[1]
        if (($major -eq 3 -and $minor -ge 10) -or $major -gt 3) {
            $useSystem = $true
        }
    }
} catch {}

$hopitDir = "$env:LOCALAPPDATA\hopit-cli"
if (Test-Path $hopitDir) {
    Remove-Item $hopitDir -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $hopitDir | Out-Null

$pyExe = ""

if ($useSystem) {
    Write-Ok "Found system Python (v$verOut). Creating isolated virtual environment."
    $venvDir = "$hopitDir\venv"
    try {
        & python -m venv $venvDir
        $pyExe = "$venvDir\Scripts\python.exe"
    } catch {
        Write-Warn "Failed to create virtual environment: $_. Falling back to standalone runtime."
        $useSystem = $false
    }
}

if (-not $useSystem) {
    Write-Warn "No compatible system Python (>= 3.10) found. Preparing standalone runtime."
    Write-Info "Downloading standalone CPython (approx. 15 MB)..."
    
    $releaseTag = "20260814"
    $pythonVersion = "3.12.14"
    $assetName = "cpython-$pythonVersion+$releaseTag-$triple-shared-install_only_stripped.tar.gz"
    $url = "https://github.com/astral-sh/python-build-standalone/releases/download/$releaseTag/$assetName"
    
    $tempTar = "$env:TEMP\hopit_python.tar.gz"
    if (Test-Path $tempTar) { Remove-Item $tempTar -Force }
    
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13
        $webClient = New-Object System.Net.WebClient
        $webClient.DownloadFile($url, $tempTar)
        
        $tarPath = "$env:SystemRoot\System32\tar.exe"
        if (Test-Path $tarPath) {
            & $tarPath -xf $tempTar -C $hopitDir
            $pyExe = "$hopitDir\python\python.exe"
        } else {
            throw "tar.exe not found. Cannot extract standalone runtime archive."
        }
        Remove-Item $tempTar -Force
    } catch {
        Write-Err "Failed to download/extract portable Python: $_"
        Read-Host "Press Enter to exit"
        exit 1
    }
}

if (-not (Test-Path $pyExe)) {
    Write-Err "Python executable not found at $pyExe."
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Ok "Python environment prepared successfully"

# -- 3. Copy Source Files ----------------------------------------------------
Write-Step 3 "Deploying application source files"

$srcDir = "$hopitDir\src"
New-Item -ItemType Directory -Force -Path $srcDir | Out-Null

Copy-Item -Path "$ScriptDir\hopit" -Destination "$srcDir\hopit" -Recurse -Force
Copy-Item -Path "$ScriptDir\hopit-cli.py" -Destination "$srcDir\hopit-cli.py" -Force
Copy-Item -Path "$ScriptDir\requirements.txt" -Destination "$srcDir\requirements.txt" -Force

Write-Ok "Application source files copied to sandboxed directory"

# -- 4. Install Dependencies -------------------------------------------------
Write-Step 4 "Installing required Python dependencies"

try {
    & $pyExe -m pip install --upgrade pip --quiet
    & $pyExe -m pip install -r "$srcDir\requirements.txt" --quiet
    Write-Ok "Dependencies installed successfully"
} catch {
    Write-Err "Dependency installation failed: $_"
    Read-Host "Press Enter to exit"
    exit 1
}

# -- 5. CascadiaCode Nerd Font & Terminal Setup ------------------------------
Write-Step 5 "Optional UI Improvements (Nerd Font)"

$fontDir = "$env:LOCALAPPDATA\Microsoft\Windows\Fonts"
$fontRegPath = "HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts"
$fontRegName = "CaskaydiaCove Nerd Font Mono Regular (TrueType)"

$fontInstalled = $false
if (Test-Path $fontRegPath) {
    $reg = Get-ItemProperty -Path $fontRegPath -ErrorAction SilentlyContinue
    if ($reg -and $reg.PSObject.Properties[$fontRegName]) {
        $fontInstalled = $true
    }
}

if ($fontInstalled) {
    Write-Ok "Font already installed"
} else {
    $fontUrl = "https://github.com/ryanoasis/nerd-fonts/releases/download/v3.3.0/CascadiaCode.zip"
    $zipDest = "$env:TEMP\hopit_CascadiaCode.zip"
    $extractDir = "$env:TEMP\hopit_CascadiaCode"

    Write-Info "Downloading CascadiaCode Nerd Font (~5 MB)..."
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13
        $webClient = New-Object System.Net.WebClient
        $webClient.DownloadFile($fontUrl, $zipDest)
        Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue
        Expand-Archive -Path $zipDest -DestinationPath $extractDir -Force

        $ttf = Get-ChildItem $extractDir -Filter "*.ttf" -Recurse |
               Where-Object { $_.Name -like "*CaskaydiaCove*Mono*-Regular.ttf" -or $_.Name -like "*Caskaydia*NFM*-Regular.ttf" } |
               Select-Object -First 1

        if ($ttf) {
            $fontFile = Join-Path $fontDir $ttf.Name
            New-Item -ItemType Directory -Force -Path $fontDir | Out-Null
            Copy-Item $ttf.FullName $fontFile -Force

            if (-not (Test-Path $fontRegPath)) {
                New-Item -Path $fontRegPath -Force | Out-Null
            }
            New-ItemProperty -Path $fontRegPath -Name $fontRegName -Value $fontFile -PropertyType String -Force | Out-Null
            Write-Ok "Nerd Font installed: $($ttf.Name)"
        }
    } catch {
        Write-Warn "Font download/install skipped: $_"
    } finally {
        Remove-Item $zipDest -ErrorAction SilentlyContinue
        Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# -- 6. Create Launcher & Path Setup -----------------------------------------
Write-Step 6 "Creating hopit-cli launcher & environment variables"

$launcher = "$hopitDir\hopit-cli.bat"
$pyLauncherExe = if ($useSystem) { "%LOCALAPPDATA%\hopit-cli\venv\Scripts\python.exe" } else { "%LOCALAPPDATA%\hopit-cli\python\python.exe" }

$launcherContent = @"
@echo off
"$pyLauncherExe" "%LOCALAPPDATA%\hopit-cli\src\hopit-cli.py" %*
"@
$launcherContent | Set-Content $launcher -Encoding ASCII

$userPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath -notlike "*$hopitDir*") {
    [System.Environment]::SetEnvironmentVariable("PATH", "$userPath;$hopitDir", "User")
    Write-Ok "hopit-cli directory added to User PATH"
} else {
    Write-Ok "hopit-cli already in PATH"
}

Refresh-Path

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Green
Write-Host "   All done! Open a NEW terminal window and run:" -ForegroundColor Green
Write-Host ""
Write-Host "       hopit-cli" -ForegroundColor White
Write-Host ""
Write-Host "   (Close any open terminal windows to reload PATH)" -ForegroundColor DarkGray
Write-Host "  ============================================" -ForegroundColor Green
Write-Host ""
