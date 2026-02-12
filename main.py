"""Main orchestration script for Rieltor offer automation.

Two-phase pipeline:
  Phase 1 (collect):  CRM → parse estates → download photos → save to SQLite
  Phase 2 (post):     SQLite → create drafts/publish on Rieltor

Usage:
  python main.py                                    # collect + post (draft)
  python main.py collect                            # only Phase 1
  python main.py collect --max-pages 1 --max-count 3
  python main.py post                               # only Phase 2 (draft)
  python main.py post --publish                     # Phase 2 with publish
  python main.py post --deal-type sell --max-count 5
  python main.py post-one offer.json                # post single offer from JSON
  python main.py post-one '{"Ціна": "100000", ...}'
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from setup_logger import init_logging, setup_logger

init_logging(
    level=os.getenv("LOG_LEVEL", "INFO"),
    filename="logs/rieltor.log",
)
logger = setup_logger(__name__)


# ── Deal-type normalization ──────────────────────────────────────────

_DEAL_TYPE_NORMALIZE = {
    "sell": "Продаж",
    "продаж": "Продаж",
    "lease": "Оренда",
    "rent": "Оренда",
    "оренда": "Оренда",
}


def _normalize_deal_type(value: str) -> Optional[str]:
    if not value:
        return None
    return _DEAL_TYPE_NORMALIZE.get(value.lower().strip())


# ── Phase 1: CRM collection ─────────────────────────────────────────

def phase1_collect(
    max_pages: Optional[int] = None,
    max_count: Optional[int] = None,
    deal_type: Optional[str] = None,
    property_type: Optional[str] = None,
    headless: bool = True,
    debug: bool = False,
) -> int:
    """Collect estates from CRM, parse, download photos, save to DB.

    Returns:
        Number of new offers saved.
    """
    from crm_data_parser import CrmSession, CrmCredentials, EstateListCollector, HTMLOfferParser, download_estate_photos
    from offer_db import OfferDB

    crm_email = os.environ.get("CRM_EMAIL", "").strip()
    crm_password = os.environ.get("CRM_PASSWORD", "").strip()
    if not crm_email or not crm_password:
        logger.error("CRM_EMAIL and CRM_PASSWORD must be set in .env")
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
        logger.info("Collected %d advertisable estates from CRM", len(items))

        # Apply filters
        if deal_type:
            normalized = _normalize_deal_type(deal_type)
            if normalized:
                items = [i for i in items if i.deal_type and i.deal_type.lower() == normalized.lower()]
                logger.info("Filtered by deal_type=%s: %d items", normalized, len(items))

        if property_type:
            items = [i for i in items if i.property_type and i.property_type.lower() == property_type.lower()]
            logger.info("Filtered by property_type=%s: %d items", property_type, len(items))

        if max_count:
            items = items[:max_count]
            logger.info("Limited to %d items", len(items))

        for idx, item in enumerate(items, 1):
            if db.estate_exists(item.estate_id):
                logger.info("[%d/%d] Estate %d already in DB, skipping", idx, len(items), item.estate_id)
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
                    logger.warning("[%d/%d] Estate %d closed, skipped", idx, len(items), item.estate_id)
                    continue

                parser = HTMLOfferParser(html, debug=debug)
                offer_data = parser.parse()

                collector.enrich_with_commission(offer_data, item)

                # Download photos while CRM session is active
                article = offer_data.get("article", str(item.estate_id))
                photo_urls = offer_data.get("apartment", {}).get("photos", [])
                if photo_urls:
                    local_paths = download_estate_photos(crm.page, photo_urls, article)
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
                    "[%d/%d] Saved estate %d (article=%s)",
                    idx, len(items), item.estate_id, article,
                )

            except Exception:
                logger.exception("[%d/%d] Failed to process estate %d", idx, len(items), item.estate_id)
                db.insert_offer(
                    estate_id=item.estate_id,
                    offer_data={},
                    property_type=item.property_type,
                    deal_type=item.deal_type,
                    title=item.title,
                    status="failed",
                )

    logger.info("Phase 1 complete: %d new offers saved to DB", saved)
    return saved


# ── Phase 2: Rieltor posting ────────────────────────────────────────

def phase2_post(
    publish: bool = False,
    deal_type: Optional[str] = None,
    property_type: Optional[str] = None,
    max_count: Optional[int] = None,
    headless: bool = True,
    debug: bool = False,
) -> int:
    """Post unprocessed offers from DB to Rieltor.

    Returns:
        Number of successfully posted offers.
    """
    from rieltor_handler import RieltorOfferPoster
    from rieltor_handler.rieltor_session import RieltorErrorPageException
    from rieltor_handler.new_offer_poster import DictOfferFormFiller, FormValidationError
    from offer_db import OfferDB
    from crm_data_parser import cleanup_photos

    phone = os.environ.get("PHONE", "").strip()
    password = os.environ.get("PASSWORD", "").strip()
    if not phone or not password:
        logger.error("PHONE and PASSWORD must be set in .env")
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
            logger.info("No unprocessed offers in DB")
            return 0

        logger.info("Found %d unprocessed offers to post", len(offers))

        with RieltorOfferPoster(
            phone=phone,
            password=password,
            headless=headless,
            debug=debug,
        ) as poster:
            poster.login()

            for idx, offer in enumerate(offers, 1):
                try:
                    offer_data = offer.offer_data
                    pt = offer_data.get("property_type", "Квартира")
                    dt = offer_data.get("offer_type", "Продаж")

                    logger.info(
                        "[%d/%d] Posting estate %d (article=%s, %s/%s)...",
                        idx, len(offers), offer.estate_id, offer.article, dt, pt,
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

                    poster.create_offer_draft(offer_data)

                    if publish:
                        report = poster.publish_and_get_report()
                    else:
                        report = poster.save_and_get_report()

                    rieltor_id = str(poster.last_saved_offer_id or "")

                    if report:
                        logger.warning(
                            "Estate %d posted with validation issues: %s",
                            offer.estate_id, report,
                        )
                        db.mark_failed(offer.estate_id, report)
                    else:
                        db.mark_posted(offer.estate_id, rieltor_id)

                    if offer.article:
                        cleanup_photos(offer.article)

                    posted += 1

                except FormValidationError as e:
                    logger.error("Validation error for estate %d: %s", offer.estate_id, e)
                    db.mark_failed(offer.estate_id, e.errors)

                except RieltorErrorPageException as e:
                    logger.error("Rieltor error page for estate %d: %s", offer.estate_id, e)
                    db.mark_failed(offer.estate_id, [{"error": str(e)}])

                except Exception:
                    logger.exception("Unexpected error posting estate %d", offer.estate_id)
                    db.mark_failed(offer.estate_id, [{"error": "unexpected error"}])

    logger.info("Phase 2 complete: %d offers posted", posted)
    return posted


# ── post-one: single offer from JSON ────────────────────────────────

def post_single_offer(
    offer_source: str,
    publish: bool = False,
    headless: bool = True,
    debug: bool = False,
) -> None:
    """Post a single offer from a JSON string or file path.

    Does NOT use the database — meant for testing and manual posting.
    """
    from rieltor_handler import RieltorOfferPoster

    # Parse offer_data from string or file
    source_path = Path(offer_source)
    if source_path.exists() and source_path.is_file():
        logger.info("Loading offer data from file: %s", source_path)
        offer_data = json.loads(source_path.read_text(encoding="utf-8"))
    else:
        offer_data = json.loads(offer_source)

    pt = offer_data.get("property_type", "Квартира")
    dt = offer_data.get("offer_type", "Продаж")

    phone = os.environ.get("PHONE", "").strip()
    password = os.environ.get("PASSWORD", "").strip()
    if not phone or not password:
        logger.error("PHONE and PASSWORD must be set in .env")
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
        poster.create_offer_draft(offer_data)

        if publish:
            report = poster.publish_and_get_report()
        else:
            report = poster.save_and_get_report()

        rieltor_id = poster.last_saved_offer_id
        if report:
            logger.warning("Posted with validation issues: %s", report)
        logger.info("Offer posted, rieltor_id=%s", rieltor_id)


# ── CLI ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rieltor offer automation: CRM → parse → post",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--no-headless", action="store_true", help="Show browser window")

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

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.debug:
        init_logging(level="DEBUG", filename="logs/rieltor.log")

    headless = not args.no_headless

    logger.info("=== Rieltor Automation Started ===")

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

        else:
            # No subcommand = full pipeline (collect + post draft)
            phase1_collect(headless=headless, debug=args.debug)
            phase2_post(publish=False, headless=headless, debug=args.debug)

        # Print summary
        from offer_db import OfferDB
        with OfferDB() as db:
            summary = db.summary()
        if summary:
            logger.info("=== DB Summary: %s ===", summary)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(1)
    except Exception:
        logger.exception("Fatal error")
        sys.exit(1)

    logger.info("=== Done ===")


if __name__ == "__main__":
    main()
