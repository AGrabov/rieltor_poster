"""Масове видалення об'єктів із «Закритої бази» rieltor.ua."""

from __future__ import annotations

from playwright.sync_api import Page

from setup_logger import setup_logger

logger = setup_logger(__name__)


class ClosedBaseCleaner:
    """Видаляє всі об'єкти із «Закритої бази» (mode=-30) по одному.

    Сайт не має масового видалення, тому цикл: вибрати radio рядка →
    «Видалити» → у діалозі обрати першу причину → підтвердити → повтор.
    """

    CLOSED_BASE_URL = "https://my.rieltor.ua/offers/management?page=1&limit=25&mode=-30"

    ROW_RADIO = "td.MuiTableCell-paddingCheckbox .MuiRadio-root"
    DELETE_BUTTON = "button:has-text('Видалити')"
    DIALOG = "div[role='dialog']"
    DIALOG_REASON_RADIO = "div[role='dialog'] .MuiRadio-root"
    DIALOG_CONFIRM = "div[role='dialog'] button:has-text('Видалити')"

    def __init__(self, page: Page) -> None:
        self.page = page

    def count(self) -> int:
        """Перейти на сторінку «Закритої бази» й порахувати рядки."""
        self.page.goto(self.CLOSED_BASE_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)  # MUI table renders after data fetch
        return self.page.locator(self.ROW_RADIO).count()

    def _delete_first(self) -> bool:
        """Видалити перший об'єкт у списку. Повертає False, якщо список порожній."""
        radios = self.page.locator(self.ROW_RADIO)
        if radios.count() == 0:
            return False

        radios.first.click()
        self.page.locator(self.DELETE_BUTTON).first.click()

        dialog = self.page.locator(self.DIALOG)
        dialog.wait_for(state="visible")
        # Перша причина — «Просто не хочу рекламувати»
        self.page.locator(self.DIALOG_REASON_RADIO).first.click()
        self.page.locator(self.DIALOG_CONFIRM).first.click()
        dialog.wait_for(state="detached")
        self.page.wait_for_timeout(1000)  # let the list reload
        # Re-navigate to refresh the list state before the next deletion
        self.page.goto(self.CLOSED_BASE_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)
        return True

    def clean(self, max_count: int | None = None, dry_run: bool = False) -> int:
        """Видалити об'єкти із «Закритої бази».

        Args:
            max_count: Максимум видалень за прогін (None = всі).
            dry_run:   Якщо True — лише порахувати, нічого не видаляти.

        Returns:
            Кількість видалених (або кількість наявних при dry_run).
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
