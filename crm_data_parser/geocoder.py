"""Геокодер (Nominatim/OSM) — нормалізація назви вулиці до канонічної укр. форми.

CRM зберігає вулицю як трапиться: російською ("Шелковичная"), у непрямому
відмінку чи з переставленими словами ("Стуса Василия"). Правила транслітерації
(``address_normalize``) покривають системні випадки, але не лексичні переклади
(Шёлк→Шовк) чи відмінювання імен. Геокодер OSM нормалізує адресу до канонічної
української назви вулиці — її потім використовуємо як ще один варіант запиту до
реєстру кадастру (zem.center), зі звичайною суворою верифікацією.

Викликаємо ЛИШЕ як fallback (коли дешева нормалізація не дала збігу), щоб не
навантажувати публічний Nominatim (політика: ≤1 запит/с, обов'язковий кеш).
"""

from __future__ import annotations

import os
import re
import time
from functools import lru_cache

import requests

from setup_logger import setup_logger

try:
    from .address_normalize import normalize_house, strip_street_type
except ImportError:  # direct-run fallback
    from address_normalize import normalize_house, strip_street_type  # noqa: I001

logger = setup_logger(__name__)

_NOMINATIM_URL = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
# Політика Nominatim вимагає змістовний User-Agent із контактом — за потреби
# перевизначити через env GEOCODER_USER_AGENT (додати свій email/домен).
_USER_AGENT = os.getenv("GEOCODER_USER_AGENT", "RieltorPoster/1.0 (cadastral street normalization)")
_MIN_INTERVAL = 1.0  # секунд між запитами (≤1 req/s)
# Ключі OSM-адреси, де може бути назва вулиці (за пріоритетом).
_ROAD_KEYS = ("road", "pedestrian", "residential", "footway", "path", "neighbourhood")

_last_call = 0.0


def geocoder_enabled() -> bool:
    """Чи увімкнено геокодер (env GEOCODER_ENABLED, типово так)."""
    return os.getenv("GEOCODER_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off", ""}


def _throttle() -> None:
    """Дотримати ≤1 запит/с до Nominatim (політика використання)."""
    global _last_call
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


@lru_cache(maxsize=2048)
def geocode_canonical_street(city: str, street: str, house: str) -> str | None:
    """Канонічна укр. назва вулиці з OSM (або None). Кешується в межах процесу.

    Повертає рядок виду "Шовковична вулиця"/"вулиця Василя Стуса" (як його дає
    OSM, з типом — для запиту тип усе одно зрізаємо). None, якщо вимкнено, нема
    результату чи сталася помилка мережі.
    """
    if not geocoder_enabled() or not (city and street):
        return None

    street_q = strip_street_type(street) or street
    # Якоримо запит БАЗОВИМ номером будинку (цифри без літери): "35-А"→"35",
    # "18д"→"18". Це дає Nominatim адресну прив'язку (надійний збіг вулиці), і
    # водночас толерує літерні суфікси, що інколи дають 0. БЕЗ номера не шукаємо:
    # порожній будинок → Nominatim повертає випадкову вулицю центроїда (хибний
    # збіг, який верифікація за самоназвою не відсіє).
    base = re.match(r"\d+", normalize_house(house))
    if not base:
        return None
    query = ", ".join([city, f"{street_q} {base.group()}"])
    return _query_nominatim(query)


def _query_nominatim(query: str) -> str | None:
    """Один запит до Nominatim → назва вулиці з адреси відповіді або None."""
    try:
        _throttle()
        resp = requests.get(
            _NOMINATIM_URL,
            params={
                "q": query,
                "format": "jsonv2",
                "addressdetails": 1,
                "accept-language": "uk",
                "limit": 1,
                "countrycodes": "ua",
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=(5, 15),
        )
        if resp.status_code >= 400:
            logger.debug("Nominatim недоступний (HTTP %s) для '%s'", resp.status_code, query)
            return None
        data = resp.json() or []
        if not data:
            logger.debug("Nominatim: нічого не знайдено для '%s'", query)
            return None
        addr = (data[0] or {}).get("address") or {}
        for key in _ROAD_KEYS:
            road = (addr.get(key) or "").strip()
            if road:
                logger.debug("Nominatim: '%s' → вулиця '%s'", query, road)
                return road
        logger.debug("Nominatim: у відповіді немає назви вулиці для '%s'", query)
        return None
    except requests.exceptions.Timeout:
        logger.debug("Timeout Nominatim для '%s'", query)
        return None
    except requests.exceptions.RequestException as e:
        logger.debug("Nominatim недоступний для '%s': %s", query, e)
        return None
    except Exception:
        logger.warning("Помилка Nominatim для '%s'", query, exc_info=True)
        return None
