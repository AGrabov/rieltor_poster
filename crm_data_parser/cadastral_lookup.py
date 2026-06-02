"""Пошук кадастрового номера за адресою через zem.center та kadastrova-karta.com."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

import requests
from bs4 import BeautifulSoup

from setup_logger import setup_logger

try:
    from .address_normalize import (
        fold_cyrillic,
        normalize_city,
        normalize_house,
        street_type_canon,
        strip_street_type,
    )
except ImportError:  # direct-run fallback
    from address_normalize import (  # noqa: I001
        fold_cyrillic,
        normalize_city,
        normalize_house,
        street_type_canon,
        strip_street_type,
    )

logger = setup_logger(__name__)

# Мінімальна схожість назви вулиці (folded) для впевненого збігу.
_STREET_MATCH_THRESHOLD = 0.78
_WORD_RE = re.compile(r"[А-Яа-яІіЇїЄєҐґA-Za-z]+")

_CADNUM_RE = re.compile(r"^\d{10}:\d{2}:\d{3}:\d{4}$")  # full-string match (for API results)
_CADNUM_IN_TEXT_RE = re.compile(r"\d{10}:\d{2}:\d{3}:\d{4}")  # substring search (for descriptions)
_KK_SEARCH_URL = "https://kadastrova-karta.com/search"
_KK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/vnd.turbo-stream.html, text/html",
    "Referer": "https://kadastrova-karta.com/",
}
_ZEM_SEARCH_URL = "https://api.zem.center/api/search"
_ZEM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# Schema types (rieltor.ua) that have a "Кадастровий номер" field
_CADASTRAL_SCHEMA_TYPES = frozenset({"будинок", "ділянка", "комерційна"})


def _house_matches(addr: str, house: str) -> bool:
    """Збіг номера будинку, толерантний до формату (``19А`` = ``19-а`` = ``19 а``).

    Порівнює канон ``normalize_house`` номера CRM з кожним комою-відділеним
    сегментом адреси кандидата (номер будинку в реєстрі стоїть в окремому
    сегменті). ``19`` і ``19-а`` лишаються різними (різні ділянки).
    """
    h = normalize_house(house)
    if not h:
        return False
    return any(normalize_house(seg) == h for seg in addr.split(","))


def _street_matches(street: str, addr: str) -> bool:
    """Чи присутня назва вулиці ``street`` в адресі кандидата (фуззі, RU↔UA).

    Порівнює ``fold_cyrillic``-форми, толеруючи и↔і та інші RU/UA відмінності.
    Перевіряє окремі токени адреси та сусідні пари (для двослівних назв).
    """
    q = fold_cyrillic(strip_street_type(street))
    if not q:
        return False
    tokens = [fold_cyrillic(t) for t in _WORD_RE.findall(addr)]
    tokens = [t for t in tokens if t]
    phrases = list(tokens)
    phrases += [f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1)]
    return any(SequenceMatcher(None, q, p).ratio() >= _STREET_MATCH_THRESHOLD for p in phrases)


def _pick_verified(candidates: list[tuple[str, str]], street: str, house: str) -> str | None:
    """Обрати кадастровий номер лише за ВПЕВНЕНОГО збігу.

    Фільтрує кандидатів за точним номером будинку (окремий токен) і фуззі-
    збігом назви вулиці, а потім розрізняє однойменні вулиці різного типу
    (вул./пров./пл. Шевченка) за каноном типу:

      • тип CRM відомий → беремо кандидата того ж типу; якщо такого немає
        (а інші типи є) → ``None`` (не вгадуємо);
      • тип CRM невідомий, але кандидати різного типу → ``None`` (неоднозначно).

    Інакше — ``None`` (поле краще лишити порожнім: чернетку не відредагувати).
    """
    matches = [
        (cadnum, addr) for cadnum, addr in candidates if _house_matches(addr, house) and _street_matches(street, addr)
    ]
    if not matches:
        return None

    crm_type = street_type_canon(street)
    if crm_type:
        same_type = [cadnum for cadnum, addr in matches if street_type_canon(addr) == crm_type]
        if same_type:
            return same_type[0]
        # Тип CRM відомий, але серед кандидатів типи є й жоден не збігся → не вгадуємо.
        if any(street_type_canon(addr) for _, addr in matches):
            return None
        # Жоден кандидат не має розпізнаного типу — нічим розрізняти, беремо перший.
        return matches[0][0]

    # Тип CRM невідомий: якщо кандидати різного типу — неоднозначно, пропускаємо.
    distinct_types = {street_type_canon(addr) for _, addr in matches if street_type_canon(addr)}
    if len(distinct_types) > 1:
        return None
    return matches[0][0]


def _search_zem_center(query: str, street: str, house: str) -> str | None:
    """Пошук кадастрового номера через zem.center JSON API (основне джерело).

    GET https://api.zem.center/api/search?q=<query>&size=20 → {"items": [...]}.
    Кожен item має ``cadnum`` та ``address``. Повертає впевнений збіг за
    номером будинку та назвою вулиці або None.
    """
    try:
        resp = requests.get(
            _ZEM_SEARCH_URL,
            params={"q": query, "size": "20"},
            headers=_ZEM_HEADERS,
            timeout=(5, 12),  # (connect, read)
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        items = (resp.json() or {}).get("items") or []
        candidates: list[tuple[str, str]] = []
        for item in items:
            cadnum = (item.get("cadnum") or "").strip()
            if _CADNUM_RE.match(cadnum):
                candidates.append((cadnum, item.get("address") or ""))
        return _pick_verified(candidates, street, house)
    except requests.exceptions.Timeout:
        logger.debug("Timeout zem.center для '%s'", query)
        return None
    except Exception:
        logger.warning("Помилка zem.center для '%s'", query, exc_info=True)
        return None


def _search_kadastrova_karta(query: str, street: str, house: str) -> str | None:
    """Пошук кадастрового номера через kadastrova-karta.com (fallback).

    Парсить Turbo Stream HTML відповідь — без Playwright.
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
        candidates: list[tuple[str, str]] = []
        for a_tag in soup.select("a[data-action='search#linkClicked']"):
            cadnum_div = a_tag.select_one("div.font-bold")
            addr_div = a_tag.select_one("div.text-gray-500")
            if not cadnum_div:
                continue
            cadnum = cadnum_div.get_text(strip=True)
            if not _CADNUM_RE.match(cadnum):
                continue
            addr = addr_div.get_text(strip=True) if addr_div else ""
            candidates.append((cadnum, addr))
        return _pick_verified(candidates, street, house)
    except requests.exceptions.Timeout:
        logger.debug("Timeout kadastrova-karta.com для '%s'", query)
        return None
    except Exception:
        logger.warning("Помилка kadastrova-karta.com для '%s'", query, exc_info=True)
        return None


