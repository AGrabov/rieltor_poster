"""Масова публікація чернеток rieltor.ua (вкладка «Чернетки», mode=-2)."""

from __future__ import annotations

import datetime as dt
import random
import re
from dataclasses import dataclass

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PWTimeout

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

    # ── цикл публікації (чистий; браузерні методи перевизначаються) ──

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

        processed: set[str] = set()
        published = 0
        while True:
            if max_count is not None and published >= max_count:
                logger.info("Досягнуто ліміту %d", max_count)
                break
            rows = self._collect_rows(page_limit=total)
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

    _DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")

    @classmethod
    def _parse_row_date(cls, text: str | None) -> dt.date | None:
        """Витягти дату формату DD.MM.YYYY із тексту комірки (None, якщо нема)."""
        if not text:
            return None
        m = cls._DATE_RE.search(text)
        if not m:
            return None
        day, month, year = (int(g) for g in m.groups())
        try:
            return dt.date(year, month, day)
        except ValueError:
            return None

    # ── константи браузера ───────────────────────────────────────────

    # limit підставляється динамічно (= кількість чернеток), щоб усі рядки
    # рендерились на одній сторінці й фільтр дат не «виштовхував» потрібні далі.
    DRAFTS_URL_TMPL = "https://my.rieltor.ua/offers/management?page=1&limit={limit}&mode=-2"
    COUNT_LIMIT = 25  # для читання бейджа достатньо малого ліміту
    TABLE = "table"
    TAB_DRAFTS = "Чернетки"
    ROW = "table tbody tr"
    PUBLISH_BUTTON = "button:has-text('Опублікувати')"           # кнопка в рядку
    DIALOG = "div[role='dialog']"
    DIALOG_CONFIRM = "div[role='dialog'] button:has-text('Внести зміни')"
    RENDER_TIMEOUT_MS = 15_000

    def _drafts_url(self, limit: int) -> str:
        return self.DRAFTS_URL_TMPL.format(limit=max(1, limit))

    def _goto(self, url: str) -> None:
        try:
            self.page.goto(url, wait_until="networkidle")
        except PWTimeout:
            pass
        try:
            self.page.wait_for_selector(self.TABLE, timeout=self.RENDER_TIMEOUT_MS)
        except PWTimeout:
            pass

    def _badge(self, label: str) -> int:
        badge = self.page.locator(
            f"xpath=//span[normalize-space()='{label}']/following-sibling::span[1]"
        )
        try:
            badge.first.wait_for(state="visible", timeout=self.RENDER_TIMEOUT_MS)
            digits = re.sub(r"\D", "", badge.first.inner_text() or "")
            if digits:
                return int(digits)
        except Exception:
            logger.debug("Не вдалося прочитати лічильник вкладки '%s'", label)
        return len(self._collect_rows(page_limit=self.COUNT_LIMIT))

    def _sleep(self, seconds: float) -> None:
        """Затримка з невеликим джиттером (щоб не отримати бан)."""
        jitter = seconds * 0.4
        self.page.wait_for_timeout(int(max(0.0, seconds + random.uniform(-jitter, jitter)) * 1000))

    # ── браузерні методи (перевіряються на живому сайті, Task 6) ──────

    def count(self) -> int:
        """Повна кількість чернеток (з лічильника вкладки «Чернетки»)."""
        self._goto(self._drafts_url(self.COUNT_LIMIT))
        return self._badge(self.TAB_DRAFTS)

    def _row_key(self, row) -> str:
        """Стабільний ключ рядка: ID оголошення з href (fallback — текст рядка)."""
        link = row.locator("a[href*='/offer']").first
        try:
            href = link.get_attribute("href") or ""
            m = re.search(r"(\d{4,})", href)
            if m:
                return m.group(1)
        except Exception:
            pass
        return (row.inner_text() or "").strip()[:80]

    def _row_date(self, row) -> dt.date | None:
        """Дата створення чернетки з рядка (селектор уточнюється в Task 6)."""
        return self._parse_row_date(row.inner_text())

    def _collect_rows(self, page_limit: int | None = None) -> list[DraftRow]:
        """Зчитати рядки чернеток. Перезавантажує список перед зчитуванням.

        page_limit — кількість рядків для URL (= загальна кількість чернеток,
        щоб усі рядки були на одній сторінці). Дефолт `COUNT_LIMIT`.
        """
        limit = page_limit if page_limit is not None else self.COUNT_LIMIT
        self._goto(self._drafts_url(limit))
        rows = self.page.locator(self.ROW)
        try:
            rows.first.wait_for(state="visible", timeout=self.RENDER_TIMEOUT_MS)
        except Exception:
            return []
        out: list[DraftRow] = []
        for i in range(rows.count()):
            row = rows.nth(i)
            out.append(DraftRow(key=self._row_key(row), date=self._row_date(row)))
        return out

    def _publish_row(self, key: str) -> bool:
        """Опублікувати рядок із заданим ключем. False — якщо не вдалося."""
        rows = self.page.locator(self.ROW)
        for i in range(rows.count()):
            row = rows.nth(i)
            if self._row_key(row) != key:
                continue
            try:
                row.locator(self.PUBLISH_BUTTON).first.click()
                dialog = self.page.locator(self.DIALOG)
                dialog.wait_for(state="visible", timeout=self.RENDER_TIMEOUT_MS)
                self.page.locator(self.DIALOG_CONFIRM).first.click()
                dialog.wait_for(state="detached", timeout=self.RENDER_TIMEOUT_MS)
                return True
            except Exception as e:
                logger.warning("Публікація %s не вдалася: %s", key, e)
                return False
        return False
