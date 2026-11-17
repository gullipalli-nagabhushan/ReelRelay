# Creates a ReelRelay shortcut on your desktop.
# Run this once. After that, double-click the ReelRelay icon to start the app.

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launcher = Join-Path $Root 'run-reelrelay.bat'
$Icon = Join-Path $Root 'static\img\reelrelay.ico'

Write-Host ""
Write-Host "  ReelRelay shortcut setup" -ForegroundColor Magenta
Write-Host ""

if (-not (Test-Path -LiteralPath $Launcher)) {
    Write-Host "  ERROR: run-reelrelay.bat was not found next to this script." -ForegroundColor Red
    Read-Host "  Press Enter to close"
    exit 1
}

try {
    $Desktop = [Environment]::GetFolderPath('Desktop')
    if (-not $Desktop -or -not (Test-Path -LiteralPath $Desktop)) {
        throw 'The Desktop folder could not be located.'
    }

    $ShortcutPath = Join-Path $Desktop 'ReelRelay.lnk'
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)

    $Shortcut.TargetPath = $Launcher
    $Shortcut.WorkingDirectory = $Root
    $Shortcut.Description = 'Start ReelRelay and open it in your browser'
    $Shortcut.WindowStyle = 1  # normal window: shows progress and keeps errors visible

    if (Test-Path -LiteralPath $Icon) {
        $Shortcut.IconLocation = "$Icon,0"
    }

    $Shortcut.Save()

    Write-Host "  Done. A ReelRelay icon is now on your desktop:" -ForegroundColor Green
    Write-Host "  $ShortcutPath"
    Write-Host ""
    Write-Host "  Double-click it whenever you want to download something."
    Write-Host "  The first launch may take a minute while Python packages install."
} catch {
    Write-Host "  ERROR: The shortcut could not be created: $($_.Exception.Message)" -ForegroundColor Red
    Read-Host "  Press Enter to close"
    exit 1
}

Write-Host ""
Read-Host "  Press Enter to close"
