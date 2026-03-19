"""Пошук кадастрового номера за адресою через kadastr.live."""

from __future__ import annotations

import re

import requests

from setup_logger import setup_logger

logger = setup_logger(__name__)

_CADNUM_RE = re.compile(r"^\d{10}:\d{2}:\d{3}:\d{4}$")
_SEARCH_URL = "https://kadastr.live/search/{}/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; rieltor-bot/1.0)"}

# Schema types (rieltor.ua) that have a "Кадастровий номер" field
_CADASTRAL_SCHEMA_TYPES = frozenset({"будинок", "ділянка", "комерційна"})


def lookup_cadastral_number(city: str, street: str, house: str) -> str | None:
    """Знайти кадастровий номер ділянки за адресою через kadastr.live.

    Args:
        city:   Назва міста або населеного пункту.
        street: Назва вулиці (без префіксу "вул." тощо).
        house:  Номер будинку.

    Returns:
        Рядок у форматі ``XXXXXXXXXX:XX:XXX:XXXX`` або ``None``, якщо не знайдено.
    """
    parts = [p.strip() for p in [city, street, house] if p and p.strip()]
    if not parts:
        return None
    query = " ".join(parts)

    try:
        url = _SEARCH_URL.format(requests.utils.quote(query, safe=""))
        resp = requests.get(url, timeout=8, headers=_HEADERS)
        resp.raise_for_status()
        results = resp.json().get("results") or []
        for item in results:
            cadnum = (item.get("cadnum") or "").strip()
            if _CADNUM_RE.match(cadnum):
                logger.debug("Кадастровий номер знайдено: %s (запит: '%s')", cadnum, query)
                return cadnum
    except Exception:
        logger.warning("Помилка пошуку кадастрового номера для '%s'", query, exc_info=True)

    return None


def enrich_offer_data_with_cadastral(offer_data: dict) -> bool:
    """Додати кадастровий номер до offer_data["address"], якщо він відсутній.

    Перевіряє тип об'єкта через CRM_TYPE_TO_SCHEMA і пропускає типи,
    що не підтримують поле «Кадастровий номер» на rieltor.ua.

    Returns:
        True якщо кадастровий номер знайдено та записано, інакше False.
    """
    from .html_parser import CRM_TYPE_TO_SCHEMA  # local to avoid circular at module load

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