def lookup_cadastral_number(city: str, street: str, house: str) -> str | None:
    """Знайти кадастровий номер ділянки за адресою.

    Стратегія (zem.center JSON API, потім kadastrova-karta.com як fallback):
      1. zem.center: місто + вулиця + будинок
      2. zem.center: місто + вулиця
      3. kadastrova-karta.com: місто + вулиця + будинок
      4. kadastrova-karta.com: місто + вулиця

    Returns:
        Рядок у форматі ``XXXXXXXXXX:XX:XXX:XXXX`` або ``None``, якщо не знайдено.
    """
    # RU→UA: місто за словником, тип вулиці зрізаємо (інакше реєстр дає 0).
    street_clean = strip_street_type(street)
    city_clean = normalize_city(city)
    house_orig = house.strip()
    # Compact house for QUERY recall — "19 б" splits and returns 0, "19б" works.
    house_query = normalize_house(house_orig)

    full = " ".join(p for p in [city_clean, street_clean, house_query] if p)
    short = " ".join(p for p in [city_clean, street_clean] if p)
    # Preserve order, drop empties and duplicates (full == short when no house)
    queries: list[str] = []
    for q in (full, short):
        if q and q not in queries:
            queries.append(q)
    if not queries:
        return None

    # Verification gets the ORIGINAL street (keeps the type for disambiguation);
    # the query `q` uses the stripped form (the registry chokes on any type).
    for q in queries:
        cadnum = _search_zem_center(q, street, house_orig)
        if cadnum:
            logger.debug("Знайдено zem.center: %s (запит '%s')", cadnum, q)
            return cadnum

    for q in queries:
        cadnum = _search_kadastrova_karta(q, street, house_orig)
        if cadnum:
            logger.debug("Знайдено kadastrova-karta.com: %s (запит '%s')", cadnum, q)
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

    # ── Крок 0: шукаємо кадастровий номер у тексті опису/нотаток ──────
    _texts = [
        (offer_data.get("apartment") or {}).get("description") or "",
        offer_data.get("personal_notes") or "",
    ]
    for _text in _texts:
        _match = _CADNUM_IN_TEXT_RE.search(_text)
        if _match:
            cadnum = _match.group()
            offer_data.setdefault("address", {})["Кадастровий номер"] = cadnum
            logger.info(
                "Кадастровий номер знайдено в описі для %s: %s",
                offer_data.get("article") or offer_data.get("property_type", "?"),
                cadnum,
            )
            return True

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
    шукає номер через zem.center і зберігає результат назад у БД.

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
