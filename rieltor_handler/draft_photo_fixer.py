"""Дозаливання фото та опису у чернетки rieltor.ua, що збереглися неповними.

Корінь — перенос місця встановлення (Program Files → AppData\\Local) зламав
абсолютні шляхи фото в БД; ще частина чернеток зберіглася без фото/опису через
збій початкового прогону. :func:`local_photos_for_offer` знаходить файли через
перерахунок (re-root), а :class:`DraftPhotoFixer` відкриває сторінку редагування
кожної чернетки й дозаливає те, чого бракує (фото з локальних файлів, опис із БД).

Селектори підтверджені на живому сайті (червень 2026):
- список чернеток: вкладка `mode=-2`, посилання `a[href*='/offers/edit/']`;
- зіставлення з БД: estate_id з поля `textarea[name='note']` ("…estate-NNNNN-…");
- фото на сторінці: `img[src*='/images/offers/']`; опис: `textarea[name='description']`;
- заливка фото/опису — через DictOfferFormFiller (секція "Опис, фотографії, відеотур",
  що коректно відмежовує головне фото від «планування»); збереження — «Зберегти».
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from crm_data_parser.photo_downloader import resolve_local_photo
from setup_logger import setup_logger

logger = setup_logger(__name__)

_BASE_URL = "https://my.rieltor.ua"
_ESTATE_ID_RE = re.compile(r"estate-(\d+)")


def local_photos_for_offer(offer_data: dict) -> list[str]:
    """Наявні локальні шляхи фото об'єкта, перераховані на поточний PICS_DIR."""
    photos = ((offer_data or {}).get("apartment") or {}).get("photos", [])
    out: list[str] = []
    for p in photos:
        if not isinstance(p, str):
            continue
        resolved = resolve_local_photo(p)
        if resolved:
            out.append(str(resolved))
    return out


@dataclass
class FixSummary:
    """Підсумок прогону дозаливання."""

    fixed: list = field(default_factory=list)  # (estate_id, ["photos:N", "desc"])
    already: list = field(default_factory=list)  # повні чернетки (фото + опис є)
    needs_crm: list = field(default_factory=list)  # фото немає на сайті й немає локально
    no_db: list = field(default_factory=list)  # чернетку не зіставлено з БД
    errors: list = field(default_factory=list)

    def counts(self) -> dict:
        return {
            "fixed": len(self.fixed),
            "already": len(self.already),
            "needs_crm": len(self.needs_crm),
            "no_db": len(self.no_db),
            "errors": len(self.errors),
        }


