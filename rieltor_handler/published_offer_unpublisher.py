"""Зняття опублікованих оголошень rieltor.ua у «Закриту базу» за rieltor_offer_id.

Вкладка «Опубліковані» (mode=10). Пошук рядка через фільтр «Id» у шапці таблиці,
далі: radio рядка → «В закриту базу» → діалог підтвердження → OK.

Чистий цикл `unpublish_offers` тестується підкласом; браузерний `unpublish`
перевіряється на живому сайті.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PWTimeout

from setup_logger import setup_logger

logger = setup_logger(__name__)


class PublishedOfferUnpublisher:
    """Переносить опубліковані оголошення у «Закриту базу» за їх ID."""

    PUBLISHED_URL = "https://my.rieltor.ua/offers/management?page=1&limit=25&mode=10"
    TABLE = "table"
    ID_FILTER = "thead input[placeholder='Id']"
    ROW_RADIO = "td.MuiTableCell-paddingCheckbox .MuiRadio-root"
    TO_CLOSED_BASE_BUTTON = "button:has-text('В закриту базу')"
    DIALOG = "div[role='dialog']"
    DIALOG_CONFIRM = "div[role='dialog'] button:has-text('OK')"
    RENDER_TIMEOUT_MS = 15_000

    def __init__(self, page: Page) -> None:
        self.page = page

    # ── цикл (чистий; браузерний unpublish перевизначається в тестах) ──

    def unpublish_offers(self, rieltor_ids: list[str], dry_run: bool = False) -> list[str]:
        """Зняти список оголошень за rieltor_offer_id.

        Returns:
            Список ID, які реально знято (при dry_run — усі, без дій на сайті).
        """
        if dry_run:
            for rid in rieltor_ids:
                logger.info("[dry-run] Зняв би оголошення %s", rid)
            return list(rieltor_ids)

        done: list[str] = []
        for rid in rieltor_ids:
            if self.unpublish(rid):
                done.append(rid)
                logger.info("Знято оголошення %s (%d/%d)", rid, len(done), len(rieltor_ids))
            else:
                logger.warning("Не вдалося зняти оголошення %s", rid)
        return done

    # ── браузерні методи (перевіряються на живому сайті) ──────────────

    def _goto_published(self) -> None:
        """Відкрити вкладку «Опубліковані» й дочекатися таблиці."""
        try:
            self.page.goto(self.PUBLISHED_URL, wait_until="networkidle")
        except PWTimeout:
            pass
        try:
            self.page.wait_for_selector(self.TABLE, timeout=self.RENDER_TIMEOUT_MS)
        except PWTimeout:
            logger.warning("Таблиця «Опубліковані» не з'явилася за %d мс", self.RENDER_TIMEOUT_MS)

    def collect_published_ids(self, limit: int = 500) -> list[str]:
        """Зчитати ID усіх оголошень із вкладки «Опубліковані» (mode=10).

        Повертає список rieltor_offer_id з посилань редагування рядків таблиці.
        """
        url = f"https://my.rieltor.ua/offers/management?page=1&limit={limit}&mode=10"
        try:
            self.page.goto(url, wait_until="networkidle")
        except PWTimeout:
            pass
        try:
            self.page.wait_for_selector(self.TABLE, timeout=self.RENDER_TIMEOUT_MS)
        except PWTimeout:
            logger.warning("Таблиця «Опубліковані» не з'явилася за %d мс", self.RENDER_TIMEOUT_MS)
            return []
        hrefs = self.page.locator("table tbody a[href*='/offers/edit/']").evaluate_all(
            "els => els.map(e => e.getAttribute('href'))"
        )
        ids: list[str] = []
        seen: set[str] = set()
        for href in hrefs:
            m = re.search(r"/offers/edit/(\d+)", href or "")
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                ids.append(m.group(1))
        logger.info("Опублікованих на сайті: %d", len(ids))
        return ids

    def _row_locator(self, rieltor_offer_id: str):
        """Локатор рядка, що містить посилання редагування з цим ID."""
        return self.page.locator(
            f"tr:has(a[href*='/offers/edit/{rieltor_offer_id}'])"
        ).first

    def _filter_by_id(self, rieltor_offer_id: str) -> bool:
        """Вписати ID у фільтр шапки й дочекатися появи рядка. False — не знайдено."""
        box = self.page.locator(self.ID_FILTER).first
        box.wait_for(state="visible", timeout=self.RENDER_TIMEOUT_MS)
        box.fill("")
        box.fill(str(rieltor_offer_id))
        try:
            self._row_locator(rieltor_offer_id).wait_for(
                state="visible", timeout=self.RENDER_TIMEOUT_MS
            )
            return True
        except PWTimeout:
            return False

    def unpublish(self, rieltor_offer_id: str) -> bool:
        """Перенести одне оголошення у «Закриту базу». False, якщо не знайдено/помилка."""
        self._goto_published()
        if not self._filter_by_id(rieltor_offer_id):
            logger.warning(
                "Оголошення %s не знайдено на вкладці «Опубліковані»", rieltor_offer_id
            )
            return False
        try:
            self._row_locator(rieltor_offer_id).locator(self.ROW_RADIO).first.click()
            btn = self.page.locator(self.TO_CLOSED_BASE_BUTTON).first
            btn.wait_for(state="visible", timeout=self.RENDER_TIMEOUT_MS)
            btn.click()
            dialog = self.page.locator(self.DIALOG)
            dialog.wait_for(state="visible", timeout=self.RENDER_TIMEOUT_MS)
            self.page.locator(self.DIALOG_CONFIRM).first.click()
            dialog.wait_for(state="detached", timeout=self.RENDER_TIMEOUT_MS)
            return True
        except Exception as e:
            logger.warning("Зняття %s не вдалося: %s", rieltor_offer_id, e)
            return False
