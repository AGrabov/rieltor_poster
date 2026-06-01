"""Масове видалення об'єктів rieltor.ua: «Закрита база» → «Видалені» → назавжди."""

from __future__ import annotations

import re

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from setup_logger import setup_logger

logger = setup_logger(__name__)


class ClosedBaseCleaner:
    """Масово очищає сміття на rieltor.ua у дві стадії.

    Видалення відбувається по одному (сайт не має масових дій):

    Стадія 1 — «Закрита база» (``mode=-30``): вибрати radio рядка →
    «Видалити» → у діалозі обрати першу причину («Просто не хочу
    рекламувати») → «OK». Об'єкт переходить у «Видалені».

    Стадія 2 — «Видалені» (``mode=-10``): вибрати radio рядка →
    «Видалити назавжди» → у діалозі «OK» (без причини). Об'єкт зникає
    остаточно.

    Список — SPA-таблиця MUI, що довантажується після переходу, тому перед
    підрахунком/дією треба дочекатися рендеру. Сторінка показує лише
    ``limit`` рядків (25), а повна кількість береться з лічильника вкладки.
    """

    CLOSED_BASE_URL = "https://my.rieltor.ua/offers/management?page=1&limit=25&mode=-30"
    DELETED_URL = "https://my.rieltor.ua/offers/management?page=1&limit=25&mode=-10"

    TABLE = "table"
    ROW_RADIO = "td.MuiTableCell-paddingCheckbox .MuiRadio-root"
    TAB_CLOSED = "Закрита база"
    TAB_DELETED = "Видалені"

    # Дії в тулбарі (унікальні в межах своєї вкладки)
    DELETE_BUTTON = "button:has-text('Видалити')"  # «Закрита база» (немає «назавжди»)
    DELETE_FOREVER_BUTTON = "button:has-text('Видалити назавжди')"  # «Видалені»

    DIALOG = "div[role='dialog']"
    DIALOG_REASON_RADIO = "div[role='dialog'] .MuiRadio-root"  # лише стадія 1
    DIALOG_CONFIRM = "div[role='dialog'] button:has-text('OK')"  # обидві стадії

    RENDER_TIMEOUT_MS = 15_000

    def __init__(self, page: Page) -> None:
        self.page = page

    # ── навігація / рендер ───────────────────────────────────────────

    def _goto(self, url: str) -> None:
        """Перейти за URL й дочекатися завантаження SPA-таблиці.

        Чекаємо networkidle (з fallback — на сторінках із поллінгом
        networkidle може не настати), потім появи ``<table>``.
        """
        try:
            self.page.goto(url, wait_until="networkidle")
        except PlaywrightTimeoutError:
            pass  # навігація відбулась, але мережа не «затихла» — продовжуємо
        try:
            self.page.wait_for_selector(self.TABLE, timeout=self.RENDER_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            pass  # таблиці немає — вкладка порожня

    def _wait_for_rows(self) -> int:
        """Дочекатися рендеру рядків; повернути їх кількість на сторінці (0 якщо порожньо)."""
        try:
            self.page.locator(self.ROW_RADIO).first.wait_for(
                state="visible", timeout=self.RENDER_TIMEOUT_MS
            )
        except PlaywrightTimeoutError:
            return 0
        return self.page.locator(self.ROW_RADIO).count()

    def _badge(self, label: str) -> int:
        """Прочитати лічильник активної вкладки за її назвою."""
        badge = self.page.locator(
            f"xpath=//span[normalize-space()='{label}']/following-sibling::span[1]"
        )
        try:
            badge.first.wait_for(state="visible", timeout=self.RENDER_TIMEOUT_MS)
            digits = re.sub(r"\D", "", badge.first.inner_text() or "")
            if digits:
                return int(digits)
        except Exception:
            logger.debug("Не вдалося прочитати лічильник вкладки '%s', рахуємо рядки", label)
        return self._wait_for_rows()

    def _delete_one(self, url: str, action_selector: str, pick_reason: bool) -> bool:
        """Видалити перший об'єкт на поточній вкладці. False, якщо список порожній.

        Args:
            url:             URL вкладки (для повторної навігації після видалення).
            action_selector: селектор кнопки дії («Видалити» / «Видалити назавжди»).
            pick_reason:     чи треба обрати причину в діалозі (стадія 1).
        """
        if self._wait_for_rows() == 0:
            return False

        self.page.locator(self.ROW_RADIO).first.click()
        # Дочекатися готовності тулбару перед кліком (інакше клік ловить таймаут)
        btn = self.page.locator(action_selector).first
        btn.wait_for(state="visible", timeout=self.RENDER_TIMEOUT_MS)
        btn.click()

        dialog = self.page.locator(self.DIALOG)
        dialog.wait_for(state="visible")
        if pick_reason:
            # Перша причина — «Просто не хочу рекламувати»
            self.page.locator(self.DIALOG_REASON_RADIO).first.click()
        self.page.locator(self.DIALOG_CONFIRM).first.click()
        dialog.wait_for(state="detached")
        self.page.wait_for_timeout(800)  # let the request settle
        self._goto(url)  # refresh the list before the next deletion
        return True

    # ── стадія 1: «Закрита база» ─────────────────────────────────────

    def count(self) -> int:
        """Повна кількість об'єктів у «Закритій базі» (з лічильника вкладки)."""
        self._goto(self.CLOSED_BASE_URL)
        return self._badge(self.TAB_CLOSED)

    def _delete_first(self) -> bool:
        """Видалити перший об'єкт із «Закритої бази» (переходить у «Видалені»)."""
        return self._delete_one(self.CLOSED_BASE_URL, self.DELETE_BUTTON, pick_reason=True)

    def clean(self, max_count: int | None = None, dry_run: bool = False) -> int:
        """Стадія 1: видалити об'єкти із «Закритої бази» (вони підуть у «Видалені»)."""
        return self._run_loop(self.count, self._delete_first, max_count, dry_run, "«Закрита база»")

    # ── стадія 2: «Видалені» (остаточно) ─────────────────────────────

    def count_deleted(self) -> int:
        """Повна кількість об'єктів у «Видалені» (з лічильника вкладки)."""
        self._goto(self.DELETED_URL)
        return self._badge(self.TAB_DELETED)

    def _purge_first(self) -> bool:
        """Остаточно видалити перший об'єкт із «Видалені»."""
        return self._delete_one(self.DELETED_URL, self.DELETE_FOREVER_BUTTON, pick_reason=False)

    def purge_deleted(self, max_count: int | None = None, dry_run: bool = False) -> int:
        """Стадія 2: остаточно видалити об'єкти із «Видалені»."""
        return self._run_loop(
            self.count_deleted, self._purge_first, max_count, dry_run, "«Видалені»"
        )

    # ── спільний цикл ────────────────────────────────────────────────

    def _run_loop(
        self,
        count_fn,
        delete_fn,
        max_count: int | None,
        dry_run: bool,
        label: str,
    ) -> int:
        """Загальний цикл видалення для обох стадій.

        Returns:
            Кількість видалених (або повна кількість наявних при dry_run).
        """
        total = count_fn()
        if dry_run:
            logger.info("[dry-run] %s: %d об'єктів", label, total)
            return total

        logger.info("Початок очистки %s: %d об'єктів", label, total)
        deleted = 0
        while True:
            if max_count is not None and deleted >= max_count:
                logger.info("%s: досягнуто ліміту %d", label, max_count)
                break
            if not delete_fn():
                logger.info("%s порожня", label)
                break
            deleted += 1
            logger.info("%s: видалено %d об'єкт(ів)", label, deleted)

        logger.info("Очистку %s завершено: видалено %d об'єкт(ів)", label, deleted)
        return deleted
