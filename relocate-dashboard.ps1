# Moves the install OUT of C:\Program Files (write-protected) into a user-writable
# folder, so the dashboard needs no admin / no UAC and auto-update (git pull, uv
# sync) + logs + offers.db all just work.
#
# SAFE: it COPIES (robocopy), it does not delete the original. If anything goes
# wrong the old Program Files install (with its admin shortcut) still works.
#
# Run once: double-click relocate-dashboard.bat. Close the running dashboard first
# so its files aren't locked. Afterwards use the new Desktop shortcut (no admin).

$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
$dst = Join-Path $env:LOCALAPPDATA 'RieltorPoster\rieltor'

Write-Host "Source: $src"
Write-Host "Target: $dst"
Write-Host ''

# 1) Copy everything (incl .venv, .git, .env, offers.db) to the writable target.
#    Skip regenerable caches/build artifacts to save time.
New-Item -ItemType Directory -Force -Path $dst | Out-Null
robocopy $src $dst /E /XD '__pycache__' '.ruff_cache' '.pytest_cache' 'build' /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) {
    Write-Host "ERROR: robocopy failed (code $LASTEXITCODE). Is the dashboard still running / a file locked?"
    Read-Host 'Press Enter to close'
    exit 1
}

$py = Join-Path $dst '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host "ERROR: venv was not copied: $py"
    Read-Host 'Press Enter to close'
    exit 1
}

# Verify the writable, gitignored files made it across (these are the whole point:
# git pull would NOT carry them, robocopy does). Warn loudly if missing/locked.
foreach ($f in @('offers.db', '.env')) {
    if (Test-Path (Join-Path $dst $f)) {
        Write-Host "OK copied: $f"
    } elseif (Test-Path (Join-Path $src $f)) {
        Write-Host "WARNING: $f was NOT copied (likely locked). Close the dashboard and re-run."
    } else {
        Write-Host "NOTE: $f does not exist in the source - skipping."
    }
}

# 2) Verify the copied venv actually runs at the new path; repair with uv sync if not.
& $py -c 'import streamlit' 1>$null 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Repairing venv at the new location (uv sync)...'
    Push-Location $dst
    try { uv sync | Out-Null } catch { Write-Host "uv sync note: $_" }
    Pop-Location
}

# 3) Fresh Desktop shortcut to the NEW location (no "run as admin" flag).
#    Remove any old shortcuts that pointed into Program Files or to the .exe.
$ws      = New-Object -ComObject WScript.Shell
$icon    = Join-Path $dst 'assets\icon.ico'
$desktop = [Environment]::GetFolderPath('Desktop')

Get-ChildItem -Path $desktop -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object {
    $s = $ws.CreateShortcut($_.FullName)
    if ($s.TargetPath -like "$src*" -or $s.TargetPath -like '*RieltorDashboard.exe') {
        Remove-Item $_.FullName -Force
        Write-Host "Removed old shortcut: $($_.Name)"
    }
}

$lnk = Join-Path $desktop 'Rieltor Dashboard.lnk'
$sc                  = $ws.CreateShortcut($lnk)
$sc.TargetPath       = $py
$sc.Arguments        = 'updater.py'
$sc.WorkingDirectory = $dst
$sc.WindowStyle      = 7
if (Test-Path $icon) { $sc.IconLocation = $icon }
$sc.Description      = 'Rieltor Dashboard'
$sc.Save()
Write-Host "New Desktop shortcut (no admin needed): $lnk"

# 4) Launch from the new location so it's clearly working.
Start-Process -FilePath $py -ArgumentList 'updater.py' -WorkingDirectory $dst -WindowStyle Minimized
Write-Host ''
Write-Host "Done. Dashboard now runs from: $dst"
Write-Host "The old 'C:\Program Files\RieltorPoster' folder can be deleted later (needs admin) - optional."
Start-Sleep -Seconds 2
