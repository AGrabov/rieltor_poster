# Makes the dashboard launch work WITHOUT the frozen RieltorDashboard.exe.
#
# Why not the .exe: a frozen PyInstaller binary, when it spawns the venv python
# (a different Python build than the one baked into the .exe), makes that python
# load incompatible DLLs and die silently — no window, no log. The bug is the
# FROZEN PARENT, not what it launches. The exact same updater.py, run BY the venv
# python instead, updates (git pull + uv sync) and launches the dashboard fine.
#
# This script REPOINTS the existing Desktop / Start-Menu shortcut(s) that point at
# RieltorDashboard.exe so they run:  .venv\Scripts\python.exe updater.py
# (same icon, same double-click, now working + auto-updating). If no such shortcut
# exists, it creates "Rieltor Dashboard.lnk" on the Desktop. Then it launches once.

$ErrorActionPreference = 'Stop'
$root    = $PSScriptRoot
$py      = Join-Path $root '.venv\Scripts\python.exe'
$icon    = Join-Path $root 'assets\icon.ico'
$exeName = 'RieltorDashboard.exe'

if (-not (Test-Path $py)) {
    Write-Host "ERROR: $py not found. Run 'uv sync' in this folder first, then re-run."
    Read-Host 'Press Enter to close'
    exit 1
}

$ws = New-Object -ComObject WScript.Shell

function Set-DashShortcut($lnkPath) {
    $sc                  = $ws.CreateShortcut($lnkPath)
    $sc.TargetPath       = $py
    $sc.Arguments        = 'updater.py'
    $sc.WorkingDirectory = $root
    $sc.WindowStyle      = 7              # minimized
    if (Test-Path $icon) { $sc.IconLocation = $icon }
    $sc.Description      = 'Rieltor Dashboard'
    $sc.Save()
}

# 1) Repoint any existing shortcut that targets the old .exe (keep its name/place).
$dirs = @(
    [Environment]::GetFolderPath('Desktop'),
    [Environment]::GetFolderPath('Programs'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Rieltor')
) | Where-Object { Test-Path $_ }

$found = $false
foreach ($dir in $dirs) {
    Get-ChildItem -Path $dir -Filter *.lnk -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
        $existing = $ws.CreateShortcut($_.FullName)
        if ($existing.TargetPath -like "*$exeName") {
            Set-DashShortcut $_.FullName
            Write-Host "Repointed existing shortcut: $($_.FullName)"
            $found = $true
        }
    }
}

# 2) None found -> create a fresh Desktop shortcut.
if (-not $found) {
    $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Rieltor Dashboard.lnk'
    Set-DashShortcut $lnk
    Write-Host "Created Desktop shortcut: $lnk"
}

# 3) Launch right now so it's visible that it works.
Start-Process -FilePath $py -ArgumentList 'updater.py' -WorkingDirectory $root -WindowStyle Minimized
Write-Host 'Dashboard starting... a splash, then a control window and the browser will appear.'
Start-Sleep -Seconds 2
