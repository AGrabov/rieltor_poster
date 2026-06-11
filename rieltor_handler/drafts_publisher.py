"""Масова публікація чернеток rieltor.ua (вкладка «Чернетки», mode=-2)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from playwright.sync_api import Page

from setup_logger import setup_logger

logger = setup_logger(__name__)


@dataclass(frozen=True)
class DraftRow:
    """Один рядок таблиці чернеток.

    key:  стабільний ідентифікатор (ID оголошення з посилання) — щоб не
          обробляти той самий рядок двічі (фільтр дат лишає рядки в списку).
    date: дата створення чернетки (None, якщо не вдалося розпарсити).
    """

    key: str
    date: dt.date | None


class DraftsPublisher:
    """Публікує чернетки по черзі. Браузерні методи ізольовані для тестів."""

    def __init__(self, page: Page) -> None:
        self.page = page

    # ── чиста логіка (юніт-тести) ────────────────────────────────────

    @staticmethod
    def _in_date_range(
        row_date: dt.date | None,
        date_from: dt.date | None,
        date_to: dt.date | None,
    ) -> bool:
        if date_from is None and date_to is None:
            return True
        if row_date is None:
            return False
        if date_from is not None and row_date < date_from:
            return False
        if date_to is not None and row_date > date_to:
            return False
        return True

    def _select_next(
        self,
        rows: list[DraftRow],
        processed: set[str],
        date_from: dt.date | None,
        date_to: dt.date | None,
    ) -> DraftRow | None:
        for row in rows:
            if row.key in processed:
                continue
            if not self._in_date_range(row.date, date_from, date_to):
                continue
            return row
        return None

    # ── цикл публікації (чистий; браузерні методи переоприділяються) ──

    def publish_drafts(
        self,
        max_count: int | None = None,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
        delay_sec: float = 3.0,
        dry_run: bool = False,
    ) -> int:
        """Опублікувати чернетки в діапазоні дат, до max_count. Повертає кількість."""
        total = self.count()
        logger.info("Чернеток на сайті: %d (max_count=%s, dry_run=%s)", total, max_count, dry_run)
        # Вантажити всі чернетки на одній сторінці (без штучного ліміту):
        self._page_limit = total

        processed: set[str] = set()
        published = 0
        while True:
            if max_count is not None and published >= max_count:
                logger.info("Досягнуто ліміту %d", max_count)
                break
            rows = self._collect_rows()
            target = self._select_next(rows, processed, date_from, date_to)
            if target is None:
                logger.info("Немає більше чернеток у діапазоні")
                break
            processed.add(target.key)
            if dry_run:
                published += 1
                continue
            if self._publish_row(target.key):
                published += 1
                logger.info("Опубліковано %d (%s)", published, target.key)
                self._sleep(delay_sec)
            else:
                logger.warning("Пропущено (не вдалося опублікувати): %s", target.key)

        logger.info("Публікацію завершено: опубліковано %d", published)
        return published
