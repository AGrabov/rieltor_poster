"""Дозаливання фото у чернетки rieltor.ua, що збереглися без фото.

Корінь проблеми — перенос місця встановлення (Program Files → AppData\\Local)
зламав абсолютні шляхи фото в БД; файли лишилися на диску за новим base.
:func:`local_photos_for_offer` знаходить їх через перерахунок (re-root), а
:class:`DraftPhotoFixer` відкриває сторінку редагування чернетки й дозаливає фото.

Браузерні примітиви (``list_draft_rows`` / ``open_edit`` / ``photo_count`` /
``read_article`` / ``upload_photos``) ізольовані — у тестах перевизначаються, а під
реальну розмітку сайту їх перевіряють наживо на ПК ріелтора (`--no-headless`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from crm_data_parser.photo_downloader import resolve_local_photo, resolve_photo_paths
from setup_logger import setup_logger

logger = setup_logger(__name__)

_BASE_URL = "https://my.rieltor.ua"


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
    """Підсумок прогону дозаливання фото."""

    fixed: list = field(default_factory=list)  # артикули, куди дозалито фото
    already: list = field(default_factory=list)  # чернетки, що вже з фото
    no_source: list = field(default_factory=list)  # без фото й без локального джерела
    errors: list = field(default_factory=list)  # помилки обробки

    def counts(self) -> dict:
        return {
            "fixed": len(self.fixed),
            "already": len(self.already),
            "no_source": len(self.no_source),
            "errors": len(self.errors),
        }


class DraftPhotoFixer:
    """Знаходить чернетки без фото й дозаливає фото на сторінці редагування.

    Чистий цикл :meth:`fix_drafts` оркеструє браузерні примітиви; самі примітиви
    тонкі й перевіряються наживо.
    """

    def __init__(self, page, db, *, dry_run: bool = True) -> None:
        self.page = page
        self.db = db
        self.dry_run = dry_run

    # ── чистий цикл ──────────────────────────────────────────────────────
    def fix_drafts(self, rows=None, max_count: int | None = None) -> FixSummary:
        """Пройти чернетки; ті, що без фото — дозалити з локального джерела.

        Args:
            rows: список (key, edit_href, date); якщо None — беремо list_draft_rows().
            max_count: межа кількості ОПРАЦЬОВАНИХ (без фото) чернеток.
        """
        summary = FixSummary()
        if rows is None:
            rows = self.list_draft_rows()
        acted = 0
        for key, edit_href, _date in rows:
            if max_count is not None and acted >= max_count:
                break
            try:
                self.open_edit(edit_href)
                if self.photo_count() > 0:
                    summary.already.append(key)
                    continue
                acted += 1
                article = self.read_article()
                offer = self.db.get_by_article(article) if article else None
                paths = local_photos_for_offer(offer.offer_data) if offer else []
                if not paths:
                    summary.no_source.append(article or key)
                    logger.warning(
                        "Чернетка %s (article=%s): фото без локального джерела "
                        "(перезібрати з CRM або перевірити шлях)",
                        key,
                        article,
                    )
                    continue
                if self.dry_run:
                    logger.info(
                        "[dry-run] Чернетка %s (article=%s): дозалив би %d фото",
                        key,
                        article,
                        len(paths),
                    )
                    summary.fixed.append(article)
                    continue
                if self.upload_photos(paths):
                    summary.fixed.append(article)
                    logger.info(
                        "Чернетка %s (article=%s): дозалито %d фото",
                        key,
                        article,
                        len(paths),
                    )
                else:
                    summary.errors.append(article or key)
                    logger.warning("Чернетка %s (article=%s): не вдалось залити фото", key, article)
            except Exception:
                logger.warning("Чернетка %s: помилка обробки", key, exc_info=True)
                summary.errors.append(key)
        logger.info("Підсумок дозаливання фото: %s", summary.counts())
        return summary

    # ── браузерні примітиви (тонкі; перевіряються наживо) ─────────────────
    def list_draft_rows(self) -> list:
        """[(key, edit_href, date)] усіх чернеток. Повторно використовує DraftsPublisher."""
        from rieltor_handler.drafts_publisher import DraftsPublisher

        dp = DraftsPublisher(self.page)
        dp._goto(dp._drafts_url(dp.MAX_PAGE_LIMIT))
        rows_loc = self.page.locator(dp.ROW)
        out: list = []
        for i in range(rows_loc.count()):
            row = rows_loc.nth(i)
            try:
                href = row.locator("a[href*='/offers/edit/']").first.get_attribute("href")
            except Exception:
                href = None
            if not href:
                continue
            out.append((dp._row_key(row), href, dp._row_date(row)))
        logger.info("Чернеток на сайті: %d", len(out))
        return out

    def open_edit(self, edit_href: str) -> None:
        url = edit_href if str(edit_href).startswith("http") else _BASE_URL + str(edit_href)
        try:
            self.page.goto(url, wait_until="networkidle")
        except Exception:
            self.page.goto(url, wait_until="domcontentloaded")

    # Селектор поля «Артикул» на сторінці редагування — підтвердити наживо.
    _ARTICLE_INPUT = "input[name='article'], input[name='externalId']"

    def read_article(self) -> str | None:
        for sel in self._ARTICLE_INPUT.split(", "):
            try:
                loc = self.page.locator(sel).first
                if loc.count():
                    val = (loc.input_value() or "").strip()
                    if val:
                        return val
            except Exception:
                continue
        return None

    # Прев'ю фото на сторінці редагування — підтвердити наживо.
    _PHOTO_PREVIEW = "img[src*='/photo'], img[src*='cdn'], .photo-preview img, [class*='photo'] img"

    def photo_count(self) -> int:
        for sel in self._PHOTO_PREVIEW.split(", "):
            try:
                n = self.page.locator(sel).count()
                if n:
                    return n
            except Exception:
                continue
        return 0

    def upload_photos(self, paths: list[str]) -> bool:
        from rieltor_handler.new_offer_poster.photo_processing import prepare_photos

        prepared = prepare_photos(resolve_photo_paths(paths))
        if not prepared:
            return False
        try:
            file_input = self.page.locator("input[type='file']").first
            if not file_input.count():
                logger.error("Сторінка редагування: поле завантаження файлу не знайдено")
                return False
            before = self.photo_count()
            file_input.set_input_files(prepared)
            self.page.wait_for_timeout(2000)
            # Дочекатись приросту прев'ю (best-effort, без падіння по таймауту).
            for _ in range(60):
                if self.photo_count() > before:
                    break
                self.page.wait_for_timeout(1000)
            return self._save_edit()
        except Exception:
            logger.warning("Сторінка редагування: помилка завантаження фото", exc_info=True)
            return False

    # Кнопка збереження на сторінці редагування — підтвердити наживо.
    _SAVE_BUTTON = "button:has-text('Зберегти'), button:has-text('Зберегти зміни'), button[type='submit']"

    def _save_edit(self) -> bool:
        for sel in self._SAVE_BUTTON.split(", "):
            try:
                btn = self.page.locator(sel).first
                if btn.count():
                    btn.click()
                    self.page.wait_for_timeout(1500)
                    return True
            except Exception:
                continue
        logger.warning("Сторінка редагування: кнопку збереження не знайдено")
        return False
