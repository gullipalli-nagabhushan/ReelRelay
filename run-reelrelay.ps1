# ReelRelay launcher for Windows.
# Starts the local app, then opens it in your default browser.
# Close this window (or press Ctrl+C) to stop ReelRelay.

[CmdletBinding()]
param(
    [int]$Port = 8080,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

function Write-Step([string]$Message) {
    Write-Host "  $Message" -ForegroundColor Cyan
}

function Write-Warn([string]$Message) {
    Write-Host "  WARNING: $Message" -ForegroundColor Yellow
}

function Fail([string]$Message) {
    Write-Host ""
    Write-Host "  ERROR: $Message" -ForegroundColor Red
    Write-Host ""
    Read-Host "  Press Enter to close"
    exit 1
}

try {
    $Host.UI.RawUI.WindowTitle = 'ReelRelay - close this window to stop'
} catch {
    # Window title is cosmetic; ignore failures in unusual consoles.
}

Write-Host ""
Write-Host "  ReelRelay 3.0.0" -ForegroundColor Magenta
Write-Host "  Local video and audio downloader" -ForegroundColor DarkGray
Write-Host ""

# --------------------------------------------------------------------------- #
# 1. Find or create the Python environment
# --------------------------------------------------------------------------- #

$Python = Join-Path $Root '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Step 'First run: creating the Python environment...'
    $Bootstrap = $null
    $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($PyLauncher) {
        & py -3 -m venv .venv
    } else {
        $Bootstrap = Get-Command python -ErrorAction SilentlyContinue
        if (-not $Bootstrap) {
            Fail 'Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/ and tick "Add Python to PATH", then try again.'
        }
        & python -m venv .venv
    }
    if (-not (Test-Path -LiteralPath $Python)) {
        Fail 'The Python environment could not be created. Install Python 3.10+ and try again.'
    }
}

& $Python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
if ($LASTEXITCODE -ne 0) {
    Fail 'Python 3.10 or newer is required. Install a newer Python, delete the .venv folder, then run this launcher again.'
}

# --------------------------------------------------------------------------- #
# 2. Make sure the dependencies are installed
# --------------------------------------------------------------------------- #

& $Python -c "import fastapi, uvicorn, yt_dlp" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Step 'Installing the required packages (only the first time)...'
    & $Python -m pip install --disable-pip-version-check --quiet --upgrade pip
    & $Python -m pip install --disable-pip-version-check --quiet -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Fail 'Installing the required packages failed. Check your internet connection, then run this launcher again.'
    }
}

# --------------------------------------------------------------------------- #
# 3. Check ffmpeg (optional, but needed for 8K and audio conversion)
# --------------------------------------------------------------------------- #

$Ffmpeg = (& $Python -c "import shutil; print(shutil.which('ffmpeg') or '')" 2>$null)
if (-not $Ffmpeg) {
    Write-Warn 'ffmpeg was not found. 8K merging and MP3/FLAC/M4A/WAV conversion will be disabled.'
    Write-Warn 'Install it with:  winget install Gyan.FFmpeg   then restart ReelRelay.'
    Write-Host ""
}

# --------------------------------------------------------------------------- #
# 4. Reuse a running copy, otherwise pick a free port
# --------------------------------------------------------------------------- #

function Get-ReelRelayUrl([int]$Candidate) {
    try {
        $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Candidate/health" -TimeoutSec 1
        if ($Health.app -eq 'ReelRelay') {
            return "http://127.0.0.1:$Candidate/"
        }
    } catch {
        return $null
    }
    return $null
}

function Test-PortBusy([int]$Candidate) {
    $Client = New-Object System.Net.Sockets.TcpClient
    try {
        $Async = $Client.BeginConnect('127.0.0.1', $Candidate, $null, $null)
        if (-not $Async.AsyncWaitHandle.WaitOne(250)) { return $false }
        $Client.EndConnect($Async)
        return $true
    } catch {
        return $false
    } finally {
        $Client.Close()
    }
}

$Existing = Get-ReelRelayUrl 8080
if ($Existing) {
    Write-Step 'ReelRelay is already running. Opening it in your browser...'
    if (-not $NoBrowser) { Start-Process $Existing }
    Start-Sleep -Seconds 2
    exit 0
}

$StartPort = $Port
$Tries = 0
while ((Test-PortBusy $Port) -and $Tries -lt 20) {
    $Port++
    $Tries++
}
if (Test-PortBusy $Port) {
    Fail "No free port was found between $StartPort and $($StartPort + 20). Close other apps and try again."
}

$Url = "http://127.0.0.1:$Port/"

# --------------------------------------------------------------------------- #
# 5. Open the browser once the app is healthy, then run the app
# --------------------------------------------------------------------------- #

if (-not $NoBrowser) {
    $BrowserJob = Start-Job -ArgumentList $Url -ScriptBlock {
        param($Target)
        for ($i = 0; $i -lt 120; $i++) {
            try {
                $Response = Invoke-WebRequest -Uri ($Target + 'health') -UseBasicParsing -TimeoutSec 2
                if ($Response.StatusCode -eq 200) {
                    Start-Process $Target
                    return
                }
            } catch {
                # The app is still starting.
            }
            Start-Sleep -Milliseconds 500
        }
    }
}

Write-Step "Starting ReelRelay at $Url"
Write-Step 'Keep this window open while you use the app. Press Ctrl+C to stop.'
Write-Host ""

try {
    & $Python -m uvicorn main:app --host 127.0.0.1 --port $Port
} finally {
    if ($BrowserJob) {
        Stop-Job $BrowserJob -ErrorAction SilentlyContinue
        Remove-Job $BrowserJob -Force -ErrorAction SilentlyContinue
    }
    Write-Host ""
    Write-Host "  ReelRelay has stopped. You can close this window." -ForegroundColor DarkGray
}
