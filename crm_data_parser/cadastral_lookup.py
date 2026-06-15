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
        looks_russian,
        normalize_city,
        normalize_house,
        recover_street_type,
        ru_to_ua_variants,
        street_type_canon,
        strip_street_type,
    )
except ImportError:  # direct-run fallback
    from address_normalize import (  # noqa: I001
        fold_cyrillic,
        looks_russian,
        normalize_city,
        normalize_house,
        recover_street_type,
        ru_to_ua_variants,
        street_type_canon,
        strip_street_type,
    )

try:
    from .geocoder import geocode_canonical_street
except ImportError:  # direct-run fallback
    from geocoder import geocode_canonical_street  # noqa: I001

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

# Почесні титули у назвах вулиць. У реєстрі вони СТОЯТЬ ПЕРЕД прізвищем
# ("Академіка Туполєва"), а CRM часто зберігає прізвище першим ("Туполєва
# Академіка"). Зберігаємо у folded-формі для RU↔UA-толерантного порівняння.
_HONORIFIC_TITLES = frozenset(
    fold_cyrillic(w)
    for w in (
        "академіка",
        "генерала",
        "генерал",
        "маршала",
        "адмірала",
        "професора",
        "інженера",
        "конструктора",
        "льотчика",
        "пілота",
        "отамана",
        "гетьмана",
        "князя",
        "княгині",
        "митрополита",
        "єпископа",
        "доктора",
        "лікаря",
        "командарма",
    )
)


def _significant_tokens(text: str) -> list[str]:
    """Folded-токени назви (без слів-типів вулиці) для збігу незалежно від порядку."""
    out: list[str] = []
    for tok in _WORD_RE.findall(text or ""):
        if street_type_canon(tok):  # тип вулиці (вул/пров/шосе…) — не частина назви
            continue
        folded = fold_cyrillic(tok)
        if folded:
            out.append(folded)
    return out


