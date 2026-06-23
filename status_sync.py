"""Звірка статусів у БД з реальним станом оголошень на rieltor.ua.

Чиста логіка зіставлення (тестується без браузера). Браузерне зчитування списків
«Опубліковані» (mode=10) та «Чернетки» виконує ``main.phase_sync_status``.

Ключ зіставлення — ``rieltor_offer_id`` (його зберігає ``OfferDB.mark_posted`` як
для опублікованих, так і для збережених чернеток). Об'єкти, що зазнали невдачі ДО
отримання id (``rieltor_offer_id`` порожній), за id не зіставити — вони потрапляють
до окремого кошика ``unmatchable``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StatusReport:
    """Результат звірки сайт ↔ БД."""

    published_on_site: list = field(default_factory=list)  # БД rid знайдено серед опублікованих
    draft_on_site: list = field(default_factory=list)  # БД rid знайдено серед чернеток
    posted_missing_from_site: list = field(default_factory=list)  # БД 'posted', а на сайті немає
    site_unknown_to_db: list = field(default_factory=list)  # id з сайту без відповідника в БД
    unmatchable: list = field(default_factory=list)  # БД new/failed/skipped без rieltor_offer_id


def reconcile_statuses(db_offers, published_ids, draft_ids) -> StatusReport:
    """Зіставити записи БД з наборами id опублікованих/чернеток на сайті.

    Args:
        db_offers: ітерабель словників з ключами estate_id, status, rieltor_offer_id, article.
        published_ids: id оголошень із вкладки «Опубліковані».
        draft_ids: id оголошень із вкладки «Чернетки».
    """
    pub = {str(x).strip() for x in published_ids if str(x).strip()}
    dft = {str(x).strip() for x in draft_ids if str(x).strip()}

    report = StatusReport()
    db_rids: set[str] = set()
    for offer in db_offers:
        rid = str(offer.get("rieltor_offer_id") or "").strip()
        status = offer.get("status")
        if not rid:
            if status in ("new", "failed", "skipped"):
                report.unmatchable.append(offer)
            continue
        db_rids.add(rid)
        if rid in pub:
            report.published_on_site.append(offer)
        elif rid in dft:
            report.draft_on_site.append(offer)
        elif status == "posted":
            report.posted_missing_from_site.append(offer)

    report.site_unknown_to_db = sorted((pub | dft) - db_rids)
    return report


def summary_counts(report: StatusReport) -> dict:
    """Кількості по кожній категорії звіту."""
    return {
        "published_on_site": len(report.published_on_site),
        "draft_on_site": len(report.draft_on_site),
        "posted_missing_from_site": len(report.posted_missing_from_site),
        "site_unknown_to_db": len(report.site_unknown_to_db),
        "unmatchable": len(report.unmatchable),
    }
