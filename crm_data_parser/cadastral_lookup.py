"""Пошук кадастрового номера за адресою через kadastr.live та kadastrova-karta.com."""

from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from setup_logger import setup_logger

logger = setup_logger(__name__)

_CADNUM_RE = re.compile(r"^\d{10}:\d{2}:\d{3}:\d{4}$")
_SEARCH_URL = "https://kadastr.live/search/{}/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; rieltor-bot/1.0)"}
_KK_SEARCH_URL = "https://kadastrova-karta.com/search"
_KK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/vnd.turbo-stream.html, text/html",
    "Referer": "https://kadastrova-karta.com/",
}

# Schema types (rieltor.ua) that have a "Кадастровий номер" field
_CADASTRAL_SCHEMA_TYPES = frozenset({"будинок", "ділянка", "комерційна"})

# Street-type prefixes to strip before querying (Ukrainian abbreviations + full forms).
# More-specific alternatives listed before shorter ones to avoid partial matches
# (e.g. "пров." must come before "пр-т" so "пр" alone is never stripped).
_STREET_PREFIX_RE = re.compile(
    r"^\s*("
    r"провулок\.?\s*|пров\.?\s*|"
    r"проспект\.?\s*|просп\.?\s*|пр-т\.?\s*|"
    r"вулиця\.?\s*|вул\.?\s*|"
    r"бульвар\.?\s*|бул\.?\s*|"
    r"площа\.?\s*|пл\.?\s*|"
    r"шосе\.?\s*|шос\.?\s*|"
    r"набережна\.?\s*|наб\.?\s*|"
    r"тупик\.?\s*|туп\.?\s*|"
    r"дорога\.?\s*|дор\.?\s*|"
    r"мікрорайон\.?\s*|мкр\.?\s*|"
    r"квартал\.?\s*|кварт\.?\s*|кв-л\.?\s*"
    r")",
    re.IGNORECASE,
)


def _strip_street_prefix(street: str) -> str:
    """Прибрати скорочення типу вулиці ('вул.', 'пр-т', 'бул.' тощо)."""
    return _STREET_PREFIX_RE.sub("", street).strip()


def _search_raw(query: str) -> list[dict]:
    """Виконати один запит до kadastr.live; повернути список результатів (або [] при 404/помилці)."""
    try:
        url = _SEARCH_URL.format(requests.utils.quote(query, safe=""))
        resp = requests.get(url, timeout=8, headers=_HEADERS)
        if resp.status_code == 404:
            logger.debug("404 для '%s'", query)
            return []
        resp.raise_for_status()
        return resp.json().get("results") or []
    except Exception:
        logger.warning("Помилка запиту для '%s'", query, exc_info=True)
        return []


def _best_cadnum(results: list[dict], house: str) -> str | None:
    """Повернути найкращий кадастровий номер зі списку результатів.

    Якщо передано house — шукає запис, у якому текст будь-якого поля
    містить номер будинку (точний збіг має пріоритет).
    Якщо точного збігу немає — повертає перший валідний номер.
    """
    house_norm = house.strip().lower()
    first_valid: str | None = None
    for item in results:
        cadnum = (item.get("cadnum") or "").strip()
        if not _CADNUM_RE.match(cadnum):
            continue
        if first_valid is None:
            first_valid = cadnum
        if house_norm:
            item_text = " ".join(str(v) for v in item.values()).lower()
            if house_norm in item_text:
                return cadnum
    return first_valid