class DraftPhotoFixer:
    """Дозаливає фото (локально) та опис (з БД) у неповні чернетки rieltor.ua.

    Чистий цикл :meth:`fix_drafts` оркеструє браузерні примітиви; примітиви тонкі.
    """

    def __init__(self, page, db, *, dry_run: bool = True) -> None:
        self.page = page
        self.db = db
        self.dry_run = dry_run

    # ── чистий цикл ──────────────────────────────────────────────────────
    def fix_drafts(self, rows=None, max_count: int | None = None) -> FixSummary:
        """Пройти чернетки; дозалити фото/опис, яких бракує.

        Args:
            rows: список (rieltor_id, edit_href); якщо None — list_draft_rows().
            max_count: межа кількості чернеток, до яких застосовано зміни/спроби.
        """
        summary = FixSummary()
        if rows is None:
            rows = self.list_draft_rows()
        acted = 0
        for rid, href, *_ in rows:
            if max_count is not None and acted >= max_count:
                break
            try:
                self.open_edit(href)
                photos_on_site = self.site_photo_count()
                desc_on_site = (self.site_description() or "").strip()
                eid = self.read_estate_id()
                offer = self.db.get_offer(eid) if eid else None
                if not offer:
                    summary.no_db.append(rid)
                    logger.warning("Чернетка %s: не зіставлено з БД (estate_id=%s)", rid, eid)
                    continue
                od = offer.offer_data or {}
                ap = od.get("apartment") or {}
                db_desc = (ap.get("description") or "").strip()
                local_photos = local_photos_for_offer(od)

                need_photos = photos_on_site == 0
                need_desc = (not desc_on_site) and bool(db_desc)

                if not need_photos and not need_desc:
                    summary.already.append(eid)
                    continue

                fix_ap: dict = {}
                did: list[str] = []
                if need_photos:
                    if local_photos:
                        fix_ap["photos"] = local_photos
                        did.append(f"photos:{len(local_photos)}")
                    else:
                        summary.needs_crm.append(eid)
                        logger.warning(
                            "estate %s (%s): фото немає на сайті й немає локально — потрібен CRM",
                            eid,
                            rid,
                        )
                if need_desc:
                    fix_ap["description"] = db_desc
                    did.append("desc")

                if not fix_ap:
                    continue  # нічого з локальних даних зробити не можемо

                acted += 1
                if self.dry_run:
                    logger.info("[dry-run] estate %s (%s): дозалив би %s", eid, rid, ", ".join(did))
                    summary.fixed.append((eid, did))
                    continue

                if self.apply_fix({"apartment": fix_ap}, od.get("property_type"), od.get("offer_type")):
                    summary.fixed.append((eid, did))
                    logger.info("estate %s (%s): дозалито %s", eid, rid, ", ".join(did))
                else:
                    summary.errors.append(eid)
                    logger.warning("estate %s (%s): збереження не вдалося", eid, rid)
            except Exception:
                logger.warning("Чернетка %s: помилка обробки", rid, exc_info=True)
                summary.errors.append(rid)

        logger.info("Підсумок: %s", summary.counts())
        return summary

    # ── браузерні примітиви (тонкі; перевірені наживо) ────────────────────
    def list_draft_rows(self) -> list:
        """[(rieltor_id, edit_href, date)] усіх чернеток (вкладка mode=-2)."""
        from rieltor_handler.drafts_publisher import DraftsPublisher

        dp = DraftsPublisher(self.page)
        dp._goto(dp._drafts_url(dp.MAX_PAGE_LIMIT))
        rows_loc = self.page.locator(dp.ROW)
        out: list = []
        for i in range(rows_loc.count()):
            row = rows_loc.nth(i)
            link = row.locator("a[href*='/offers/edit/']").first
            href = link.get_attribute("href") if link.count() else None
            if not href:
                continue
            m = re.search(r"/offers/edit/(\d+)", href)
            out.append((m.group(1) if m else href, href, dp._row_date(row)))
        logger.info("Чернеток на сайті: %d", len(out))
        return out

    def open_edit(self, edit_href: str) -> None:
        url = edit_href if str(edit_href).startswith("http") else _BASE_URL + str(edit_href)
        try:
            self.page.goto(url, wait_until="domcontentloaded")
        except Exception:
            self.page.goto(url, wait_until="load")
        self.page.wait_for_timeout(5000)

    def site_photo_count(self) -> int:
        try:
            return self.page.locator("img[src*='/images/offers/']").count()
        except Exception:
            return 0

    def site_description(self) -> str:
        try:
            loc = self.page.locator("textarea[name='description']").first
            return (loc.input_value() or "") if loc.count() else ""
        except Exception:
            return ""

    def read_estate_id(self) -> int | None:
        try:
            loc = self.page.locator("textarea[name='note']").first
            note = (loc.input_value() or "") if loc.count() else ""
        except Exception:
            note = ""
        m = _ESTATE_ID_RE.search(note)
        return int(m.group(1)) if m else None

    def apply_fix(self, offer_data: dict, property_type: str | None, deal_type: str | None) -> bool:
        """Залити фото/опис у фото-секцію через перевірений DictOfferFormFiller і зберегти."""
        from rieltor_handler.new_offer_poster.dict_filler import DictOfferFormFiller

        filler = DictOfferFormFiller(
            self.page,
            property_type=property_type or "Квартира",
            deal_type=deal_type or "Продаж",
        )
        root = filler._new_offer_root()
        filler._fill_photos_from_dict(root, offer_data)
        return self._save()

    # Кнопка збереження чернетки (НЕ «Опублікувати»!) — перевірено наживо.
    def _save(self) -> bool:
        try:
            btn = self.page.locator("button:has-text('Зберегти')").first
            if not btn.count():
                logger.warning("Сторінка редагування: кнопку «Зберегти» не знайдено")
                return False
            btn.click()
            self.page.wait_for_timeout(2500)
            return True
        except Exception:
            logger.warning("Сторінка редагування: помилка збереження", exc_info=True)
            return False
