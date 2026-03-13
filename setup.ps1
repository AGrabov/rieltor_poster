# ==============================================================
# setup.ps1  --  Bootstrap script for Rieltor Automation
# ==============================================================
# Steps:
#   1. Install uv (if not installed)
#   2. Install Python 3.14 via uv
#   3. Clone repository (if not already cloned)
#   4. Create venv and install dependencies
#   5. Install Chromium browser for Playwright
#   6. Create .env from .env.example (if .env does not exist)
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File setup.ps1
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -RepoDir "C:\Projects\rieltor"
# ==============================================================

param(
    [string]$RepoDir  = "$PSScriptRoot",
    [string]$RepoUrl  = "https://github.com/AGrabov/rieltor_poster.git",
    [string]$PyVer    = "3.14"
)

$ErrorActionPreference = "Stop"

# -- Helpers ---------------------------------------------------

function Write-Step([string]$msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}

function Write-OK([string]$msg) {
    Write-Host "    [OK] $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "    [!]  $msg" -ForegroundColor Yellow
}

function Assert-Exit {
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`n[FAIL] Last command exited with code $LASTEXITCODE" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# -- 1. uv -----------------------------------------------------

Write-Step "Checking uv"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Warn "uv not found -- installing..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    Assert-Exit

    $uvBin = "$env:USERPROFILE\.local\bin"
    if ($env:PATH -notlike "*$uvBin*") {
        $env:PATH = "$uvBin;$env:PATH"
    }
    Write-OK "uv installed: $(uv --version)"
} else {
    Write-OK "uv already installed: $(uv --version)"
}

# -- 2. Python -------------------------------------------------

Write-Step "Checking Python $PyVer"

$pyPath = uv python find $PyVer 2>$null
if (-not $pyPath) {
    Write-Warn "Python $PyVer not found -- installing via uv..."
    uv python install $PyVer; Assert-Exit
    Write-OK "Python $PyVer installed"
} else {
    Write-OK "Python $PyVer already available: $pyPath"
}

# -- 3. Repository ---------------------------------------------

Write-Step "Checking repository"

if (-not (Test-Path (Join-Path $RepoDir ".git"))) {
    Write-Warn "Repository not found in $RepoDir -- cloning..."
    $parent = Split-Path $RepoDir -Parent
    $folder = Split-Path $RepoDir -Leaf
    git clone $RepoUrl (Join-Path $parent $folder); Assert-Exit
    Write-OK "Repository cloned to $RepoDir"
} else {
    Write-OK "Repository already exists: $RepoDir"
}

Set-Location $RepoDir

# -- 4. venv + dependencies ------------------------------------

Write-Step "Creating venv and installing dependencies"

uv sync; Assert-Exit
Write-OK "Dependencies installed"

# -- 5. Playwright -- Chromium ---------------------------------

Write-Step "Installing Chromium for Playwright"

uv run playwright install chromium; Assert-Exit
Write-OK "Chromium installed"

# -- 6. .env ---------------------------------------------------

Write-Step "Setting up .env"

$envFile    = Join-Path $RepoDir ".env"
$envExample = Join-Path $RepoDir ".env.example"

if (Test-Path $envFile) {
    Write-OK ".env already exists -- skipping"
} elseif (Test-Path $envExample) {
    Copy-Item $envExample $envFile
    Write-Warn ".env created from .env.example -- fill in your credentials"
} else {
    Write-Warn ".env.example not found -- create .env manually (see README.md)"
}

# -- Done ------------------------------------------------------

Write-Host @"

==============================================================
  Setup complete!

  Next steps:
    1. Open .env and fill in CRM_EMAIL, CRM_PASSWORD,
       PHONE, PASSWORD and other variables.
    2. Launch the dashboard:
         uv run streamlit run dashboard.py
       or use CLI:
         uv run python main.py collect
         uv run python main.py post
==============================================================
"@ -ForegroundColor Green