def _search_kadastrova_karta(query: str, house: str) -> str | None:
    """Пошук кадастрового номера через kadastrova-karta.com (fallback).

    Парсить Turbo Stream HTML відповідь — без Playwright.
    Повертає перший cadnum, де адреса містить номер будинку,
    або перший валідний cadnum якщо точного збігу немає.
    """
    try:
        resp = requests.get(
            _KK_SEARCH_URL,
            params={"q": query},
            headers=_KK_HEADERS,
            timeout=(5, 8),  # (connect, read)
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        house_norm = house.strip().lower()
        first_valid: str | None = None

        for a_tag in soup.select("a[data-action='search#linkClicked']"):
            cadnum_div = a_tag.select_one("div.font-bold")
            addr_div = a_tag.select_one("div.text-gray-500")
            if not cadnum_div:
                continue
            cadnum = cadnum_div.get_text(strip=True)
            if not _CADNUM_RE.match(cadnum):
                continue
            if first_valid is None:
                first_valid = cadnum
            if house_norm and addr_div:
                addr_text = addr_div.get_text(strip=True).lower()
                # Match ", 45/3" or ", 45 " — house at end or before comma
                if re.search(r"[,\s]" + re.escape(house_norm) + r"(\s*$|[,\s])", addr_text):
                    return cadnum

        return first_valid
    except requests.exceptions.Timeout:
        logger.debug("Timeout kadastrova-karta.com для '%s'", query)
        return None
    except Exception:
        logger.warning("Помилка kadastrova-karta.com для '%s'", query, exc_info=True)
        return None


def lookup_cadastral_number(city: str, street: str, house: str) -> str | None:
    """Знайти кадастровий номер ділянки за адресою.

    Стратегія (kadastr.live, потім kadastrova-karta.com як fallback):
      1. kadastr.live: місто + вулиця + будинок
      2. kadastr.live: місто + вулиця (без будинку)
      3. kadastr.live: вулиця + будинок (без міста)
      4. kadastrova-karta.com: місто + вулиця + будинок
      5. kadastrova-karta.com: місто + вулиця (без будинку)

    Args:
        city:   Назва міста або населеного пункту.
        street: Назва вулиці (без префіксу "вул." тощо).
        house:  Номер будинку.

    Returns:
        Рядок у форматі ``XXXXXXXXXX:XX:XXX:XXXX`` або ``None``, якщо не знайдено.
    """
    street_clean = _strip_street_prefix(street)
    city_clean = city.strip()
    # Normalize house: "45/3" → "45 3" so the slash doesn't break the URL path
    house_clean = house.strip().replace("/", " ").strip()
    house_orig = house.strip()

    def _try_live(query: str) -> str | None:
        return _best_cadnum(_search_raw(query), house_orig)

    # ── Кроки 1–3: kadastr.live ───────────────────────────────────────
    parts = [p for p in [city_clean, street_clean, house_clean] if p]
    if not parts:
        return None

    cadnum = _try_live(" ".join(parts))
    if cadnum:
        logger.debug("Знайдено kadastr.live (крок 1): %s", cadnum)
        return cadnum

    if house_orig:
        parts2 = [p for p in [city_clean, street_clean] if p]
        if parts2:
            cadnum = _try_live(" ".join(parts2))
            if cadnum:
                logger.debug("Знайдено kadastr.live (крок 2): %s", cadnum)
                return cadnum

        parts3 = [p for p in [street_clean, house_clean] if p]
        if len(parts3) > 1:
            cadnum = _try_live(" ".join(parts3))
            if cadnum:
                logger.debug("Знайдено kadastr.live (крок 3): %s", cadnum)
                return cadnum

    # ── Кроки 4–5: kadastrova-karta.com fallback ─────────────────────
    kk_queries = [
        " ".join(p for p in [city_clean, street_clean, house_orig] if p),
        " ".join(p for p in [city_clean, street_clean] if p),
    ]
    for q in kk_queries:
        if not q:
            continue
        cadnum = _search_kadastrova_karta(q, house_orig)
        if cadnum:
            logger.debug("Знайдено kadastrova-karta.com: %s (запит: '%s')", cadnum, q)
            return cadnum

    return None


def enrich_offer_data_with_cadastral(offer_data: dict) -> bool:
    """Додати кадастровий номер до offer_data["address"], якщо він відсутній.

    Перевіряє тип об'єкта через CRM_TYPE_TO_SCHEMA і пропускає типи,
    що не підтримують поле «Кадастровий номер» на rieltor.ua.

    Returns:
        True якщо кадастровий номер знайдено та записано, інакше False.
    """
    try:
        from .html_parser import CRM_TYPE_TO_SCHEMA  # package context
    except ImportError:
        from html_parser import CRM_TYPE_TO_SCHEMA  # noqa: I001  # direct run fallback

    raw_type = (offer_data.get("property_type") or "").lower()
    schema_type = CRM_TYPE_TO_SCHEMA.get(raw_type, raw_type).lower()
    if schema_type not in _CADASTRAL_SCHEMA_TYPES:
        return False

    address = offer_data.get("address") or {}
    if address.get("Кадастровий номер"):
        return False

    cadnum = lookup_cadastral_number(
        city=address.get("Місто") or "",
        street=address.get("Вулиця") or "",
        house=address.get("Будинок") or "",
    )
    if cadnum:
        offer_data.setdefault("address", {})["Кадастровий номер"] = cadnum
        logger.info(
            "Кадастровий номер для %s: %s",
            offer_data.get("article") or offer_data.get("property_type", "?"),
            cadnum,
        )
        return True
    return False





def fill_missing_cadastral_numbers(max_count: int | None = None) -> int:
    """Знайти кадастрові номери для всіх об'єктів у БД, де вони відсутні.

    Запитує БД, фільтрує за типами (Будинок, Ділянка, Комерційна),
    шукає номер через kadastr.live і зберігає результат назад у БД.

    Args:
        max_count: Обмежити кількість оброблюваних об'єктів (None = без обмежень).

    Returns:
        Кількість записів, для яких номер знайдено та збережено.
    """
    import sys
    from pathlib import Path  # noqa: PLC0415

    sys.path.insert(0, str(Path(__file__).parent.parent))

    from offer_db import OfferDB  # type: ignore[import]

    # property_type in DB stores the schema name in title case (e.g. "Будинок", "Комерційна").
    # Pass title-case values directly; offer_db compares without LOWER() (SQLite is ASCII-only).
    property_type_filter = [t.capitalize() for t in _CADASTRAL_SCHEMA_TYPES]

    updated = 0
    with OfferDB() as db:
        offers = db.get_without_cadastral(property_types=property_type_filter)
        if max_count:
            offers = offers[:max_count]

        logger.info(
            "Об'єктів без кадастрового номера: %d",
            len(offers),
        )

        for offer in offers:
            offer_data = offer.offer_data
            article = offer.article or str(offer.estate_id)
            addr = offer_data.get("address") or {}
            logger.debug(
                "Пошук кадастрового для %s (%s %s %s)",
                article,
                addr.get("Місто", ""),
                addr.get("Вулиця", ""),
                addr.get("Будинок", ""),
            )
            if enrich_offer_data_with_cadastral(offer_data):
                db.update_offer_data(offer.estate_id, offer_data)
                updated += 1

    logger.info("Кадастрові номери оновлено: %d / %d", updated, len(offers))
    return updated


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Пошук кадастрових номерів у БД")
    parser.add_argument("--max-count", type=int, default=None, help="Максимальна кількість об'єктів")
    args = parser.parse_args()

    found = fill_missing_cadastral_numbers(max_count=args.max_count)
    print(f"Знайдено та збережено: {found}")


if __name__ == "__main__":
    main()
