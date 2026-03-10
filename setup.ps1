# ============================================================
# setup.ps1  —  Bootstrap script for Rieltor Automation
# ============================================================
# Что делает:
#   1. Устанавливает uv (если не установлен)
#   2. Устанавливает Python 3.14 через uv
#   3. Клонирует репозиторий (если запущен вне него)
#   4. Создаёт venv и устанавливает зависимости
#   5. Устанавливает браузер Chromium для Playwright
#   6. Создаёт .env из .env.example (если .env не существует)
#
# Использование:
#   .\setup.ps1
#   .\setup.ps1 -RepoDir "C:\Projects\rieltor"
# ============================================================

param(
    [string]$RepoDir  = "$PSScriptRoot",
    [string]$RepoUrl  = "https://github.com/AGrabov/rieltor_poster.git",
    [string]$PyVer    = "3.14"
)

$ErrorActionPreference = "Stop"

# ── Helpers ──────────────────────────────────────────────────────────

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
        Write-Host "`n[FAIL] Последняя команда завершилась с кодом $LASTEXITCODE" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# ── 1. uv ────────────────────────────────────────────────────────────

Write-Step "Проверка uv"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Warn "uv не найден — устанавливаю..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    Assert-Exit

    # Обновить PATH для текущей сессии
    $uvBin = "$env:USERPROFILE\.local\bin"
    if ($env:PATH -notlike "*$uvBin*") {
        $env:PATH = "$uvBin;$env:PATH"
    }
    Write-OK "uv установлен: $(uv --version)"
} else {
    Write-OK "uv уже установлен: $(uv --version)"
}

# ── 2. Python ────────────────────────────────────────────────────────

Write-Step "Проверка Python $PyVer"

$pyPath = uv python find $PyVer 2>$null
if (-not $pyPath) {
    Write-Warn "Python $PyVer не найден — устанавливаю через uv..."
    uv python install $PyVer; Assert-Exit
    Write-OK "Python $PyVer установлен"
} else {
    Write-OK "Python $PyVer уже доступен: $pyPath"
}

# ── 3. Репозиторий ───────────────────────────────────────────────────

Write-Step "Проверка репозитория"

# Если запущен не из папки с .git — клонируем
if (-not (Test-Path (Join-Path $RepoDir ".git"))) {
    Write-Warn "Репозиторий не найден в $RepoDir — клонирую..."
    $parent = Split-Path $RepoDir -Parent
    $folder = Split-Path $RepoDir -Leaf
    git clone $RepoUrl (Join-Path $parent $folder); Assert-Exit
    Write-OK "Репозиторий клонирован в $RepoDir"
} else {
    Write-OK "Репозиторий уже существует: $RepoDir"
}

Set-Location $RepoDir

# ── 4. Виртуальное окружение + зависимости ───────────────────────────

Write-Step "Создание venv и установка зависимостей"

uv sync; Assert-Exit
Write-OK "Зависимости установлены"

# ── 5. Playwright — Chromium ─────────────────────────────────────────

Write-Step "Установка браузера Chromium для Playwright"

uv run playwright install chromium; Assert-Exit
Write-OK "Chromium установлен"

# ── 6. .env ──────────────────────────────────────────────────────────

Write-Step "Настройка .env"

$envFile    = Join-Path $RepoDir ".env"
$envExample = Join-Path $RepoDir ".env.example"

if (Test-Path $envFile) {
    Write-OK ".env уже существует — пропускаю"
} elseif (Test-Path $envExample) {
    Copy-Item $envExample $envFile
    Write-Warn ".env создан из .env.example — заполните переменные в файле .env"
} else {
    Write-Warn ".env.example не найден — создайте .env вручную (см. README.md)"
}

# ── Готово ───────────────────────────────────────────────────────────

Write-Host @"

============================================================
  Установка завершена!

  Следующие шаги:
    1. Откройте .env и заполните CRM_EMAIL, CRM_PASSWORD,
       PHONE, PASSWORD и другие переменные.
    2. Запустите дашборд:
         uv run streamlit run dashboard.py
       или CLI:
         uv run python main.py collect
         uv run python main.py post
============================================================
"@ -ForegroundColor Green
