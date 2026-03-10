# Rieltor Automation

Автоматизація публікації нерухомості: збір об'єктів з CRM → парсинг → збереження в БД → публікація на [Rieltor.ua](https://rieltor.ua).

## Як це працює

**Фаза 1 (collect):** авторизація в CRM через Playwright → збір списку об'єктів → парсинг HTML-карток → завантаження фото → збереження в SQLite.

**Фаза 2 (post):** зчитування непоопрацьованих записів з БД → авторизація на Rieltor.ua → заповнення форми → збереження чернетки або публікація.

## Встановлення

```bash
uv sync
uv run playwright install chromium
```

Скопіюйте `.env.example` → `.env` і заповніть змінні:

```env
# CRM credentials
CRM_EMAIL=your@email.com
CRM_PASSWORD=yourpassword

# Rieltor.ua credentials
PHONE=+380XXXXXXXXX
PASSWORD=yourpassword

# Комісія (підставляється в оголошення)
COMMISSION_SALE=3
COMMISSION_SALE_UNIT=%
COMMISSION_RENT=50
COMMISSION_RENT_UNIT=%

LOG_LEVEL=INFO
```

## Використання

```bash
# Повний пайплайн: збір + публікація чернетки
python main.py

# Тільки збір (Фаза 1)
python main.py collect
python main.py collect --max-pages 2 --max-count 10

# Тільки публікація (Фаза 2)
python main.py post
python main.py post --publish               # одразу публікує (не чернетка)
python main.py post --deal-type sell --max-count 5

# Публікація одного об'єкта з JSON-файлу або рядка
python main.py post-one offer.json
python main.py post-one '{"Ціна": "100000", ...}'

# Показати вікно браузера (не headless)
python main.py --no-headless collect

# Debug-режим
python main.py --debug post
```

## Структура проєкту

```text
main.py                   # CLI та оркестрація пайплайну
offer_db.py               # SQLite-обгортка (OfferDB)
setup_logger.py           # Налаштування логування

crm_data_parser/
  crm_session.py          # Playwright-сесія для CRM
  estate_list_collector.py  # Збір списку об'єктів
  html_parser.py          # Парсинг HTML-картки об'єкта
  field_extractor.py      # Витяг окремих полів
  description_analyzer.py # NLP-аналіз опису (spaCy)
  photo_downloader.py     # Завантаження фото

rieltor_handler/
  rieltor_session.py      # Playwright-сесія для Rieltor.ua
  rieltor_offer_poster.py # Головний постер
  new_offer_poster/       # Заповнення форми нового оголошення

schemas/                  # JSON-схеми форм оголошень
offers/                   # Локальні фото об'єктів
logs/                     # Лог-файли
```

## Залежності

Управління залежностями через [uv](https://github.com/astral-sh/uv).

- `playwright` + `selenium` — браузерна автоматизація
- `beautifulsoup4` — парсинг HTML
- `spacy` — NLP-аналіз описів
- `python-dotenv` — змінні середовища
- `colorlog` — кольорові логи
- `pillow` — робота з зображеннями
