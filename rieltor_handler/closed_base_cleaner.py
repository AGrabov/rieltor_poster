"""Масове видалення об'єктів із «Закритої бази» rieltor.ua."""

from __future__ import annotations

import re

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from setup_logger import setup_logger

logger = setup_logger(__name__)


class ClosedBaseCleaner:
    """Видаляє всі об'єкти із «Закритої бази» (mode=-30) по одному.

    Сайт не має масового видалення, тому цикл: вибрати radio рядка →
    «Видалити» → у діалозі обрати першу причину → підтвердити → повтор.

    Список — це SPA-таблиця MUI, яка довантажується після переходу, тому
    перед підрахунком/видаленням треба дочекатися рендеру рядків.
    Сторінка показує лише `limit` рядків (25), але повна кількість береться
    з лічильника вкладки «Закрита база».
    """

    CLOSED_BASE_URL = "https://my.rieltor.ua/offers/management?page=1&limit=25&mode=-30"

    TABLE = "table"
    ROW_RADIO = "td.MuiTableCell-paddingCheckbox .MuiRadio-root"
    TAB_LABEL = "Закрита база"
    DELETE_BUTTON = "button:has-text('Видалити')"
    DIALOG = "div[role='dialog']"
    DIALOG_REASON_RADIO = "div[role='dialog'] .MuiRadio-root"
    DIALOG_CONFIRM = "div[role='dialog'] button:has-text('Видалити')"

    RENDER_TIMEOUT_MS = 15_000

    def __init__(self, page: Page) -> None:
        self.page = page

    # ── навігація / рендер ───────────────────────────────────────────

    def _goto_base(self) -> None:
        """Перейти на сторінку «Закритої бази» й дочекатися появи таблиці."""
        self.page.goto(self.CLOSED_BASE_URL, wait_until="domcontentloaded")
        try:
            self.page.wait_for_selector(self.TABLE, timeout=self.RENDER_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            pass  # таблиці немає — база порожня

    def _wait_for_rows(self) -> int:
        """Дочекатися рендеру рядків і повернути їх кількість на сторінці (0 якщо порожньо)."""
        try:
            self.page.locator(self.ROW_RADIO).first.wait_for(
                state="visible", timeout=self.RENDER_TIMEOUT_MS
            )
        except PlaywrightTimeoutError:
            return 0
        return self.page.locator(self.ROW_RADIO).count()

    # ── публічні методи ──────────────────────────────────────────────

    def count(self) -> int:
        """Повна кількість об'єктів у «Закритій базі» (з лічильника вкладки)."""
        self._goto_base()
        # Вкладка: <div class="... active"><span>Закрита база</span><span>490</span></div>
        badge = self.page.locator(
            f"xpath=//span[normalize-space()='{self.TAB_LABEL}']/following-sibling::span[1]"
        )
        try:
            badge.first.wait_for(state="visible", timeout=self.RENDER_TIMEOUT_MS)
            digits = re.sub(r"\D", "", badge.first.inner_text() or "")
            if digits:
                return int(digits)
        except Exception:
            logger.debug("Не вдалося прочитати лічильник вкладки, рахуємо рядки сторінки")
        return self._wait_for_rows()

    def _delete_first(self) -> bool:
        """Видалити перший об'єкт у списку. Повертає False, якщо список порожній."""
        if self._wait_for_rows() == 0:
            return False

        self.page.locator(self.ROW_RADIO).first.click()
        self.page.locator(self.DELETE_BUTTON).first.click()

        dialog = self.page.locator(self.DIALOG)
        dialog.wait_for(state="visible")
        # Перша причина — «Просто не хочу рекламувати»
        self.page.locator(self.DIALOG_REASON_RADIO).first.click()
        self.page.locator(self.DIALOG_CONFIRM).first.click()
        dialog.wait_for(state="detached")
        self.page.wait_for_timeout(800)  # let the request settle
        # Re-navigate to refresh the list before the next deletion
        self._goto_base()
        return True

    def clean(self, max_count: int | None = None, dry_run: bool = False) -> int:
        """Видалити об'єкти із «Закритої бази».

        Args:
            max_count: Максимум видалень за прогін (None = всі).
            dry_run:   Якщо True — лише порахувати, нічого не видаляти.

        Returns:
            Кількість видалених (або повна кількість наявних при dry_run).
        """
        total = self.count()
        if dry_run:
            logger.info("[dry-run] У «Закритій базі» об'єктів: %d", total)
            return total

        logger.info("Початок очистки «Закритої бази»: %d об'єктів", total)
        deleted = 0
        while True:
            if max_count is not None and deleted >= max_count:
                logger.info("Досягнуто ліміту видалень: %d", max_count)
                break
            if not self._delete_first():
                logger.info("«Закрита база» порожня")
                break
            deleted += 1
            logger.info("Видалено %d об'єкт(ів)", deleted)

        logger.info("Очистку завершено: видалено %d об'єкт(ів)", deleted)
        return deleted
