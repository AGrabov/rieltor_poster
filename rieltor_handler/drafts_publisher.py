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
