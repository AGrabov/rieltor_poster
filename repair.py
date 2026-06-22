"""БД-сервіс: автоматичний ремонт помилкових (failed) об'єктів.

Перебирає failed-об'єкти, визначає категорію помилки валідації й намагається
виправити адресні помилки (Район / Кадастровий номер / Будинок) повторним
пошуком району та кадастрового номера. «Будинок має починатись з цифри» теж
сюди: це часто наслідок невибраного Району/вулиці (каскад адреси не відпрацював),
і правильний Район/кадастр це лагодить.

Якщо після збагачення Район змінився або кадастровий номер дозаповнено — об'єкт
повертається у чергу (status → new). Інакше лишається failed (щоб не зациклювати).
Помилки, не пов'язані з адресою, пропускаються — район/кадастр їх не виправлять.
"""

from __future__ import annotations

from crm_data_parser.cadastral_lookup import enrich_offer_data_with_cadastral
from offer_db import OfferDB
from setup_logger import setup_logger

logger = setup_logger(__name__)

# Категорії помилок, які можна спробувати виправити пошуком Району/кадастру.
_ADDRESS_CATS = frozenset({"raion", "cadastral", "house"})


def classify_errors(errors) -> set[str]:
    """Категорії помилок валідації об'єкта: {'raion','cadastral','house','other'}.

    Кожен елемент звіту валідації описує одне поле — категоризуємо за текстом
    (section + field + message), або за рядком винятку ('error').
    """
    cats: set[str] = set()
    for item in errors or []:
        if isinstance(item, dict):
            text = " ".join(str(item.get(k, "")) for k in ("section", "field", "message", "error"))
        else:
            text = str(item)
        low = text.lower()
        if "кадастр" in low:
            cats.add("cadastral")
        elif "будин" in low:
            cats.add("house")
        elif "район" in low:
            cats.add("raion")
        else:
            cats.add("other")
    return cats


def repair_failed_offers(max_count: int | None = None, db_path=None) -> dict:
    """Спробувати виправити failed-об'єкти пошуком Району/кадастру.

    Returns:
        Статистика: scanned / requeued / raion_fixed / cadastral_fixed /
        skipped (не адресні) / still_failed (адресні, але не виправились).
    """
    stats = {
        "scanned": 0,
        "requeued": 0,
        "raion_fixed": 0,
        "cadastral_fixed": 0,
        "skipped": 0,
        "still_failed": 0,
    }
    with (OfferDB(db_path) if db_path else OfferDB()) as db:
        failed = db.list_offers(statuses=["failed"], limit=max_count)
        logger.info("БД сервіс: знайдено %d помилкових об'єктів", len(failed))

        for rec in failed:
            stats["scanned"] += 1
            label = rec.article or str(rec.estate_id)
            cats = classify_errors(rec.errors)

            if not (cats & _ADDRESS_CATS):
                logger.info(
                    "Пропуск %s: помилки %s не адресні — район/кадастр не допоможуть",
                    label,
                    sorted(cats) or ["—"],
                )
                stats["skipped"] += 1
                continue

            offer_data = rec.offer_data
            addr = offer_data.get("address") or {}
            raion_before = (addr.get("Район") or "").strip()
            cad_before = (addr.get("Кадастровий номер") or "").strip()

            try:
                enrich_offer_data_with_cadastral(offer_data)
            except Exception:
                logger.exception("Помилка збагачення для %s — лишається failed", label)
                stats["still_failed"] += 1
                continue

            addr = offer_data.get("address") or {}
            raion_after = (addr.get("Район") or "").strip()
            cad_after = (addr.get("Кадастровий номер") or "").strip()

            raion_fixed = bool(raion_after) and raion_after != raion_before
            cadastral_fixed = bool(cad_after) and not cad_before

            if raion_fixed or cadastral_fixed:
                db.requeue(rec.estate_id, offer_data)
                stats["requeued"] += 1
                if raion_fixed:
                    stats["raion_fixed"] += 1
                if cadastral_fixed:
                    stats["cadastral_fixed"] += 1
                logger.info(
                    "Виправлено %s → new (помилки %s; район '%s'→'%s', кадастр '%s'→'%s')",
                    label,
                    sorted(cats),
                    raion_before or "—",
                    raion_after or "—",
                    cad_before or "—",
                    cad_after or "—",
                )
            else:
                stats["still_failed"] += 1
                logger.info("Не вдалось виправити %s (район/кадастр не змінились) — лишається failed", label)

    logger.info("БД сервіс завершено: %s", stats)
    return stats