def _street_variants(street_clean: str) -> list[str]:
    """Варіанти написання вулиці для пошуку (recall).

    Якщо назва містить почесний титул не на першому місці ("Туполєва Академіка"),
    додає варіант із титулом попереду ("Академіка Туполєва") — канонічний для
    реєстру. Звичайні «ім'я прізвище» (без титулу) НЕ переставляємо, щоб не
    плодити зайвих запитів.
    """
    variants = [street_clean]
    tokens = street_clean.split()
    if len(tokens) >= 2:
        hon_idx = [i for i, t in enumerate(tokens) if fold_cyrillic(t) in _HONORIFIC_TITLES]
        if hon_idx and hon_idx != list(range(len(hon_idx))):
            fronted = [tokens[i] for i in hon_idx] + [tokens[i] for i in range(len(tokens)) if i not in hon_idx]
            variants.append(" ".join(fronted))
    out: list[str] = []
    for v in variants:
        if v and v not in out:
            out.append(v)
    return out


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
    Збіг НЕЗАЛЕЖНИЙ ВІД ПОРЯДКУ слів: кожне значуще слово запиту має фуззі-
    збігтися з якимось словом адреси. Це покриває перестановку титулу
    ("Туполєва Академіка" ↔ "Академіка Туполєва"), бо обидва слова присутні.
    """
    q_tokens = _significant_tokens(strip_street_type(street))
    if not q_tokens:
        return False
    addr_tokens = _significant_tokens(addr)
    if not addr_tokens:
        return False
    return all(
        any(SequenceMatcher(None, q, a).ratio() >= _STREET_MATCH_THRESHOLD for a in addr_tokens) for q in q_tokens
    )


def _pick_verified(candidates: list[tuple[str, str]], street: str, house: str) -> tuple[str, str] | None:
    """Обрати (кадастровий номер, адресу реєстру) лише за ВПЕВНЕНОГО збігу.

    Фільтрує кандидатів за точним номером будинку (окремий токен) і фуззі-
    збігом назви вулиці, а потім розрізняє однойменні вулиці різного типу
    (вул./пров./пл. Шевченка) за каноном типу:

      • тип CRM відомий → беремо кандидата того ж типу; якщо такого немає
        (а інші типи є) → ``None`` (не вгадуємо);
      • тип CRM невідомий, але кандидати різного типу → ``None`` (неоднозначно).

    Інакше — ``None`` (поле краще лишити порожнім: чернетку не відредагувати).

    Returns:
        (cadnum, registry_address) або None.
    """
    # Збіг: номер будинку з суфіксом (точно) + назва вулиці (фуззі, RU↔UA).
    # Тип вулиці звіряємо нижче — за відомого типу він має збігтися.
    matches = [
        (cadnum, addr) for cadnum, addr in candidates if _house_matches(addr, house) and _street_matches(street, addr)
    ]
    if not matches:
        return None

    crm_type = street_type_canon(street)
    if crm_type:
        same_type = [(cadnum, addr) for cadnum, addr in matches if street_type_canon(addr) == crm_type]
        if same_type:
            return same_type[0]
        # Тип CRM відомий, але серед кандидатів типи є й жоден не збігся → не вгадуємо.
        if any(street_type_canon(addr) for _, addr in matches):
            return None
        # Жоден кандидат не має розпізнаного типу — нічим розрізняти, беремо перший.
        return matches[0]

    # Тип CRM невідомий: якщо кандидати різного типу — неоднозначно, пропускаємо.
    distinct_types = {street_type_canon(addr) for _, addr in matches if street_type_canon(addr)}
    if len(distinct_types) > 1:
        return None
    return matches[0]


def _search_zem_center(query: str, street: str, house: str) -> tuple[str, str] | None:
    """Пошук кадастрового номера через zem.center JSON API (основне джерело).

    GET https://api.zem.center/api/search?q=<query>&size=20 → {"items": [...]}.
    Кожен item має ``cadnum`` та ``address``. Повертає (cadnum, address) за
    впевненого збігу номера будинку й назви вулиці або None.
    """
    try:
        resp = requests.get(
            _ZEM_SEARCH_URL,
            params={"q": query, "size": "20"},
            headers=_ZEM_HEADERS,
            timeout=(5, 12),  # (connect, read)
        )
        if resp.status_code >= 400:
            logger.debug("zem.center недоступна (HTTP %s) для '%s'", resp.status_code, query)
            return None

        items = (resp.json() or {}).get("items") or []
        candidates: list[tuple[str, str]] = []
        for item in items:
            cadnum = (item.get("cadnum") or "").strip()
            if _CADNUM_RE.match(cadnum):
                candidates.append((cadnum, item.get("address") or ""))
        rec = _pick_verified(candidates, street, house)
        if rec is None:
            # Видимий слід «спробували, але без впевненого збігу» — інакше здається,
            # ніби zem.center пропущено й одразу пішов fallback kadastrova-karta.
            logger.debug("zem.center: немає впевненого збігу для '%s' (кандидатів: %d)", query, len(candidates))
        return rec
    except requests.exceptions.Timeout:
        logger.debug("Timeout zem.center для '%s'", query)
        return None
    except requests.exceptions.RequestException as e:
        logger.debug("zem.center недоступна для '%s': %s", query, e)
        return None
    except Exception:
        logger.warning("Помилка zem.center для '%s'", query, exc_info=True)
        return None


def _search_kadastrova_karta(query: str, street: str, house: str) -> tuple[str, str] | None:
    """Пошук кадастрового номера через kadastrova-karta.com (fallback).

    Парсить Turbo Stream HTML відповідь — без Playwright. Повертає (cadnum, address).
    """
    try:
        resp = requests.get(
            _KK_SEARCH_URL,
            params={"q": query},
            headers=_KK_HEADERS,
            timeout=(5, 8),  # (connect, read)
        )
        if resp.status_code >= 400:
            # Сайт часто віддає 503 (анти-бот) — це очікувана недоступність,
            # не помилка: тихо, без traceback (інакше лог засмічується).
            logger.debug("kadastrova-karta.com недоступна (HTTP %s) для '%s'", resp.status_code, query)
            return None

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
    except requests.exceptions.RequestException as e:
        logger.debug("kadastrova-karta.com недоступна для '%s': %s", query, e)
        return None
    except Exception:
        logger.warning("Помилка kadastrova-karta.com для '%s'", query, exc_info=True)
        return None


def lookup_cadastral_record(city: str, street: str, house: str) -> tuple[str, str] | None:
    """Знайти (кадастровий номер, адресу реєстру) ділянки за адресою.

    Стратегія (варіанти написання вулиці: оригінал + «титул попереду» +
    RU→UA-транслітерація; для кожного — з будинком, потім без):
      1. zem.center за всіма варіантами з CRM-даних
      2. геокодер OSM (fallback, мережа лише коли крок 1 не дав збігу): канонічна
         укр. назва вулиці → ще раз zem.center
      3. kadastrova-karta.com за всіма варіантами (часто недоступна)

    Returns:
        (cadnum, registry_address) або None. cadnum у форматі
        ``XXXXXXXXXX:XX:XXX:XXXX``; адреса — канонічна з реєстру (з типом
        вулиці та адмінрайоном), напр. "м.Київ, Дарницький р-н, шосе ...".
    """
    # RU→UA: місто за словником, тип вулиці зрізаємо ЛИШЕ у ЗАПИТІ (реєстр інакше
    # дає 0); у даних тип лишається й використовується для звірки нижче.
    street_clean = strip_street_type(street)
    city_clean = normalize_city(city)
    house_orig = house.strip()
    # Compact house for QUERY recall — "19 б" splits and returns 0, "19б" works.
    house_query = normalize_house(house_orig)

    # Спроби (запит, вулиця-для-верифікації). Порядок слів: оригінал, потім
    # «титул попереду» (CRM "Туполєва Академіка" → реєстр "Академіка Туполєва").
    # Однакові за мовою варіанти верифікуємо ОРИГІНАЛЬНОЮ вулицею (зберігає тип
    # для розрізнення вул./пров.); транслітерований варіант — самим UA-написанням
    # (рос. оригінал не збігся б з укр. адресою реєстру).
    attempts: list[tuple[str, str]] = []
    seen_q: set[str] = set()

    def _add(name: str, verify_street: str) -> None:
        full = " ".join(p for p in [city_clean, name, house_query] if p)
        short = " ".join(p for p in [city_clean, name] if p)
        for q in (full, short):
            if q and q not in seen_q:
                seen_q.add(q)
                attempts.append((q, verify_street))

    for street_variant in _street_variants(street_clean):
        _add(street_variant, street)

    # RU→UA: CRM зберігає вулицю російською ("Якубенковская") — реєстр шукає лише
    # за укр. написанням. Додаємо транслітеровані варіанти (best-effort, кілька
    # для неоднозначних закінчень); верифікація відсіє хибні.
    if looks_russian(street_clean):
        for ua in ru_to_ua_variants(street_clean):
            if ua and fold_cyrillic(ua) != fold_cyrillic(street_clean):
                for ua_variant in _street_variants(ua):
                    _add(ua_variant, ua_variant)

    if not attempts:
        return None

    # ── 1) zem.center за варіантами написання з CRM-даних ──
    for q, verify_street in attempts:
        rec = _search_zem_center(q, verify_street, house_orig)
        if rec:
            logger.debug("Знайдено zem.center: %s (запит '%s')", rec[0], q)
            return rec

    # ── 2) Геокодер (OSM) як fallback ──
    # Мережевий виклик робимо ЛИШЕ коли дешева нормалізація не дала збігу: OSM дає
    # канонічну укр. назву вулиці (покриває лексичні переклади/відмінювання, які
    # правила не беруть). Верифікація лишається суворою — хибний геокод не заповнить.
    geo_attempts: list[tuple[str, str]] = []
    canonical = geocode_canonical_street(city_clean, street, house_orig)
    if canonical:
        canon_clean = strip_street_type(canonical)
        if canon_clean:
            for parts in ((city_clean, canon_clean, house_query), (city_clean, canon_clean)):
                gq = " ".join(p for p in parts if p)
                if gq and gq not in seen_q:
                    seen_q.add(gq)
                    geo_attempts.append((gq, canonical))
    for q, verify_street in geo_attempts:
        rec = _search_zem_center(q, verify_street, house_orig)
        if rec:
            logger.debug("Знайдено zem.center (геокодер): %s (запит '%s')", rec[0], q)
            return rec

    # ── 3) kadastrova-karta.com (часто недоступна) ──
    for q, verify_street in attempts + geo_attempts:
        rec = _search_kadastrova_karta(q, verify_street, house_orig)
        if rec:
            logger.debug("Знайдено kadastrova-karta.com: %s (запит '%s')", rec[0], q)
            return rec

    return None


def lookup_cadastral_number(city: str, street: str, house: str) -> str | None:
    """Знайти лише кадастровий номер за адресою (обгортка над record)."""
    rec = lookup_cadastral_record(city, street, house)
    return rec[0] if rec else None


def lookup_address_by_cadnum(cadnum: str) -> str | None:
    """Отримати канонічну адресу реєстру за кадастровим номером (zem.center).

    Потрібно, щоб дозаповнити Район/тип вулиці для об'єктів, у яких номер уже
    збережено (а отже повторного пошуку за адресою не було).
    """
    cadnum = (cadnum or "").strip()
    if not _CADNUM_RE.match(cadnum):
        return None
    try:
        resp = requests.get(
            _ZEM_SEARCH_URL,
            params={"q": cadnum, "size": "5"},
            headers=_ZEM_HEADERS,
            timeout=(5, 12),
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        for item in (resp.json() or {}).get("items") or []:
            if (item.get("cadnum") or "").strip() == cadnum:
                return (item.get("address") or "").strip() or None
    except requests.exceptions.Timeout:
        logger.debug("Timeout zem.center (cadnum) для '%s'", cadnum)
    except Exception:
        logger.warning("Помилка zem.center (cadnum) для '%s'", cadnum, exc_info=True)
    return None


# Слова-типи вулиць у канонічному (повному) вигляді з реєстру.
_REGISTRY_TYPE_WORDS = frozenset(
    {
        "шосе",
        "проспект",
        "просп",
        "бульвар",
        "бул",
        "провулок",
        "пров",
        "площа",
        "пл",
        "узвіз",
        "набережна",
        "проїзд",
        "тупик",
        "тупік",
        "алея",
        "дорога",
        "дор",
        "вулиця",
        "вул",
        "майдан",
    }
)
_REGISTRY_DEFAULT_TYPES = frozenset({"вулиця", "вул"})


def _format_registry_street(segment: str) -> str:
    """Перетворити сегмент вулиці з реєстру у формат сайту: НАЗВА + тип.

    "шосе Харківське" → "Харківське шосе"; "вул. Воскресенська" → "Воскресенська"
    (типовий "вул" опускаємо — сайт трактує вулицю як тип за замовчуванням).
    """
    tokens = [t for t in segment.replace(".", " ").split() if t]
    type_tok = next((t.lower() for t in tokens if t.lower() in _REGISTRY_TYPE_WORDS), None)
    name = " ".join(t for t in tokens if t.lower() not in _REGISTRY_TYPE_WORDS).strip()
    if not name:
        return segment.strip()
    if not type_tok or type_tok in _REGISTRY_DEFAULT_TYPES:
        return name
    return f"{name} {type_tok}"


def parse_registry_address(addr: str) -> dict:
    """Розібрати канонічну адресу реєстру на Район і Вулицю (з типом).

    Напр. "м.Київ, Дарницький р-н, шосе Харківське, 201-203" →
    {"Район": "Дарницький", "Вулиця": "Харківське шосе"}.
    """
    result: dict[str, str] = {}
    for part in (p.strip() for p in (addr or "").split(",") if p.strip()):
        low = part.lower()
        if re.search(r"\bр-?н\b|\bрайон\b", low):
            if "Район" not in result:
                result["Район"] = re.sub(r"\s*(р-?н|район)\b\.?", "", part, flags=re.IGNORECASE).strip()
        elif "Вулиця" not in result and any(t.lower() in _REGISTRY_TYPE_WORDS for t in part.replace(".", " ").split()):
            result["Вулиця"] = _format_registry_street(part)
    return result


def _street_base(street: str) -> str:
    """Базова назва вулиці (без будь-яких типів, fold для RU↔UA звірки)."""
    tokens = [t for t in (street or "").replace(".", " ").split() if t.lower() not in _REGISTRY_TYPE_WORDS]
    return fold_cyrillic(" ".join(tokens))


def _registry_matches_crm(crm_address: dict, registry_addr: str) -> bool:
    """Чи парцель реєстру відповідає адресі CRM за тими ж критеріями, що й вибір
    кадастрового номера: номер будинку з суфіксом (точно) + тип вулиці (за
    відомого — збіг) + назва вулиці (фуззі). Використовується, щоб дозволити
    перезапис Району/написання адреси з реєстру лише за впевненого збігу.
    """
    street = crm_address.get("Вулиця") or ""
    house = crm_address.get("Будинок") or ""
    if not street or not house or not registry_addr:
        return False
    return _pick_verified([("", registry_addr)], street, house) is not None


def enrich_offer_data_with_cadastral(offer_data: dict) -> bool:
    """Дозаповнити кадастровий номер, Район і тип вулиці з реєстру.

    Перевіряє тип об'єкта через CRM_TYPE_TO_SCHEMA і пропускає типи, що не
    підтримують кадастр на rieltor.ua (Будинок/Ділянка/Комерційна). Коли є
    кадастровий номер (знайдений або вже збережений), бере з реєстру
    канонічну адресу: Район (адмінрайон — CRM часто дає мікрорайон/масив) та
    Вулицю з типом (сайту потрібен "шосе"/"проспект" для вибору зі списку).

    Returns:
        True якщо щось змінилось (номер, Район чи Вулиця).
    """
    try:
        from .html_parser import CRM_TYPE_TO_SCHEMA  # package context
    except ImportError:
        from html_parser import CRM_TYPE_TO_SCHEMA  # noqa: I001  # direct run fallback

    raw_type = (offer_data.get("property_type") or "").lower()
    schema_type = CRM_TYPE_TO_SCHEMA.get(raw_type, raw_type).lower()
    if schema_type not in _CADASTRAL_SCHEMA_TYPES:
        return False

    address = offer_data.setdefault("address", {})
    label = offer_data.get("article") or offer_data.get("property_type", "?")
    changed = False

    # ── Якщо у вулиці немає типу — спробувати знайти його в описі/нотатках ──
    # (CRM часто зберігає вулицю без типу; тип потрібен для звірки з реєстром і
    # для вибору варіанта на сайті). Робимо ДО пошуку — точніша верифікація.
    _street = address.get("Вулиця") or ""
    if _street:
        _typed = recover_street_type(
            _street,
            (offer_data.get("apartment") or {}).get("description") or "",
            offer_data.get("personal_notes") or "",
        )
        if _typed != _street:
            address["Вулиця"] = _typed
            changed = True
            logger.info("Тип вулиці з опису для %s: '%s' → '%s'", label, _street, _typed)

    cadnum = (address.get("Кадастровий номер") or "").strip()
    registry_addr: str | None = None

    if not cadnum:
        # ── Крок 0: кадастровий номер у тексті опису/нотаток ──
        for _text in (
            (offer_data.get("apartment") or {}).get("description") or "",
            offer_data.get("personal_notes") or "",
        ):
            _match = _CADNUM_IN_TEXT_RE.search(_text)
            if _match:
                cadnum = _match.group()
                address["Кадастровий номер"] = cadnum
                changed = True
                logger.info("Кадастровий номер знайдено в описі для %s: %s", label, cadnum)
                break

    if not cadnum:
        # ── Крок 1: пошук за адресою (повертає й канонічну адресу реєстру) ──
        rec = lookup_cadastral_record(
            city=address.get("Місто") or "",
            street=address.get("Вулиця") or "",
            house=address.get("Будинок") or "",
        )
        if rec:
            cadnum, registry_addr = rec
            address["Кадастровий номер"] = cadnum
            changed = True
            logger.info("Кадастровий номер для %s: %s", label, cadnum)

    if not cadnum:
        return changed

    # ── Район + тип вулиці з канонічної адреси реєстру ──
    if registry_addr is None:
        registry_addr = lookup_address_by_cadnum(cadnum)
    if registry_addr:
        parsed = parse_registry_address(registry_addr)
        # Реєстр — джерело істини для Району/написання адреси ЛИШЕ за впевненого
        # збігу (номер будинку з суфіксом + тип вулиці + назва). Інакше лишаємо
        # дані CRM (кадастровий номер усе одно збережено вище).
        if _registry_matches_crm(address, registry_addr):
            raion = parsed.get("Район")
            if raion and address.get("Район") != raion:
                logger.info("Район з реєстру для %s: '%s' → '%s'", label, address.get("Район"), raion)
                address["Район"] = raion
                changed = True
            reg_street = parsed.get("Вулиця")
            if reg_street and reg_street != (address.get("Вулиця") or ""):
                logger.info("Вулиця з реєстру для %s: '%s' → '%s'", label, address.get("Вулиця"), reg_street)
                address["Вулиця"] = reg_street
                changed = True
        else:
            logger.debug("Реєстр: не повний збіг адреси для %s — Район/вулицю лишаємо з CRM", label)

    return changed


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
