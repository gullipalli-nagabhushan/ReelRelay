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
$MinPython = '3.11'

function Test-PythonVersion([string]$Exe) {
    # Native commands write to stderr, and $ErrorActionPreference = 'Stop' turns
    # redirected stderr into a terminating error. Relax it for this probe.
    $Previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $Exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
    $Result = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $Previous
    return $Result
}

function Test-Dependencies([string]$Exe) {
    $Previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $Exe -c "import fastapi, uvicorn, yt_dlp" *> $null
    $Result = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $Previous
    return $Result
}

function Test-FreeThreaded([string]$Exe) {
    # Free-threaded builds ("3.13t") have no prebuilt wheels for pydantic-core,
    # httptools, watchfiles or pyyaml, so pip would need a C/Rust toolchain.
    $Previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $Value = (& $Exe -c "import sysconfig; print(int(sysconfig.get_config_var('Py_GIL_DISABLED') or 0))" 2>$null) |
        Select-Object -Last 1
    $ErrorActionPreference = $Previous
    return ("$Value".Trim() -eq '1')
}

function Remove-Venv {
    Remove-Item -LiteralPath (Join-Path $Root '.venv') -Recurse -Force -ErrorAction SilentlyContinue
}

# Creates .venv from the newest standard (GIL) Python that is 3.11 or newer.
# Returns $true when a usable environment exists.
function Initialize-Venv {
    $Previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'

    $Candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $Listed = (& py -0p 2>$null) | Out-String
        $Versions = @()
        foreach ($Line in ($Listed -split "`r?`n")) {
            if ($Line -match '-V:(\d+\.\d+)(t?)') {
                if ($Matches[2] -ne 't') { $Versions += $Matches[1] }
            }
        }
        $Versions = $Versions | Sort-Object -Unique |
            ForEach-Object { [pscustomobject]@{ Tag = $_; Number = [version]$_ } } |
            Sort-Object Number -Descending |
            ForEach-Object { $_.Tag }
        foreach ($Tag in $Versions) { $Candidates += , @('py', "-$Tag") }
    }
    $Candidates += , @('python', $null)

    foreach ($Candidate in $Candidates) {
        $Exe = $Candidate[0]
        $VersionFlag = $Candidate[1]
        if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { continue }

        if ($VersionFlag) {
            & py $VersionFlag -m venv .venv
        } else {
            & $Exe -m venv .venv
        }
        if (-not (Test-Path -LiteralPath $Python)) { Remove-Venv; continue }
        if (-not (Test-PythonVersion $Python)) { Remove-Venv; continue }
        if (Test-FreeThreaded $Python) { Remove-Venv; continue }

        $ErrorActionPreference = $Previous
        return $true
    }

    Remove-Venv
    $ErrorActionPreference = $Previous
    return $false
}

# An environment created earlier may use a Python that current yt-dlp releases
# no longer support, or a free-threaded build without wheels. Rebuild it.
if ((Test-Path -LiteralPath $Python) -and (-not (Test-PythonVersion $Python) -or (Test-FreeThreaded $Python))) {
    Write-Step 'Rebuilding the Python environment...'
    Remove-Venv
}

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Step 'First run: creating the Python environment...'
    if (-not (Initialize-Venv)) {
        Fail "No usable Python was found. Install Python $MinPython or newer (the standard build, not the free-threaded one) from https://www.python.org/downloads/, tick ""Add Python to PATH"", then run this launcher again."
    }
}

# --------------------------------------------------------------------------- #
# 2. Make sure the dependencies are installed
# --------------------------------------------------------------------------- #

if (-not (Test-Dependencies $Python)) {
    Write-Step 'Installing the required packages (only the first time)...'
    $Previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $Python -m pip install --disable-pip-version-check --quiet --upgrade pip
    & $Python -m pip install --disable-pip-version-check --quiet -r requirements.txt
    $InstallExit = $LASTEXITCODE
    $ErrorActionPreference = $Previous
    if ($InstallExit -ne 0) {
        Fail 'Installing the required packages failed. Check your internet connection, then run this launcher again.'
    }
}

# --------------------------------------------------------------------------- #
# 3. Check ffmpeg (optional, but needed for 8K and audio conversion)
# --------------------------------------------------------------------------- #

$Previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$Ffmpeg = (& $Python -c "import shutil; print(shutil.which('ffmpeg') or '')" 2>$null)
$ErrorActionPreference = $Previous
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
