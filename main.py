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

from setup_logger import init_logging, setup_logger

load_dotenv()


init_logging(
    level=os.getenv("LOG_LEVEL", "INFO"),
    filename="logs/rieltor.log",
    clear_on_start=True,
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

        items = collector.collect_advertisable(max_pages=max_pages)
        logger.info("Зібрано %d рекламованих об'єктів з CRM", len(items))

        # Apply filters
        if deal_type:
            normalized = _normalize_deal_type(deal_type)
            if normalized:
                items = [i for i in items if i.deal_type and i.deal_type.lower() == normalized.lower()]
                logger.info("Відфільтровано за deal_type=%s: %d елементів", normalized, len(items))

        if property_type:
            from crm_data_parser.html_parser import CRM_TYPE_TO_SCHEMA
            pt_lower = property_type.lower()

            def _matches_property_type(item) -> bool:
                crm_type = (item.property_type or "").lower()
                # Direct match: e.g. "Будинок" == "будинок"
                if crm_type == pt_lower:
                    return True
                # Schema match: e.g. "Таунхаус"/"Дуплекс" → "Будинок"
                return CRM_TYPE_TO_SCHEMA.get(crm_type, "").lower() == pt_lower

            items = [i for i in items if _matches_property_type(i)]
            logger.info("Відфільтровано за property_type=%s: %d елементів", property_type, len(items))

        for idx, item in enumerate(items, 1):
            if max_count and saved >= max_count:
                logger.info("Досягнуто ліміту %d нових збережених об'єктів, зупинка", max_count)
                break

            if db.estate_exists(item.estate_id):
                logger.info(
                    "[%d/%d] Об'єкт %d вже є в БД, пропускаємо",
                    idx,
                    len(items),
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
                        "[%d/%d] Об'єкт %d закрито, пропущено",
                        idx,
                        len(items),
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
                if photo_dl_link:
                    local_paths = download_watermark_zip(crm.page, photo_dl_link, article)
                    if not local_paths and photo_urls:
                        logger.warning("Watermark ZIP порожній, використовуємо окремі фото для %s", article)
                        local_paths = download_estate_photos(crm.page, photo_urls, article)
                elif photo_urls:
                    local_paths = download_estate_photos(crm.page, photo_urls, article)
                else:
                    local_paths = []
                if local_paths:
                    if "apartment" not in offer_data:
                        offer_data["apartment"] = {}
                    offer_data["apartment"]["photos"] = local_paths

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
                    "[%d/%d] Збережено об'єкт %d (article=%s)",
                    idx,
                    len(items),
                    item.estate_id,
                    article,
                )

            except Exception:
                logger.exception(
                    "[%d/%d] Помилка обробки об'єкта %d",
                    idx,
                    len(items),
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

    posted = 0

    with OfferDB() as db:
        offers = db.get_unprocessed(
            deal_type=db_deal_type,
            property_type=property_type,
            max_count=max_count,
        )
        if not offers:
            logger.info("Необроблених оголошень у БД не знайдено")
            return 0

        logger.info("Знайдено %d необроблених оголошень для публікації", len(offers))

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
                    else:
                        db.mark_posted(offer.estate_id, rieltor_id)

                    if offer.article:
                        cleanup_photos(offer.article)

                    posted += 1

                except FormValidationError as e:
                    logger.error("Помилка валідації для об'єкта %d: %s", offer.estate_id, e)
                    logger.error(
                        "Дані об'єкта %d (article=%s):\n%s",
                        offer.estate_id,
                        offer.article,
                        json.dumps(offer_data, ensure_ascii=False, indent=2),
                    )
                    db.mark_failed(offer.estate_id, e.errors)

                except RieltorErrorPageException as e:
                    logger.error("Сторінка помилки Rieltor для об'єкта %d: %s", offer.estate_id, e)
                    logger.error(
                        "Дані об'єкта %d (article=%s):\n%s",
                        offer.estate_id,
                        offer.article,
                        json.dumps(offer_data, ensure_ascii=False, indent=2),
                    )
                    db.mark_failed(offer.estate_id, [{"error": str(e)}])

                except Exception:
                    logger.exception("Непередбачена помилка при публікації об'єкта %d", offer.estate_id)
                    logger.error(
                        "Дані об'єкта %d (article=%s):\n%s",
                        offer.estate_id,
                        offer.article,
                        json.dumps(offer_data, ensure_ascii=False, indent=2),
                    )
                    db.mark_failed(offer.estate_id, [{"error": "unexpected error"}])

    logger.info("Фаза 2 завершена: %d оголошень опубліковано", posted)
    return posted


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
    parser = argparse.ArgumentParser(
        description="Rieltor offer automation: CRM → parse → post",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode (also set via HEADLESS=true env var)")

    sub = parser.add_subparsers(dest="command")

    # collect
    p_collect = sub.add_parser("collect", help="Phase 1: collect from CRM into DB")
    p_collect.add_argument("--max-pages", type=int, help="Max CRM pagination pages")
    p_collect.add_argument("--max-count", type=int, help="Max offers to collect")
    p_collect.add_argument("--deal-type", help="Filter: sell or lease")
    p_collect.add_argument("--property-type", help="Filter: Квартира, Будинок, etc.")

    # post
    p_post = sub.add_parser("post", help="Phase 2: post from DB to Rieltor")
    p_post.add_argument("--publish", action="store_true", help="Publish instead of draft")
    p_post.add_argument("--max-count", type=int, help="Max offers to post")
    p_post.add_argument("--deal-type", help="Filter: sell or lease")
    p_post.add_argument("--property-type", help="Filter: Квартира, Будинок, etc.")

    # post-one
    p_one = sub.add_parser("post-one", help="Post a single offer from JSON")
    p_one.add_argument("source", help="JSON string or path to .json file")
    p_one.add_argument("--publish", action="store_true", help="Publish instead of draft")

    # cadastral
    p_cad = sub.add_parser("cadastral", help="Fill missing cadastral numbers in DB")
    p_cad.add_argument("--max-count", type=int, help="Max offers to process")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.debug:
        init_logging(level="DEBUG", filename="logs/rieltor.log")

    headless = os.getenv("HEADLESS", "").lower() == "true" or args.headless

    logger.info("=== Автоматизацію Rieltor запущено ===")

    try:
        if args.command == "collect":
            phase1_collect(
                max_pages=args.max_pages,
                max_count=args.max_count,
                deal_type=args.deal_type,
                property_type=args.property_type,
                headless=headless,
                debug=args.debug,
            )

        elif args.command == "post":
            phase2_post(
                publish=args.publish,
                deal_type=args.deal_type,
                property_type=args.property_type,
                max_count=args.max_count,
                headless=headless,
                debug=args.debug,
            )

        elif args.command == "post-one":
            post_single_offer(
                offer_source=args.source,
                publish=args.publish,
                headless=headless,
                debug=args.debug,
            )

        elif args.command == "cadastral":
            phase_cadastral(max_count=args.max_count)

        else:
            # No subcommand = full pipeline (collect + post draft)
            phase1_collect(headless=headless, debug=args.debug)
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
