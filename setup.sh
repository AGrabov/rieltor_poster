#!/usr/bin/env bash
# ============================================================
# setup.sh  —  Bootstrap script for Rieltor Automation
# ============================================================
# Что делает:
#   1. Устанавливает uv (если не установлен)
#   2. Устанавливает Python 3.12 через uv
#   3. Клонирует репозиторий (если запущен вне него)
#   4. Создаёт venv и устанавливает зависимости
#   5. Устанавливает браузер Chromium для Playwright
#   6. Создаёт .env из .env.example (если .env не существует)
#
# Использование:
#   bash setup.sh
#   bash setup.sh --repo-dir ~/projects/rieltor
# ============================================================

set -euo pipefail

REPO_URL="https://github.com/AGrabov/rieltor_poster.git"
PY_VER="3.14"
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

# ── Параметры ────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo-dir) REPO_DIR="$2"; shift 2 ;;
        --py-ver)   PY_VER="$2";   shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ── Helpers ──────────────────────────────────────────────────────────

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RESET='\033[0m'

step() { echo -e "\n${CYAN}==> $1${RESET}"; }
ok()   { echo -e "    ${GREEN}[OK]${RESET} $1"; }
warn() { echo -e "    ${YELLOW}[!] ${RESET} $1"; }

# ── 1. uv ────────────────────────────────────────────────────────────

step "Проверка uv"

if ! command -v uv &>/dev/null; then
    warn "uv не найден — устанавливаю..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Подключить к текущей сессии
    export PATH="$HOME/.local/bin:$PATH"
    ok "uv установлен: $(uv --version)"
else
    ok "uv уже установлен: $(uv --version)"
fi

# ── 2. Python ────────────────────────────────────────────────────────

step "Проверка Python $PY_VER"

if ! uv python find "$PY_VER" &>/dev/null; then
    warn "Python $PY_VER не найден — устанавливаю через uv..."
    uv python install "$PY_VER"
    ok "Python $PY_VER установлен"
else
    ok "Python $PY_VER уже доступен: $(uv python find "$PY_VER")"
fi

# ── 3. Репозиторий ───────────────────────────────────────────────────

step "Проверка репозитория"

if [[ ! -d "$REPO_DIR/.git" ]]; then
    warn "Репозиторий не найден в $REPO_DIR — клонирую..."
    git clone "$REPO_URL" "$REPO_DIR"
    ok "Репозиторий клонирован в $REPO_DIR"
else
    ok "Репозиторий уже существует: $REPO_DIR"
fi

cd "$REPO_DIR"

# ── 4. venv + зависимости ────────────────────────────────────────────

step "Создание venv и установка зависимостей"

uv sync
ok "Зависимости установлены"

# ── 5. Playwright — Chromium ─────────────────────────────────────────

step "Установка браузера Chromium для Playwright"

# На Linux нужны системные зависимости
if [[ "$(uname)" == "Linux" ]]; then
    warn "Linux: устанавливаю системные зависимости Playwright..."
    uv run playwright install-deps chromium
fi

uv run playwright install chromium
ok "Chromium установлен"

# ── 6. .env ──────────────────────────────────────────────────────────

step "Настройка .env"

if [[ -f "$REPO_DIR/.env" ]]; then
    ok ".env уже существует — пропускаю"
elif [[ -f "$REPO_DIR/.env.example" ]]; then
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    warn ".env создан из .env.example — заполните переменные в файле .env"
else
    warn ".env.example не найден — создайте .env вручную (см. README.md)"
fi

# ── Готово ───────────────────────────────────────────────────────────

echo -e "
${GREEN}============================================================
  Установка завершена!

  Следующие шаги:
    1. Откройте .env и заполните CRM_EMAIL, CRM_PASSWORD,
       PHONE, PASSWORD и другие переменные.
    2. Запустите дашборд:
         uv run streamlit run dashboard.py
       или CLI:
         uv run python main.py collect
         uv run python main.py post
============================================================${RESET}
"
