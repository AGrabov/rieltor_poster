# ==============================================================
# setup.ps1  --  Bootstrap script for Rieltor Automation
# ==============================================================
# Що робить:
#   1. Встановлює uv (якщо не встановлено)
#   2. Встановлює Python 3.14 через uv
#   3. Клонує репозиторій (якщо запущено поза ним)
#   4. Створює venv і встановлює залежності
#   5. Встановлює браузер Chromium для Playwright
#   6. Створює .env з .env.example (якщо .env не існує)
#
# Використання:
#   .\setup.ps1
#   .\setup.ps1 -RepoDir "C:\Projects\rieltor"
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
        Write-Host "`n[FAIL] Команда завершилась з кодом $LASTEXITCODE" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# -- 1. uv -----------------------------------------------------

Write-Step "Перевірка uv"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Warn "uv не знайдено -- встановлюю..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    Assert-Exit

    $uvBin = "$env:USERPROFILE\.local\bin"
    if ($env:PATH -notlike "*$uvBin*") {
        $env:PATH = "$uvBin;$env:PATH"
    }
    Write-OK "uv встановлено: $(uv --version)"
} else {
    Write-OK "uv вже встановлено: $(uv --version)"
}

# -- 2. Python -------------------------------------------------

Write-Step "Перевірка Python $PyVer"

$pyPath = uv python find $PyVer 2>$null
if (-not $pyPath) {
    Write-Warn "Python $PyVer не знайдено -- встановлюю через uv..."
    uv python install $PyVer; Assert-Exit
    Write-OK "Python $PyVer встановлено"
} else {
    Write-OK "Python $PyVer вже доступний: $pyPath"
}

# -- 3. Репозиторій --------------------------------------------

Write-Step "Перевірка репозиторію"

if (-not (Test-Path (Join-Path $RepoDir ".git"))) {
    Write-Warn "Репозиторій не знайдено в $RepoDir -- клоную..."
    $parent = Split-Path $RepoDir -Parent
    $folder = Split-Path $RepoDir -Leaf
    git clone $RepoUrl (Join-Path $parent $folder); Assert-Exit
    Write-OK "Репозиторій клоновано в $RepoDir"
} else {
    Write-OK "Репозиторій вже існує: $RepoDir"
}

Set-Location $RepoDir

# -- 4. venv + залежності --------------------------------------

Write-Step "Створення venv і встановлення залежностей"

uv sync; Assert-Exit
Write-OK "Залежності встановлено"

# -- 5. Playwright -- Chromium ---------------------------------

Write-Step "Встановлення браузера Chromium для Playwright"

uv run playwright install chromium; Assert-Exit
Write-OK "Chromium встановлено"

# -- 6. .env ---------------------------------------------------

Write-Step "Налаштування .env"

$envFile    = Join-Path $RepoDir ".env"
$envExample = Join-Path $RepoDir ".env.example"

if (Test-Path $envFile) {
    Write-OK ".env вже існує -- пропускаю"
} elseif (Test-Path $envExample) {
    Copy-Item $envExample $envFile
    Write-Warn ".env створено з .env.example -- заповніть змінні у файлі .env"
} else {
    Write-Warn ".env.example не знайдено -- створіть .env вручну (див. README.md)"
}

# -- Готово ----------------------------------------------------

Write-Host @"

==============================================================
  Встановлення завершено!

  Наступні кроки:
    1. Відкрийте .env і заповніть CRM_EMAIL, CRM_PASSWORD,
       PHONE, PASSWORD та інші змінні.
    2. Запустіть дашборд:
         uv run streamlit run dashboard.py
       або CLI:
         uv run python main.py collect
         uv run python main.py post
==============================================================
"@ -ForegroundColor Green
