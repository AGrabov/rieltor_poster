"""Головний скрипт оркестрації автоматизації оголошень Rieltor.

Двофазний конвеєр:
  Фаза 1 (collect):  CRM → парсинг об'єктів → завантаження фото → збереження в SQLite
  Фаза 2 (post):     SQLite → створення чернеток/публікація на Rieltor

Використання:
  python main.py                                    # collect + post (чернетка)
  python main.py collect                            # тільки Фаза 1
  python main.py collect --max-pages 1 --max-count 3
  python main.py post                               # тільки Фаза 2 (чернетка)
  python main.py post --publish                     # Фаза 2 з публікацією
  python main.py post --deal-type sell --max-count 5
  python main.py post-one offer.json                # публікація одного оголошення з JSON
  python main.py post-one '{"Ціна": "100000", ...}'
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from setup_logger import extra_file_handler, init_logging, setup_logger

load_dotenv()

# ── Drafts count file ────────────────────────────────────────────────

DRAFTS_COUNT_FILE = Path(__file__).parent / "tmp" / "drafts_count.json"

# ── Окремі лог-файли за напрямом роботи ──────────────────────────────
# Кожна команда, окрім спільного logs/rieltor.log, дублює свої записи у
# профільний файл: парсинг CRM, публікація та сервісні дії в БД — окремо.
_LOGS_DIR = Path(__file__).parent / "logs"
CRM_PARSE_LOG_FILE = _LOGS_DIR / "crm_parse.log"  # Фаза 1 — збір з CRM
PUBLISH_LOG_FILE = _LOGS_DIR / "publish.log"  # Фаза 2 — публікація
DB_SERVICE_LOG_FILE = _LOGS_DIR / "db_service.log"  # сервісні дії в БД (кадастр, ремонт)
DRAFTS_LOG_FILE = _LOGS_DIR / "drafts_publish.log"  # масова публікація чернеток
SYNC_LOG_FILE = _LOGS_DIR / "sync_status.log"  # звірка статусів БД ↔ сайт
FIX_PHOTOS_LOG_FILE = _LOGS_DIR / "fix_draft_photos.log"  # дозаливання фото у чернетки


def write_drafts_count(count: int, path: Path = DRAFTS_COUNT_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"count": count}), encoding="utf-8")


def read_drafts_count(path: Path = DRAFTS_COUNT_FILE) -> int | None:
    try:
        return int(json.loads(path.read_text(encoding="utf-8"))["count"])
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return None


init_logging(
    level=os.getenv("LOG_LEVEL", "INFO"),
    filename="logs/rieltor.log",
    clear_on_start=False,  # логи більше не очищаються на старті (ротація обмежує розмір)
)
logger = setup_logger(__name__)


# ── Legacy offer-data normalization ─────────────────────────────────


def _normalize_offer_data(offer_data: dict) -> None:
    """Fix legacy DB entries that were collected with old notes/description format.

    Changes applied (idempotent — safe to call on already-normalized data):
    1. personal_notes: remove "Артикул: #..." line.
    2. personal_notes: strip email from Відповідальний contacts and remove
       the surrounding parentheses — "(тел: X, email: Y)" → "тел: X".
    3. description: append "Артикул: #..." if not already present there
       (migrates article from old notes position to description).
    """
    article = offer_data.get("article")
    notes = offer_data.get("personal_notes", "") or ""

    # 1+2: fix notes
    if notes:
        cleaned: list[str] = []
        for line in notes.splitlines():
            # Drop the legacy "Артикул: #..." line
            if re.match(r"^Артикул:\s*#", line.strip()):
                continue
            # Fix "Відповідальний: Name (тел: X, email: Y)" → "Відповідальний: Name тел: X"
            if line.startswith("Відповідальний:"):
                # Remove every ", email: ..." segment (with or without trailing close-paren)
                line = re.sub(r",?\s*email:[^,)]*", "", line)
                # Unwrap parentheses: "(тел: X)" → "тел: X"
                line = re.sub(r"\s*\(([^)]*)\)", lambda m: " " + m.group(1).strip(), line)
                line = line.rstrip(" ,")
            cleaned.append(line)
        offer_data["personal_notes"] = "\n".join(cleaned)

    # 3: ensure article is in description
    if article:
        article_tag = f"Артикул: #{article}"
        desc_block = offer_data.get("apartment") or {}
        desc = desc_block.get("description", "") or ""
        if article_tag not in desc:
            if "apartment" not in offer_data:
                offer_data["apartment"] = {}
            offer_data["apartment"]["description"] = (desc + f"\n\n{article_tag}").strip()


# ── Deal-type normalization ──────────────────────────────────────────

_DEAL_TYPE_NORMALIZE = {
    "sell": "Продаж",
    "продаж": "Продаж",
    "lease": "Оренда",
    "rent": "Оренда",
    "оренда": "Оренда",
}


def _normalize_deal_type(value: str) -> str | None:
    if not value:
        return None
    return _DEAL_TYPE_NORMALIZE.get(value.lower().strip())


# ── Property-type filtering ──────────────────────────────────────────

# Free-to-post property types on rieltor.ua. "Безкоштовне" expands to these.
_FREE_PROPERTY_TYPES = ["Будинок", "Комерційна", "Ділянка", "Паркомісце"]


def _build_collect_item_filter(deal_type: str | None, property_type: str | None):
    """Скласти предикат для фільтрації карток списку CRM на льоту.

    Застосовується ДО підрахунку зібраних об'єктів, тож ліміт max_count
    рахує саме об'єкти потрібного типу/угоди. "Безкоштовне" розгортається
    в усі безкоштовні типи (як у фазі post).

    Returns:
        Функція item -> bool (True = картку лишити).
    """
    from crm_data_parser.html_parser import CRM_TYPE_TO_SCHEMA

    normalized_deal = _normalize_deal_type(deal_type) if deal_type else None

    def _schema_type(item) -> str:
        crm_type = (item.property_type or "").lower()
        return CRM_TYPE_TO_SCHEMA.get(crm_type, item.property_type or "").lower()

    def _matches_deal(item) -> bool:
        if not normalized_deal:
            return True
        return bool(item.deal_type and item.deal_type.lower() == normalized_deal.lower())

    def _matches_property(item) -> bool:
        if not property_type:
            return True
        crm_type = (item.property_type or "").lower()
        schema = _schema_type(item)
        if property_type == "Безкоштовне":
            return any(schema == f.lower() or schema.startswith(f.lower() + "_") for f in _FREE_PROPERTY_TYPES)
        pt_lower = property_type.lower()
        # Direct CRM-type match (e.g. "будинок") or schema match (e.g. "таунхаус" → "Будинок").
        return crm_type == pt_lower or schema == pt_lower

    def _item_filter(item) -> bool:
        return _matches_deal(item) and _matches_property(item)

    return _item_filter


# ── Phase 1: CRM collection ─────────────────────────────────────────


def phase1_collect(
    max_pages: int | None = None,
    max_count: int | None = None,
    deal_type: str | None = None,
    property_type: str | None = None,
    headless: bool = True,
    debug: bool = False,
) -> int:
    """Зібрати об'єкти з CRM, розпарсити, завантажити фото, зберегти в БД.

    Returns:
        Кількість нових оголошень збережених у БД.
    """
    from crm_data_parser import (
        CrmCredentials,
        CrmSession,
        EstateListCollector,
        HTMLOfferParser,
        download_estate_photos,
        download_watermark_zip,
    )
    from offer_db import OfferDB

    crm_email = os.environ.get("CRM_EMAIL", "").strip()
    crm_password = os.environ.get("CRM_PASSWORD", "").strip()
    if not crm_email or not crm_password:
        logger.error("CRM_EMAIL та CRM_PASSWORD повинні бути задані в .env")
        return 0

    crm_creds = CrmCredentials(email=crm_email, password=crm_password)
    saved = 0

    with OfferDB() as db, CrmSession(crm_creds, headless=headless, debug=debug) as crm:
        crm.login()

        collector = EstateListCollector(
            crm.page,
            commission_sale=os.getenv("COMMISSION_SALE", "3"),
            commission_sale_unit=os.getenv("COMMISSION_SALE_UNIT", "%"),
            commission_rent=os.getenv("COMMISSION_RENT", "50"),
            commission_rent_unit=os.getenv("COMMISSION_RENT_UNIT", "%"),
            debug=debug,
        )

        # Build the type/deal filter and iterate the CRM list lazily, so the
        # property-type filter is applied BEFORE counting and pagination stops
        # as soon as max_count new offers are saved (no need to crawl all pages).
        item_filter = _build_collect_item_filter(deal_type, property_type)
        if deal_type or property_type:
            logger.info(
                "Фільтр збору: deal_type=%s, property_type=%s",
                _normalize_deal_type(deal_type) if deal_type else "будь-який",
                property_type or "будь-який",
            )

        # Denominator shown in "saved" progress logs: target new saves, or "?" when unbounded.
        target_label = str(max_count) if max_count else "?"
        idx = 0
        for item in collector.iter_advertisable(max_pages=max_pages, item_filter=item_filter):
            if max_count and saved >= max_count:
                logger.info("Досягнуто ліміту %d нових збережених об'єктів, зупинка", max_count)
                break
            idx += 1

            if db.estate_exists(item.estate_id):
                logger.info(
                    "[%d] Об'єкт %d вже є в БД, пропускаємо",
                    idx,
                    item.estate_id,
                )
                continue

            try:
                html = collector.get_estate_html(item.estate_id)
                if html is None:
                    db.insert_offer(
                        estate_id=item.estate_id,
                        offer_data={},
                        property_type=item.property_type,
                        deal_type=item.deal_type,
                        title=item.title,
                        status="skipped",
                    )
                    logger.warning(
                        "[%d] Об'єкт %d закрито, пропущено",
                        idx,
                        item.estate_id,
                    )
                    continue

                parser = HTMLOfferParser(html, debug=debug)
                offer_data = parser.parse()

                collector.enrich_with_commission(offer_data, item)
                collector.enrich_with_responsible_contacts(offer_data)
                collector.enrich_with_cadastral_number(offer_data)

                # Download photos while CRM session is active
                article = offer_data.get("article", str(item.estate_id))
                photo_dl_link = offer_data.get("photo_download_link")
                photo_urls = offer_data.get("apartment", {}).get("photos", [])
                add_watermark = os.getenv("ADD_WATERMARK", "true").lower() == "true"
                if add_watermark:
                    # Качаємо фото без вотермарки — наш логотип додасть photo_processing
                    local_paths = download_estate_photos(crm.page, photo_urls, article) if photo_urls else []
                elif photo_dl_link:
                    # Качаємо ZIP з вотермаркою з CRM
                    local_paths = download_watermark_zip(crm.page, photo_dl_link, article)
                    if not local_paths and photo_urls:
                        logger.warning("Watermark ZIP порожній, використовуємо окремі фото для %s", article)
                        local_paths = download_estate_photos(crm.page, photo_urls, article)
                elif photo_urls:
                    local_paths = download_estate_photos(crm.page, photo_urls, article)
                else:
                    local_paths = []
                if local_paths:
                    # Зберігаємо й оригінальні URL — джерело для перезавантаження у Фазі 2
                    _store_downloaded_photos(offer_data, photo_urls, local_paths)

                db.insert_offer(
                    estate_id=item.estate_id,
                    offer_data=offer_data,
                    article=article,
                    property_type=offer_data.get("property_type"),
                    deal_type=offer_data.get("offer_type"),
                    title=item.title,
                )
                saved += 1
                logger.info(
                    "[збережено %d/%s] Об'єкт %d (article=%s)",
                    saved,
                    target_label,
                    item.estate_id,
                    article,
                )

            except Exception:
                logger.exception(
                    "[%d] Помилка обробки об'єкта %d",
                    idx,
                    item.estate_id,
                )
                db.insert_offer(
                    estate_id=item.estate_id,
                    offer_data={},
                    property_type=item.property_type,
                    deal_type=item.deal_type,
                    title=item.title,
                    status="failed",
                )

    logger.info("Фаза 1 завершена: %d нових оголошень збережено в БД", saved)
    return saved


# ── Photo helpers ────────────────────────────────────────────────────


def _photos_missing(offer_data: dict) -> bool:
    """True if local photo files for this offer are absent on disk.

    Шляхи перевіряються через ``resolve_local_photo`` — стійко до переносу місця
    встановлення (старий абсолютний шлях у БД → перерахунок на поточний PICS_DIR).
    """
    from crm_data_parser.photo_downloader import resolve_local_photo

    photos = (offer_data.get("apartment") or {}).get("photos", [])
    if not photos:
        return True
    return not any(resolve_local_photo(p) for p in photos if isinstance(p, str))


def _backfill_source_photos(offer, photos: list, db) -> bool:
    """Зберегти оригінальні URL фото з CRM у БД, якщо їх там ще немає.

    Викликається під час перевірки актуальності: HTML сторінки об'єкта вже
    завантажено, тож ``apartment.source_photos`` можна поповнити «безкоштовно»
    для об'єктів, зібраних до фіксу (без збереженого джерела). Це дає змогу
    перезавантажити фото без повторного збору у Фазі 1.

    Returns:
        True, якщо джерело записано (раніше його не було).
    """
    if not photos:
        return False
    offer_data = offer.offer_data or {}
    apartment = offer_data.get("apartment") or {}
    existing = [u for u in (apartment.get("source_photos") or []) if isinstance(u, str) and u.strip()]
    if existing:
        return False
    offer_data.setdefault("apartment", {})["source_photos"] = list(photos)
    offer.offer_data = offer_data
    db.update_offer_data(offer.estate_id, offer_data)
    logger.info(
        "Об'єкт %s: збережено %d URL фото з CRM (джерело для перезавантаження)",
        offer.article or offer.estate_id,
        len(photos),
    )
    return True


def _store_downloaded_photos(offer_data: dict, source_urls: list, local_paths: list) -> None:
    """Записати локальні шляхи у ``apartment.photos``, зберігши оригінальні CRM-URL.

    Оригінальні URL зберігаються в ``apartment.source_photos`` — саме вони є
    джерелом для перезавантаження у Фазі 2, якщо локальні файли зникнуть
    (``apartment.photos`` на той момент уже міститиме локальні шляхи, а не URL).
    """
    apartment = offer_data.setdefault("apartment", {})
    source = [u for u in source_urls if isinstance(u, str) and u.strip()]
    if source:
        apartment["source_photos"] = source
    apartment["photos"] = local_paths


def _photo_source_urls(offer_data: dict) -> list[str]:
    """Оригінальні CRM-URL фото для перезавантаження.

    Спершу беремо збережені в Фазі 1 ``apartment.source_photos``; як запасний
    варіант — http-посилання, що могли лишитись у ``apartment.photos``
    (об'єкти, зібрані без вотермарки / до фіксу збереження джерела).
    """
    apartment = offer_data.get("apartment") or {}
    source = [u for u in (apartment.get("source_photos") or []) if isinstance(u, str) and u.strip()]
    if source:
        return source
    return [p for p in apartment.get("photos", []) if isinstance(p, str) and p.startswith("http")]


def _download_photos_with_retry(page, photo_urls: list, article: str, max_attempts: int = 3) -> list[str]:
    """Завантажити фото з повторними спробами; повернути найповніший результат.

    ``download_estate_photos`` сам ловить помилки окремих фото і повертає те, що
    вдалося. Тут ми повторюємо спробу, поки не отримаємо повний набір або не
    вичерпаємо ``max_attempts``, лишаючи найкращий (найповніший) результат.
    """
    from crm_data_parser import download_estate_photos

    best: list[str] = []
    for attempt in range(1, max_attempts + 1):
        try:
            local_paths = download_estate_photos(page, photo_urls, article)
        except Exception:
            logger.warning(
                "Об'єкт %s: помилка завантаження фото (спроба %d/%d)",
                article,
                attempt,
                max_attempts,
                exc_info=True,
            )
            local_paths = []
        if len(local_paths) > len(best):
            best = local_paths
        if len(best) >= len(photo_urls):
            if attempt > 1:
                logger.info(
                    "Об'єкт %s: усі %d фото завантажено з %d-ї спроби",
                    article,
                    len(best),
                    attempt,
                )
            return best
        if attempt < max_attempts:
            logger.warning(
                "Об'єкт %s: завантажено %d/%d фото (спроба %d/%d), повтор...",
                article,
                len(local_paths),
                len(photo_urls),
                attempt,
                max_attempts,
            )
    return best


def _redownload_photos_in_session(page, offers: list, db) -> None:
    """Перезавантажити фото для об'єктів з відсутніми локально файлами.

    Використовує вже відкриту CRM-сторінку ``page`` (сесію відкриває викликач).
    Джерело — оригінальні CRM-URL (``apartment.source_photos``). Кожне
    завантаження повторюється до ``PHOTO_REDOWNLOAD_RETRIES`` разів. Оновлює
    offer_data та БД на місці. Веде детальний лог із підсумком.
    """
    missing = [o for o in offers if _photos_missing(o.offer_data)]
    if not missing:
        return

    max_attempts = max(1, int(os.getenv("PHOTO_REDOWNLOAD_RETRIES", "3")))
    logger.info(
        "Перезавантаження фото: %d об'єктів з відсутніми локальними фото (до %d спроб кожен)",
        len(missing),
        max_attempts,
    )

    recovered = 0
    failed: list[str] = []  # є джерело, але завантажити не вдалось
    no_source: list[str] = []  # немає збережених URL (зібрано до фіксу)

    for offer in missing:
        article = offer.article or str(offer.estate_id)
        offer_data = offer.offer_data
        source_urls = _photo_source_urls(offer_data)
        if not source_urls:
            no_source.append(article)
            logger.warning(
                "Об'єкт %s: немає збережених URL фото для перезавантаження "
                "(зібрано до фіксу — потрібно перезібрати у Фазі 1)",
                article,
            )
            continue

        local_paths = _download_photos_with_retry(page, source_urls, article, max_attempts=max_attempts)
        if local_paths:
            offer_data.setdefault("apartment", {})["photos"] = local_paths
            db.update_offer_data(offer.estate_id, offer_data)
            recovered += 1
            logger.info(
                "Об'єкт %s: перезавантажено %d/%d фото",
                article,
                len(local_paths),
                len(source_urls),
            )
        else:
            failed.append(article)
            logger.warning(
                "Об'єкт %s: не вдалось перезавантажити жодного фото з %d URL після %d спроб",
                article,
                len(source_urls),
                max_attempts,
            )

    logger.info(
        "Підсумок перезавантаження фото: відновлено %d, не вдалось %d, без джерела URL %d",
        recovered,
        len(failed),
        len(no_source),
    )
    if failed:
        logger.warning("Перезавантаження не вдалось (є джерело): %s", ", ".join(failed))
    if no_source:
        logger.warning("Без збережених URL (перезібрати у Фазі 1): %s", ", ".join(no_source))


def _price_changed(stored_price, stored_currency, current_price, current_currency) -> bool:
    """Чи відрізняється поточна ціна в CRM від збереженої.

    None у будь-якій із поточних/збережених сум трактуємо як «порівняти не можна»
    (не зміна), щоб не зчиняти хибних тривог, коли ціну не вдалось зчитати.
    """
    if current_price is None or stored_price is None:
        return False
    try:
        amount_changed = int(float(str(stored_price))) != int(current_price)
    except (TypeError, ValueError):
        amount_changed = str(stored_price).strip() != str(current_price).strip()
    currency_changed = bool(
        stored_currency and current_currency and str(stored_currency).strip() != str(current_currency).strip()
    )
    return amount_changed or currency_changed


def _log_price_change(offer, actuality) -> bool:
    """Залогувати зміну ціни об'єкта в CRM («було → стало»). True, якщо змінилась."""
    stored_price = (offer.offer_data or {}).get("Ціна")
    stored_currency = (offer.offer_data or {}).get("Валюта")
    if _price_changed(stored_price, stored_currency, actuality.price, actuality.currency):
        logger.warning(
            "Ціна змінилась у CRM для об'єкта %d (article=%s): було %s %s → стало %s %s",
            offer.estate_id,
            offer.article,
            stored_price,
            stored_currency or "",
            actuality.price,
            actuality.currency or "",
        )
        return True
    return False


def _crm_preflight(offers: list, db, headless: bool, debug: bool) -> list:
    """Пре-флайт перед публікацією: один захід у CRM.

    1. Перевіряє актуальність кожного об'єкта: закриті в CRM → mark_skipped,
       виключаються зі списку на публікацію.
    2. Для «живих» з відсутніми локально фото — перезавантажує фото.

    Якщо CRM_EMAIL/CRM_PASSWORD не задані — перевірка та перезавантаження
    пропускаються, повертається вихідний список (публікація не блокується).

    Returns:
        Відфільтрований список «живих» оголошень.
    """
    crm_email = os.environ.get("CRM_EMAIL", "").strip()
    crm_password = os.environ.get("CRM_PASSWORD", "").strip()
    if not crm_email or not crm_password:
        logger.warning(
            "CRM_EMAIL/CRM_PASSWORD не задані — перевірка актуальності та "
            "перезавантаження фото пропущені (%d об'єктів публікуються як є)",
            len(offers),
        )
        return offers

    from crm_data_parser import CrmCredentials, CrmSession, EstateListCollector

    crm_creds = CrmCredentials(email=crm_email, password=crm_password)
    live: list = []
    with CrmSession(crm_creds, headless=headless, debug=debug) as crm:
        crm.login()
        collector = EstateListCollector(crm.page, debug=debug)

        # 1. Перевірка актуальності (статус закриття + ціна за один перехід)
        for offer in offers:
            try:
                actuality = collector.check_actuality(offer.estate_id)
            except Exception:
                # Збій перевірки одного об'єкта (таймаут, недоступна сторінка тощо)
                # не повинен зривати весь прогон. Fail-open: лишаємо об'єкт
                # «живим» — публікацію не блокуємо, як і за відсутності CRM-кредів.
                logger.warning(
                    "Не вдалось перевірити актуальність об'єкта %d (article=%s) — публікуємо як є",
                    offer.estate_id,
                    offer.article,
                    exc_info=True,
                )
                live.append(offer)
                continue
            _log_price_change(offer, actuality)
            if actuality.closed:
                logger.warning(
                    "Об'єкт %d (article=%s) закрито в CRM — пропускаємо публікацію",
                    offer.estate_id,
                    offer.article,
                )
                db.mark_skipped(offer.estate_id, "закрито в CRM")
            else:
                # Поповнити джерело фото з уже завантаженого HTML (для перезакачки)
                _backfill_source_photos(offer, actuality.photos, db)
                live.append(offer)

        logger.info(
            "Перевірка актуальності: %d живих, %d закрито",
            len(live),
            len(offers) - len(live),
        )

        # 2. Перезавантаження відсутніх фото для живих
        _redownload_photos_in_session(crm.page, live, db)

    return live


def _crm_recover_draft_photos(estate_ids: list[int], db, *, headless: bool, debug: bool) -> set[int]:
    """Фолбек на CRM: завантажити фото для чернеток без локальних файлів.

    Для кожного об'єкта: якщо немає збережених URL джерела
    (``apartment.source_photos``) — зчитати їх зі сторінки CRM через
    ``check_actuality`` і поповнити; далі перезавантажити фото на диск та оновити
    БД через :func:`_redownload_photos_in_session` (ті самі примітиви, що й у
    пре-флайті Фази 2).

    Якщо CRM_EMAIL/CRM_PASSWORD не задані (напр. поза мережею компанії) — фолбек
    пропускається з попередженням і повертається порожня множина.

    Returns:
        Множину estate_id, для яких після прогону з'явились локальні фото.
    """
    if not estate_ids:
        return set()

    crm_email = os.environ.get("CRM_EMAIL", "").strip()
    crm_password = os.environ.get("CRM_PASSWORD", "").strip()
    if not crm_email or not crm_password:
        logger.warning(
            "CRM_EMAIL/CRM_PASSWORD не задані — фолбек на CRM пропущено (%d чернеток лишаються без фото)",
            len(estate_ids),
        )
        return set()

    from crm_data_parser import CrmCredentials, CrmSession, EstateListCollector

    offers = [o for o in (db.get_offer(eid) for eid in estate_ids) if o]
    if not offers:
        logger.warning("Жодну з %d чернеток не зіставлено з БД — фолбек на CRM неможливий", len(estate_ids))
        return set()

    crm_creds = CrmCredentials(email=crm_email, password=crm_password)
    logger.info("Фолбек на CRM: завантаження фото для %d чернеток", len(offers))
    with CrmSession(crm_creds, headless=headless, debug=debug) as crm:
        crm.login()
        collector = EstateListCollector(crm.page, debug=debug)
        # Поповнити джерело фото для тих, у кого його ще немає (зібрано до фіксу).
        for offer in offers:
            if _photo_source_urls(offer.offer_data):
                continue
            try:
                actuality = collector.check_actuality(offer.estate_id)
            except Exception:
                logger.warning(
                    "Об'єкт %s: не вдалось зчитати сторінку CRM для джерела фото",
                    offer.article or offer.estate_id,
                    exc_info=True,
                )
                continue
            _backfill_source_photos(offer, actuality.photos, db)
        # Завантажити відсутні фото (оновлює offer.offer_data та БД на місці).
        _redownload_photos_in_session(crm.page, offers, db)

    recovered = {o.estate_id for o in offers if not _photos_missing(o.offer_data)}
    logger.info("Фолбек на CRM: відновлено локальні фото для %d з %d чернеток", len(recovered), len(offers))
    return recovered


# ── Phase 2: Rieltor posting ────────────────────────────────────────


def phase2_post(
    publish: bool = False,
    deal_type: str | None = None,
    property_type: str | None = None,
    max_count: int | None = None,
    headless: bool = True,
    debug: bool = False,
) -> int:
    """Опублікувати необроблені оголошення з БД на Rieltor.

    Returns:
        Кількість успішно опублікованих оголошень.
    """
    from crm_data_parser import cleanup_photos
    from offer_db import OfferDB
    from rieltor_handler import RieltorOfferPoster
    from rieltor_handler.new_offer_poster import (
        DictOfferFormFiller,
        FormValidationError,
    )
    from rieltor_handler.rieltor_session import RieltorErrorPageException

    phone = os.environ.get("PHONE", "").strip()
    password = os.environ.get("PASSWORD", "").strip()
    if not phone or not password:
        logger.error("PHONE та PASSWORD повинні бути задані в .env")
        return 0

    # Normalize deal_type filter for DB query
    db_deal_type = _normalize_deal_type(deal_type) if deal_type else None

    # Expand "Безкоштовне" to all free property types
    _FREE_TYPES = ["Будинок", "Комерційна", "Ділянка", "Паркомісце"]
    if property_type == "Безкоштовне":
        db_property_type: str | list[str] | None = _FREE_TYPES
    else:
        db_property_type = property_type

    def _matches_type_filter(pt: str, filt: str) -> bool:
        """Check if offer's property_type matches the CLI filter (handles Паркомісце variants)."""
        if filt == "Безкоштовне":
            return any(pt.lower() == f.lower() or pt.lower().startswith(f.lower() + "_") for f in _FREE_TYPES)
        if filt.lower() == "паркомісце":
            return pt.lower().startswith("паркомісце")
        return pt.lower() == filt.lower()

    published = 0  # успішно (без помилок валідації)
    failed = 0  # збережено/опубліковано з помилками валідації або винятком
    skipped = 0  # свідомо пропущено (напр. Будинок без номера)

    with OfferDB() as db:
        offers = db.get_unprocessed(
            deal_type=db_deal_type,
            property_type=db_property_type,
            max_count=max_count,
        )
        if not offers:
            logger.info("Необроблених оголошень у БД не знайдено")
            return 0

        # Pre-flight: відсіяти закриті в CRM + перезавантажити відсутні фото
        offers = _crm_preflight(offers, db, headless=headless, debug=debug)
        if not offers:
            logger.info("Після перевірки актуальності немає живих оголошень для публікації")
            return 0

        logger.info("Знайдено %d живих оголошень для публікації", len(offers))

        with RieltorOfferPoster(
            phone=phone,
            password=password,
            headless=headless,
            debug=debug,
        ) as poster:
            poster.login()

            for idx, offer in enumerate(offers, 1):
                offer_data: dict = {}
                try:
                    offer_data = offer.offer_data
                    pt = offer_data.get("property_type", "Квартира")
                    dt = offer_data.get("offer_type", "Продаж")

                    logger.info(
                        "[%d/%d] Публікація об'єкта %d (article=%s, %s/%s)...",
                        idx,
                        len(offers),
                        offer.estate_id,
                        offer.article,
                        dt,
                        pt,
                    )

                    # Guard: skip if offer type doesn't match the requested filter
                    if property_type and not _matches_type_filter(pt, property_type):
                        logger.warning(
                            "Пропуск %d: тип '%s' не відповідає фільтру '%s'",
                            offer.estate_id,
                            pt,
                            property_type,
                        )
                        continue

                    # Reconfigure filler for this offer's types
                    poster.property_type = pt
                    poster.deal_type = dt
                    poster.filler = DictOfferFormFiller(
                        poster.page,
                        property_type=pt,
                        deal_type=dt,
                        debug=debug,
                    )

                    _normalize_offer_data(offer_data)

                    # Skip Будинок offers without house number — can't geocode or find cadastral
                    if pt == "Будинок" and not offer_data.get("address", {}).get("Будинок", ""):
                        logger.warning(
                            "Об'єкт %d (article=%s): Будинок без номера будинку — пропускаємо",
                            offer.estate_id,
                            offer.article,
                        )
                        db.mark_skipped(offer.estate_id, "Будинок без номера будинку")
                        skipped += 1
                        continue

                    # Збагатити кадастровим номером якщо відсутній (phase 2 fallback)
                    from crm_data_parser.cadastral_lookup import enrich_offer_data_with_cadastral

                    if enrich_offer_data_with_cadastral(offer_data):
                        db.update_offer_data(offer.estate_id, offer_data)

                    poster.create_offer_draft(offer_data)

                    if publish:
                        report = poster.publish_and_get_report()
                    else:
                        report = poster.save_and_get_report()

                    rieltor_id = str(poster.last_saved_offer_id or "")

                    if report:
                        logger.warning(
                            "Об'єкт %d опубліковано з помилками валідації: %s",
                            offer.estate_id,
                            report,
                        )
                        logger.error(
                            "Дані об'єкта %d (article=%s):\n%s",
                            offer.estate_id,
                            offer.article,
                            json.dumps(offer_data, ensure_ascii=False, indent=2),
                        )
                        db.mark_failed(offer.estate_id, report)
                        failed += 1
                    else:
                        db.mark_posted(offer.estate_id, rieltor_id)
                        published += 1

                    if offer.article:
                        cleanup_photos(offer.article)

                except FormValidationError as e:
                    logger.error("Помилка валідації для об'єкта %d: %s", offer.estate_id, e)
                    logger.error(
                        "Дані об'єкта %d (article=%s):\n%s",
                        offer.estate_id,
                        offer.article,
                        json.dumps(offer_data, ensure_ascii=False, indent=2),
                    )
                    db.mark_failed(offer.estate_id, e.errors)
                    failed += 1

                except RieltorErrorPageException as e:
                    logger.warning("Сторінка помилки для об'єкта %d, повторна спроба...", offer.estate_id)
                    try:
                        poster.filler = DictOfferFormFiller(
                            poster.page,
                            property_type=pt,
                            deal_type=dt,
                            debug=debug,
                        )
                        poster.create_offer_draft(offer_data)
                        report = poster.publish_and_get_report() if publish else poster.save_and_get_report()
                        rieltor_id = str(poster.last_saved_offer_id or "")
                        if report:
                            logger.warning(
                                "Об'єкт %d (повтор): помилки валідації: %s",
                                offer.estate_id,
                                report,
                            )
                            db.mark_failed(offer.estate_id, report)
                            failed += 1
                        else:
                            db.mark_posted(offer.estate_id, rieltor_id)
                            if offer.article:
                                cleanup_photos(offer.article)
                            published += 1
                    except Exception as retry_e:
                        logger.error("Повтор для об'єкта %d не вдався: %s", offer.estate_id, retry_e)
                        logger.error(
                            "Дані об'єкта %d (article=%s):\n%s",
                            offer.estate_id,
                            offer.article,
                            json.dumps(offer_data, ensure_ascii=False, indent=2),
                        )
                        db.mark_failed(offer.estate_id, [{"error": str(e)}])
                        failed += 1

                except Exception:
                    logger.exception("Непередбачена помилка при публікації об'єкта %d", offer.estate_id)
                    logger.error(
                        "Дані об'єкта %d (article=%s):\n%s",
                        offer.estate_id,
                        offer.article,
                        json.dumps(offer_data, ensure_ascii=False, indent=2),
                    )
                    db.mark_failed(offer.estate_id, [{"error": "unexpected error"}])
                    failed += 1

    processed = published + failed + skipped
    action = "опубліковано" if publish else "збережено чернеток"
    logger.info(
        "Фаза 2 завершена: %s %d, з помилками валідації %d, пропущено %d (усього оброблено %d)",
        action,
        published,
        failed,
        skipped,
        processed,
    )
    return published


# ── cadastral: fill missing cadastral numbers in DB ─────────────────


def phase_cadastral(max_count: int | None = None) -> int:
    """Знайти кадастрові номери для об'єктів у БД, де вони відсутні.

    Працює для всіх статусів і всіх типів, що підтримують поле
    «Кадастровий номер» (Будинок, Ділянка, Комерційна).

    Returns:
        Кількість записів, для яких знайдено та збережено номер.
    """
    from crm_data_parser.cadastral_lookup import enrich_offer_data_with_cadastral
    from offer_db import OfferDB

    # property_type in DB stores schema names (title-case); SQLite LOWER() is ASCII-only.
    property_type_filter = ["Будинок", "Ділянка", "Комерційна"]

    updated = 0
    with OfferDB() as db:
        offers = db.get_without_cadastral(property_types=property_type_filter)
        if max_count:
            offers = offers[:max_count]

        logger.info(
            "Об'єктів без кадастрового номера: %d (перевіряємо %d)",
            len(offers),
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


# ── clean-trash: bulk-delete rieltor.ua «Закрита база» ──────────────


def phase_clean_trash(
    max_count: int | None = None,
    deleted_max_count: int | None = None,
    dry_run: bool = False,
    skip_deleted: bool = False,
    deleted_only: bool = False,
    headless: bool = True,
    debug: bool = False,
) -> int:
    """Масово очистити сміття на rieltor.ua у дві стадії.

    Стадія 1 — «Закрита база» (об'єкти переходять у «Видалені»).
    Стадія 2 — «Видалені» (остаточне видалення).

    Args:
        max_count:         ліміт видалень стадії 1 (None = всі).
        deleted_max_count: ліміт видалень стадії 2 (None = всі).
        dry_run:           лише підрахунок, нічого не видаляти.
        skip_deleted:      виконати лише стадію 1 (не чіпати «Видалені»).
        deleted_only:      виконати лише стадію 2 (очистити «Видалені» назавжди).

    Returns:
        Сумарна кількість видалених об'єктів (або наявних при dry_run).
    """
    from rieltor_handler.closed_base_cleaner import ClosedBaseCleaner
    from rieltor_handler.rieltor_session import RieltorCredentials, RieltorSession

    phone = os.environ.get("PHONE", "").strip()
    password = os.environ.get("PASSWORD", "").strip()
    if not phone or not password:
        logger.error("PHONE та PASSWORD повинні бути задані в .env")
        return 0

    total = 0
    with RieltorSession(
        RieltorCredentials(phone=phone, password=password),
        headless=headless,
        debug=debug,
    ) as session:
        session.login()
        cleaner = ClosedBaseCleaner(session.page)

        if not deleted_only:
            total += cleaner.clean(max_count=max_count, dry_run=dry_run)
        if not skip_deleted:
            total += cleaner.purge_deleted(max_count=deleted_max_count, dry_run=dry_run)

    logger.info("clean-trash завершено: %d", total)
    return total


# ── prune-stale: зняти неактуальні опубліковані з реклами ───────────


def phase_prune_stale(
    max_count: int | None = None,
    deal_type: str | None = None,
    property_type: str | None = None,
    dry_run: bool = False,
    headless: bool = True,
    debug: bool = False,
) -> int:
    """Зняти з реклами опубліковані об'єкти, що закрилися в CRM (→ «Закрита база»).

    Два проходи (щоб не тримати два браузери одночасно):
      1. CRM — серед опублікованих (status='posted') знайти закриті.
      2. Rieltor — перенести знайдені у «Закриту базу», у БД → skipped.

    Returns:
        Кількість знятих об'єктів (або кандидатів при dry_run).
    """
    from crm_data_parser import CrmCredentials, CrmSession, EstateListCollector
    from offer_db import OfferDB
    from rieltor_handler import PublishedOfferUnpublisher
    from rieltor_handler.rieltor_session import RieltorCredentials, RieltorSession

    crm_email = os.environ.get("CRM_EMAIL", "").strip()
    crm_password = os.environ.get("CRM_PASSWORD", "").strip()
    phone = os.environ.get("PHONE", "").strip()
    password = os.environ.get("PASSWORD", "").strip()
    if not crm_email or not crm_password:
        logger.error("CRM_EMAIL та CRM_PASSWORD повинні бути задані в .env")
        return 0
    if not phone or not password:
        logger.error("PHONE та PASSWORD повинні бути задані в .env")
        return 0

    db_deal_type = _normalize_deal_type(deal_type) if deal_type else None

    with OfferDB() as db:
        posted = db.get_posted()
        if db_deal_type:
            posted = [o for o in posted if (o.deal_type or "").lower() == db_deal_type.lower()]
        if property_type:
            posted = [o for o in posted if (o.property_type or "").lower() == property_type.lower()]
        if not posted:
            logger.info("Немає опублікованих об'єктів для перевірки")
            return 0

        logger.info("Перевірка актуальності %d опублікованих об'єктів...", len(posted))

        # Прохід 1 (CRM): зібрати закриті
        stale: list = []
        crm_creds = CrmCredentials(email=crm_email, password=crm_password)
        with CrmSession(crm_creds, headless=headless, debug=debug) as crm:
            crm.login()
            collector = EstateListCollector(crm.page, debug=debug)
            for offer in posted:
                actuality = collector.check_actuality(offer.estate_id)
                _log_price_change(offer, actuality)
                if actuality.closed:
                    logger.info(
                        "Об'єкт %d (rieltor_id=%s) закрито в CRM — кандидат на зняття",
                        offer.estate_id,
                        offer.rieltor_offer_id,
                    )
                    stale.append(offer)

        if max_count is not None:
            stale = stale[:max_count]

        logger.info("Закрилося в CRM: %d об'єктів", len(stale))
        if not stale:
            return 0

        if dry_run:
            for offer in stale:
                logger.info(
                    "[dry-run] Зняв би rieltor_id=%s (estate %d, article=%s)",
                    offer.rieltor_offer_id,
                    offer.estate_id,
                    offer.article,
                )
            return len(stale)

        # Прохід 2 (Rieltor): зняти з реклами
        rid_to_estate = {o.rieltor_offer_id: o.estate_id for o in stale}
        with RieltorSession(
            RieltorCredentials(phone=phone, password=password),
            headless=headless,
            debug=debug,
        ) as session:
            session.login()
            unpublisher = PublishedOfferUnpublisher(session.page)
            done = unpublisher.unpublish_offers([o.rieltor_offer_id for o in stale])

        for rid in done:
            estate_id = rid_to_estate.get(rid)
            if estate_id is not None:
                db.mark_skipped(estate_id, "закрито в CRM, знято з реклами")

        logger.info("prune-stale завершено: знято %d об'єктів", len(done))
        return len(done)


# ── publish-drafts: bulk-publish rieltor.ua drafts ──────────────────


def _build_crm_actuality_skip_fn(stack, dry_run: bool, headless: bool, debug: bool):
    """Скласти предикат key -> bool для перевірки актуальності чернеток у CRM.

    Зв'язок rieltor_id ↔ estate_id береться з БД. Предикат повертає True, якщо
    чернетку треба пропустити (об'єкт закрито в CRM); закриті позначаються в БД
    як skipped (окрім dry_run). Чернетки, яких немає в БД, не перевіряються
    (предикат повертає False — публікуються як є).

    Відкриває CRM-сесію та БД у переданому ``ExitStack``. Повертає None, якщо
    креди CRM не задані — тоді перевірка вимикається, а публікація не блокується.
    """
    crm_email = os.environ.get("CRM_EMAIL", "").strip()
    crm_password = os.environ.get("CRM_PASSWORD", "").strip()
    if not crm_email or not crm_password:
        logger.warning("CRM_EMAIL/CRM_PASSWORD не задані — перевірка актуальності чернеток пропущена")
        return None

    from crm_data_parser import CrmCredentials, CrmSession, EstateListCollector
    from offer_db import OfferDB

    db = stack.enter_context(OfferDB())
    posted = db.get_posted()
    rid_to_estate = {o.rieltor_offer_id: o.estate_id for o in posted}
    rid_to_offer = {o.rieltor_offer_id: o for o in posted}
    crm = stack.enter_context(
        CrmSession(CrmCredentials(email=crm_email, password=crm_password), headless=headless, debug=debug)
    )
    crm.login()
    collector = EstateListCollector(crm.page, debug=debug)
    cache: dict[int, bool] = {}

    def _skip(key: str) -> bool:
        estate_id = rid_to_estate.get(key)
        if estate_id is None:
            return False  # немає в БД — публікуємо без перевірки
        closed = cache.get(estate_id)
        if closed is None:
            actuality = collector.check_actuality(estate_id)
            offer = rid_to_offer.get(key)
            if offer is not None:
                _log_price_change(offer, actuality)
            closed = actuality.closed
            cache[estate_id] = closed
        if closed and not dry_run:
            db.mark_skipped(estate_id, "закрито в CRM")
        return closed

    return _skip


def phase_publish_drafts(
    count_only: bool = False,
    max_count: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    delay: float = 3.0,
    dry_run: bool = False,
    check_actuality: bool = False,
    headless: bool = True,
    debug: bool = False,
) -> int:
    """Підрахувати або масово опублікувати чернетки на rieltor.ua.

    check_actuality — якщо True, перед публікацією кожної чернетки перевіряє в
    CRM, чи об'єкт не закрито (закриті не публікуються, позначаються skipped).
    Без CRM-кред перевірка пропускається, публікація не блокується.
    """
    import contextlib
    import datetime as dt

    from rieltor_handler.drafts_publisher import DraftsPublisher
    from rieltor_handler.rieltor_session import RieltorCredentials, RieltorSession

    phone = os.environ.get("PHONE", "").strip()
    password = os.environ.get("PASSWORD", "").strip()
    if not phone or not password:
        logger.error("PHONE та PASSWORD повинні бути задані в .env")
        return 0

    try:
        d_from = dt.date.fromisoformat(date_from) if date_from else None
        d_to = dt.date.fromisoformat(date_to) if date_to else None
    except ValueError as e:
        logger.error("Невірний формат дати: %s. Очікується YYYY-MM-DD", e)
        return 0

    with (
        extra_file_handler(DRAFTS_LOG_FILE),
        RieltorSession(
            RieltorCredentials(phone=phone, password=password),
            headless=headless,
            debug=debug,
        ) as session,
    ):
        session.login()
        publisher = DraftsPublisher(session.page)

        if count_only:
            n = publisher.count()
            write_drafts_count(n)
            logger.info("Чернеток на сайті: %d (записано у %s)", n, DRAFTS_COUNT_FILE)
            return n

        with contextlib.ExitStack() as stack:
            skip_fn = _build_crm_actuality_skip_fn(stack, dry_run, headless, debug) if check_actuality else None
            return publisher.publish_drafts(
                max_count=max_count,
                date_from=d_from,
                date_to=d_to,
                delay_sec=delay,
                dry_run=dry_run,
                skip_fn=skip_fn,
            )


# ── sync-status: reconcile DB statuses with the live site ───────────


def phase_sync_status(headless: bool = True, debug: bool = False) -> dict:
    """Звірити статуси в БД з реальним станом на rieltor.ua (лише звіт, без змін).

    Зчитує вкладки «Опубліковані» (mode=10) і «Чернетки», зіставляє за
    ``rieltor_offer_id`` із записами БД і друкує розбіжності. БД НЕ змінюється
    (dry-run); застосування змін — окремим кроком у майбутньому.
    """
    import status_sync
    from offer_db import OfferDB
    from rieltor_handler import PublishedOfferUnpublisher
    from rieltor_handler.drafts_publisher import DraftsPublisher
    from rieltor_handler.rieltor_session import RieltorCredentials, RieltorSession

    phone = os.environ.get("PHONE", "").strip()
    password = os.environ.get("PASSWORD", "").strip()
    if not phone or not password:
        logger.error("PHONE та PASSWORD повинні бути задані в .env")
        return {}

    with OfferDB() as db:
        db_offers = [
            {
                "estate_id": o.estate_id,
                "article": o.article,
                "status": o.status,
                "rieltor_offer_id": o.rieltor_offer_id,
            }
            for o in db.get_all()
        ]
    logger.info("У БД оголошень: %d", len(db_offers))

    with RieltorSession(
        RieltorCredentials(phone=phone, password=password),
        headless=headless,
        debug=debug,
    ) as session:
        session.login()
        published_ids = PublishedOfferUnpublisher(session.page).collect_published_ids()
        draft_ids = [r.key for r in DraftsPublisher(session.page)._collect_rows()]
    logger.info("Зчитано з сайту: опублікованих=%d, чернеток=%d", len(published_ids), len(draft_ids))

    report = status_sync.reconcile_statuses(db_offers, published_ids, draft_ids)
    counts = status_sync.summary_counts(report)
    logger.info(
        "Звірка (сайт ↔ БД): на сайті опубл.=%d, чернетки=%d, "
        "БД 'posted' без сайту=%d, на сайті без БД=%d, без rieltor_id=%d",
        counts["published_on_site"],
        counts["draft_on_site"],
        counts["posted_missing_from_site"],
        counts["site_unknown_to_db"],
        counts["unmatchable"],
    )

    def _fmt(o: dict) -> str:
        return f"estate {o['estate_id']} (article={o.get('article')}, rid={o.get('rieltor_offer_id')})"

    if report.posted_missing_from_site:
        logger.warning(
            "БД 'posted', але на сайті НЕ знайдено (%d): %s",
            len(report.posted_missing_from_site),
            ", ".join(_fmt(o) for o in report.posted_missing_from_site),
        )
    if report.draft_on_site:
        logger.info(
            "Ще чернетки на сайті, не опубліковані (%d): %s",
            len(report.draft_on_site),
            ", ".join(_fmt(o) for o in report.draft_on_site),
        )
    if report.site_unknown_to_db:
        logger.warning(
            "На сайті є, а в БД немає rieltor_id (%d): %s",
            len(report.site_unknown_to_db),
            ", ".join(report.site_unknown_to_db),
        )
    if report.unmatchable:
        logger.info(
            "БД new/failed/skipped без rieltor_id — за id не звірити (%d): %s",
            len(report.unmatchable),
            ", ".join(_fmt(o) for o in report.unmatchable),
        )
    logger.info("Звірка завершена (dry-run, БД не змінено).")
    return counts


# ── fix-draft-photos: re-upload photos into photoless drafts ─────────


def phase_fix_draft_photos(
    apply: bool = False,
    max_count: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    headless: bool = True,
    debug: bool = False,
) -> dict:
    """Дозалити фото у чернетки rieltor.ua, що збереглися без фото.

    Перше джерело фото — локальні файли (перерахунок шляху на поточний PICS_DIR,
    стійко до переносу встановлення). Якщо локальних файлів немає, з ``apply=True``
    і заданими CRM-кредами — фолбек на CRM: фото завантажуються з CRM, після чого
    чернетки дозаливаються другим заходом у rieltor.ua. Без CRM-кредів (напр. поза
    мережею компанії) фолбек пропускається. dry-run за замовчуванням.

    Браузери не відкриваються одночасно: rieltor-захід (дозаливання з диску) →
    CRM-захід (завантаження фото) → повторний rieltor-захід (дозаливання
    відновлених) — як у prune-stale.
    """
    import datetime as dt

    from offer_db import OfferDB
    from rieltor_handler.draft_photo_fixer import DraftPhotoFixer
    from rieltor_handler.drafts_publisher import DraftsPublisher
    from rieltor_handler.rieltor_session import RieltorCredentials, RieltorSession

    phone = os.environ.get("PHONE", "").strip()
    password = os.environ.get("PASSWORD", "").strip()
    if not phone or not password:
        logger.error("PHONE та PASSWORD повинні бути задані в .env")
        return {}

    try:
        d_from = dt.date.fromisoformat(date_from) if date_from else None
        d_to = dt.date.fromisoformat(date_to) if date_to else None
    except ValueError as e:
        logger.error("Невірний формат дати: %s. Очікується YYYY-MM-DD", e)
        return {}

    creds = RieltorCredentials(phone=phone, password=password)

    with OfferDB() as db:
        # Захід 1 — rieltor: дозалити з локальних файлів; зібрати CRM-цілі.
        with RieltorSession(creds, headless=headless, debug=debug) as session:
            session.login()
            fixer = DraftPhotoFixer(session.page, db, dry_run=not apply)
            rows = fixer.list_draft_rows()
            if d_from or d_to:
                rows = [r for r in rows if DraftsPublisher._in_date_range(r[2], d_from, d_to)]
                logger.info("Після фільтра дат лишилось чернеток: %d", len(rows))
            summary = fixer.fix_drafts(rows=rows, max_count=max_count)

        # Фолбек на CRM + повторний захід — лише за реальних дій і наявних цілей.
        if apply and summary.crm_targets:
            recovered = _crm_recover_draft_photos(
                [eid for eid, _, _ in summary.crm_targets], db, headless=headless, debug=debug
            )
            pass2_rows = [(rid, href, None) for eid, rid, href in summary.crm_targets if eid in recovered]
            if pass2_rows:
                # Захід 2 — rieltor: дозалити фото, завантажені з CRM.
                with RieltorSession(creds, headless=headless, debug=debug) as session2:
                    session2.login()
                    fixer2 = DraftPhotoFixer(session2.page, db, dry_run=False)
                    summary2 = fixer2.fix_drafts(rows=pass2_rows)
                # Звести результати другого заходу в один підсумок.
                summary.fixed.extend(summary2.fixed)
                summary.errors.extend(summary2.errors)
            # Відновлені вже не потребують CRM — прибрати їх із needs_crm.
            summary.needs_crm = [eid for eid in summary.needs_crm if eid not in recovered]

    counts = summary.counts()
    logger.info(
        "Дозаливання фото (%s): дозалито=%d, вже повні=%d, потрібен CRM=%d, не зіставлено з БД=%d, помилок=%d",
        "apply" if apply else "dry-run",
        counts["fixed"],
        counts["already"],
        counts["needs_crm"],
        counts["no_db"],
        counts["errors"],
    )
    if summary.needs_crm:
        logger.warning(
            "Без локального джерела фото — потрібен CRM (%d): %s",
            len(summary.needs_crm),
            ", ".join(map(str, summary.needs_crm)),
        )
    if summary.no_db:
        logger.warning(
            "Не зіставлено з БД (%d): %s",
            len(summary.no_db),
            ", ".join(map(str, summary.no_db)),
        )
    if summary.errors:
        logger.warning("Помилки (%d): %s", len(summary.errors), ", ".join(map(str, summary.errors)))
    return counts


# ── clean-pics: local photo folder maintenance ──────────────────────


def _classify_pic_folder(name: str, keep_articles: set[str], known_articles: set[str], all_pics: bool) -> str | None:
    """Причина видалити папку артикула (рядок) або None, якщо зберегти.

    keep_articles / known_articles — артикули в нижньому регістрі.
    - all_pics=True  → завжди "all" (знести все);
    - артикул у keep (статус 'new')   → None (фото ще потрібні для постингу);
    - артикула немає в БД             → "orphan";
    - інакше (posted/failed/skipped)  → "not-new".
    """
    if all_pics:
        return "all"
    art = name.strip().lower()
    if art in keep_articles:
        return None
    return "orphan" if art not in known_articles else "not-new"


def _dir_size_bytes(path: Path) -> int:
    """Сумарний розмір файлів у каталозі (стійко до зниклих/заблокованих файлів)."""
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def phase_clean_pics(*, all_pics: bool = False, dry_run: bool = False) -> dict:
    """Очистити локальну папку фото (pics), звільнивши місце.

    За замовчуванням видаляє папки артикулів, фото яких більше не потрібні
    локально: усі НЕ у статусі 'new' (posted/failed/skipped) та осиротілі
    (артикула немає в БД). Папки об'єктів у статусі 'new' зберігаються — їхні
    фото ще потрібні для постингу. Якщо їх усе ж видалити (--all), preflight
    Фази 2 перезавантажить фото з CRM, але лише в мережі компанії з CRM-кредами.

    Args:
        all_pics: знести всі папки (сценарій «зняти все й зібрати наново»).
        dry_run: лише показати план і скільки звільниться, нічого не видаляти.

    Returns:
        Підсумок {'deleted', 'kept', 'orphans', 'freed_bytes', 'kept_bytes'}.
    """
    import shutil

    from crm_data_parser.photo_downloader import PICS_DIR
    from offer_db import OfferDB

    empty = {"deleted": 0, "kept": 0, "orphans": 0, "freed_bytes": 0, "kept_bytes": 0}
    if not PICS_DIR.exists():
        logger.info("Папки фото немає (%s) — нічого очищати", PICS_DIR)
        return empty

    folders = [d for d in PICS_DIR.iterdir() if d.is_dir()]
    if not folders:
        logger.info("У папці фото %s немає підпапок артикулів", PICS_DIR)
        return empty

    # Артикули об'єктів у черзі на постинг (status='new') — їхні фото зберігаємо.
    with OfferDB() as db:
        keep_articles = {o.article.strip().lower() for o in db.get_unprocessed() if o.article}
        known_articles = {o.article.strip().lower() for o in db.get_all() if o.article}

    deleted = kept = orphans = 0
    freed = kept_bytes = 0
    for folder in folders:
        reason = _classify_pic_folder(folder.name, keep_articles, known_articles, all_pics)
        size = _dir_size_bytes(folder)
        if reason is None:
            kept += 1
            kept_bytes += size
            continue
        if reason == "orphan":
            orphans += 1
        if dry_run:
            logger.info("[dry-run] видалив би %s (%s, %.1f МБ)", folder.name, reason, size / 1e6)
            deleted += 1
            freed += size
            continue
        try:
            shutil.rmtree(folder)
        except Exception:
            logger.warning("Не вдалось видалити %s", folder, exc_info=True)
            continue
        deleted += 1
        freed += size
        logger.info("Видалено %s (%s, %.1f МБ)", folder.name, reason, size / 1e6)

    logger.info(
        "Очищення pics (%s%s): видалено=%d (осиротілих=%d), збережено(new)=%d, звільнено=%.2f ГБ, лишилось=%.2f ГБ",
        "ALL" if all_pics else "non-new+orphan",
        ", dry-run" if dry_run else "",
        deleted,
        orphans,
        kept,
        freed / 1e9,
        kept_bytes / 1e9,
    )
    return {
        "deleted": deleted,
        "kept": kept,
        "orphans": orphans,
        "freed_bytes": freed,
        "kept_bytes": kept_bytes,
    }


# ── post-one: single offer from JSON ────────────────────────────────


def post_single_offer(
    offer_source: str,
    publish: bool = False,
    headless: bool = True,
    debug: bool = False,
) -> None:
    """Опублікувати одне оголошення з JSON-рядка або файлу.

    Не використовує базу даних — призначено для тестування та ручної публікації.
    """
    from rieltor_handler import RieltorOfferPoster

    # Parse offer_data from string or file
    source_path = Path(offer_source)
    if source_path.exists() and source_path.is_file():
        logger.info("Завантаження даних оголошення з файлу: %s", source_path)
        offer_data = json.loads(source_path.read_text(encoding="utf-8"))
    else:
        offer_data = json.loads(offer_source)

    pt = offer_data.get("property_type", "Квартира")
    dt = offer_data.get("offer_type", "Продаж")

    phone = os.environ.get("PHONE", "").strip()
    password = os.environ.get("PASSWORD", "").strip()
    if not phone or not password:
        logger.error("PHONE та PASSWORD повинні бути задані в .env")
        return

    with RieltorOfferPoster(
        phone=phone,
        password=password,
        property_type=pt,
        deal_type=dt,
        headless=headless,
        debug=debug,
    ) as poster:
        poster.login()
        _normalize_offer_data(offer_data)
        poster.create_offer_draft(offer_data)

        if publish:
            report = poster.publish_and_get_report()
        else:
            report = poster.save_and_get_report()

        rieltor_id = poster.last_saved_offer_id
        if report:
            logger.warning("Опубліковано з помилками валідації: %s", report)
        logger.info("Оголошення опубліковано, rieltor_id=%s", rieltor_id)


# ── CLI ──────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    # Global flags (--debug, --headless/--no-headless) are accepted both BEFORE
    # and AFTER the subcommand. The subparsers share them via `common`, whose
    # actions use SUPPRESS defaults so an absent flag on a subparser does NOT
    # clobber a value parsed before the subcommand. The main parser gets its OWN
    # distinct actions (real defaults None/False) — do NOT add `common` as a
    # parent here and do NOT call set_defaults() for these dests: both would
    # mutate the shared SUPPRESS actions back to a concrete default and
    # re-introduce the clobber.
    _headless_help = "Run browser headless (default). Use --no-headless to show the window. Env: HEADLESS=false."
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--debug", action="store_true", default=argparse.SUPPRESS, help="Enable debug logging")
    common.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=argparse.SUPPRESS,
        help=_headless_help,
    )

    parser = argparse.ArgumentParser(
        description="Rieltor offer automation: CRM → parse → post",
    )
    parser.add_argument("--debug", action="store_true", default=False, help="Enable debug logging")
    # Headless is the default. Use --no-headless for a visible window; HEADLESS env
    # var (true/false) overrides when neither flag is passed.
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=_headless_help,
    )

    sub = parser.add_subparsers(dest="command")

    # collect
    p_collect = sub.add_parser("collect", parents=[common], help="Phase 1: collect from CRM into DB")
    p_collect.add_argument("--max-pages", type=int, help="Max CRM pagination pages")
    p_collect.add_argument("--max-count", type=int, help="Max offers to collect")
    p_collect.add_argument("--deal-type", help="Filter: sell or lease")
    p_collect.add_argument(
        "--property-type",
        help="Filter: Квартира, Будинок, etc., or Безкоштовне (all free-to-post types)",
    )

    # post
    p_post = sub.add_parser("post", parents=[common], help="Phase 2: post from DB to Rieltor")
    p_post.add_argument("--publish", action="store_true", help="Publish instead of draft")
    p_post.add_argument("--max-count", type=int, help="Max offers to post")
    p_post.add_argument("--deal-type", help="Filter: sell or lease")
    p_post.add_argument("--property-type", help="Filter: Квартира, Будинок, etc.")

    # post-one
    p_one = sub.add_parser("post-one", parents=[common], help="Post a single offer from JSON")
    p_one.add_argument("source", help="JSON string or path to .json file")
    p_one.add_argument("--publish", action="store_true", help="Publish instead of draft")

    # cadastral
    p_cad = sub.add_parser("cadastral", parents=[common], help="Fill missing cadastral numbers in DB")
    p_cad.add_argument("--max-count", type=int, help="Max offers to process")

    # repair-failed (БД-сервіс)
    p_repair = sub.add_parser(
        "repair-failed",
        parents=[common],
        help="Repair failed offers via район/cadastral lookup and requeue the fixed ones",
    )
    p_repair.add_argument("--max-count", type=int, help="Max failed offers to process")

    # clean-trash
    p_clean = sub.add_parser(
        "clean-trash",
        parents=[common],
        help="Bulk-clean rieltor.ua: «Закрита база» → «Видалені» → permanently",
    )
    p_clean.add_argument("--max-count", type=int, help="Max stage-1 deletions («Закрита база»)")
    p_clean.add_argument("--deleted-max-count", type=int, help="Max stage-2 deletions («Видалені» назавжди)")
    p_clean.add_argument("--dry-run", action="store_true", help="Count only, delete nothing")
    p_clean.add_argument("--skip-deleted", action="store_true", help="Stage 1 only (do not purge «Видалені»)")
    p_clean.add_argument("--deleted-only", action="store_true", help="Stage 2 only (purge «Видалені» permanently)")

    # publish-drafts
    p_pub = sub.add_parser("publish-drafts", parents=[common], help="Bulk-publish drafts on rieltor.ua")
    p_pub.add_argument("--count-only", action="store_true", help="Лише порахувати -> tmp/drafts_count.json")
    p_pub.add_argument("--max-count", type=int, help="Макс. кількість публікацій")
    p_pub.add_argument("--date-from", help="Дата з (YYYY-MM-DD)")
    p_pub.add_argument("--date-to", help="Дата по (YYYY-MM-DD)")
    p_pub.add_argument("--delay", type=float, default=3.0, help="Базова затримка між публікаціями, с")
    p_pub.add_argument("--dry-run", action="store_true", help="Лише відібрати, нічого не публікувати")
    p_pub.add_argument(
        "--check-actuality",
        action="store_true",
        help="Перевіряти в CRM, чи об'єкт не закрито (закриті чернетки не публікуються)",
    )

    # prune-stale
    p_prune = sub.add_parser(
        "prune-stale",
        parents=[common],
        help="Зняти з реклами опубліковані об'єкти, що закрилися в CRM (→ «Закрита база»)",
    )
    p_prune.add_argument("--max-count", type=int, help="Макс. кількість знятих")
    p_prune.add_argument("--deal-type", help="Фільтр: sell або lease")
    p_prune.add_argument("--property-type", help="Фільтр: Квартира, Будинок тощо")
    p_prune.add_argument("--dry-run", action="store_true", help="Лише показати кандидатів, нічого не знімати")

    # sync-status
    sub.add_parser(
        "sync-status",
        parents=[common],
        help="Звірити статуси БД з реальним станом на rieltor.ua (опубліковані + чернетки), лише звіт",
    )

    # fix-draft-photos
    p_fix = sub.add_parser(
        "fix-draft-photos",
        parents=[common],
        help="Дозалити фото у чернетки rieltor.ua, що збереглися без фото (dry-run за замовч.)",
    )
    p_fix.add_argument("--apply", action="store_true", help="Реально дозаливати фото (без --apply — лише звіт)")
    p_fix.add_argument("--max-count", type=int, help="Макс. кількість опрацьованих чернеток без фото")
    p_fix.add_argument("--date-from", help="Дата з (YYYY-MM-DD)")
    p_fix.add_argument("--date-to", help="Дата по (YYYY-MM-DD)")

    # clean-pics
    p_clean_pics = sub.add_parser(
        "clean-pics",
        parents=[common],
        help="Очистити локальну папку фото (pics): прибрати непотрібні (не 'new') та осиротілі папки",
    )
    p_clean_pics.add_argument(
        "--all",
        dest="all_pics",
        action="store_true",
        help="Знести ВСІ папки фото (для «зняти все й зібрати наново»)",
    )
    p_clean_pics.add_argument("--dry-run", action="store_true", help="Лише показати план і звільнене місце")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.debug:
        init_logging(level="DEBUG", filename="logs/rieltor.log")

    # Default: headless ON. Precedence: explicit --headless/--no-headless flag,
    # then HEADLESS env var, then default True.
    if args.headless is not None:
        headless = args.headless
    else:
        headless = os.getenv("HEADLESS", "true").strip().lower() not in ("false", "0", "no", "off")

    logger.info("=== Автоматизацію Rieltor запущено ===")

    try:
        if args.command == "collect":
            with extra_file_handler(CRM_PARSE_LOG_FILE):
                phase1_collect(
                    max_pages=args.max_pages,
                    max_count=args.max_count,
                    deal_type=args.deal_type,
                    property_type=args.property_type,
                    headless=headless,
                    debug=args.debug,
                )

        elif args.command == "post":
            with extra_file_handler(PUBLISH_LOG_FILE):
                phase2_post(
                    publish=args.publish,
                    deal_type=args.deal_type,
                    property_type=args.property_type,
                    max_count=args.max_count,
                    headless=headless,
                    debug=args.debug,
                )

        elif args.command == "post-one":
            with extra_file_handler(PUBLISH_LOG_FILE):
                post_single_offer(
                    offer_source=args.source,
                    publish=args.publish,
                    headless=headless,
                    debug=args.debug,
                )

        elif args.command == "cadastral":
            with extra_file_handler(DB_SERVICE_LOG_FILE):
                phase_cadastral(max_count=args.max_count)

        elif args.command == "repair-failed":
            from repair import repair_failed_offers

            with extra_file_handler(DB_SERVICE_LOG_FILE):
                repair_failed_offers(max_count=args.max_count)

        elif args.command == "clean-trash":
            phase_clean_trash(
                max_count=args.max_count,
                deleted_max_count=args.deleted_max_count,
                dry_run=args.dry_run,
                skip_deleted=args.skip_deleted,
                deleted_only=args.deleted_only,
                headless=headless,
                debug=args.debug,
            )

        elif args.command == "publish-drafts":
            phase_publish_drafts(
                count_only=args.count_only,
                max_count=args.max_count,
                date_from=args.date_from,
                date_to=args.date_to,
                delay=args.delay,
                dry_run=args.dry_run,
                check_actuality=args.check_actuality,
                headless=headless,
                debug=args.debug,
            )

        elif args.command == "prune-stale":
            phase_prune_stale(
                max_count=args.max_count,
                deal_type=args.deal_type,
                property_type=args.property_type,
                dry_run=args.dry_run,
                headless=headless,
                debug=args.debug,
            )

        elif args.command == "sync-status":
            with extra_file_handler(SYNC_LOG_FILE):
                phase_sync_status(headless=headless, debug=args.debug)

        elif args.command == "fix-draft-photos":
            with extra_file_handler(FIX_PHOTOS_LOG_FILE):
                phase_fix_draft_photos(
                    apply=args.apply,
                    max_count=args.max_count,
                    date_from=args.date_from,
                    date_to=args.date_to,
                    headless=headless,
                    debug=args.debug,
                )

        elif args.command == "clean-pics":
            with extra_file_handler(DB_SERVICE_LOG_FILE):
                phase_clean_pics(all_pics=args.all_pics, dry_run=args.dry_run)

        else:
            # No subcommand = full pipeline (collect + post draft) — кожна фаза у свій лог
            with extra_file_handler(CRM_PARSE_LOG_FILE):
                phase1_collect(headless=headless, debug=args.debug)
            with extra_file_handler(PUBLISH_LOG_FILE):
                phase2_post(publish=False, headless=headless, debug=args.debug)

        # Print summary
        from offer_db import OfferDB

        with OfferDB() as db:
            summary = db.summary()
        if summary:
            logger.info("=== Зведення БД: %s ===", summary)

    except KeyboardInterrupt:
        logger.info("Перервано користувачем")
        sys.exit(1)
    except Exception:
        logger.exception("Критична помилка")
        sys.exit(1)

    logger.info("=== Завершено ===")


if __name__ == "__main__":
    main()
