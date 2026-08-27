"""Перенесення оголошень rieltor.ua у «Мої угоди» за rieltor_offer_id.

Використовується, коли перевірка актуальності виявила, що об'єкт закрито в CRM:
такий об'єкт — це, як правило, угода, тож він має потрапити у вкладку «Угоди»,
а не в «Закриту базу» чи назад у «Чорновики».

Розмітка перевірена наживо (2026-08-27): вкладки «Опубліковані» (`mode=10`) і
«Чорновики» (`mode=-2`) мають однакову таблицю — фільтр «Id» у шапці, radio в
рядках і кнопку «В угоди» в тулбарі. Діалог теж спільний: показує ціну
оголошення текстом і просить «Ціну угоди» в `input[name=userPrice]` (default 0).
Чекбокс «Верифікувати угоду…» (лише на вкладці чернеток) не чіпаємо.

Чистий цикл `move_offers_to_deals` і парсер ціни тестуються юніт-тестами;
браузерний `move_to_deals` перевіряється на живому сайті.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PWTimeout

from setup_logger import setup_logger

logger = setup_logger(__name__)


class DealsMover:
    """Записує оголошення у «Мої угоди» за їх ID (шукає і серед опублікованих, і серед чернеток)."""

    PUBLISHED_URL = "https://my.rieltor.ua/offers/management?page=1&limit=25&mode=10"
    DRAFTS_URL = "https://my.rieltor.ua/offers/management?page=1&limit=25&mode=-2"
    TABLE = "table"
    ID_FILTER = "thead input[placeholder='Id']"
    ROW_RADIO = "td.MuiTableCell-paddingCheckbox .MuiRadio-root"
    TO_DEALS_BUTTON = "button:has-text('В угоди')"
    DIALOG = "div[role='dialog']"
    DIALOG_PRICE = "div[role='dialog'] input[name='userPrice']"
    DIALOG_CONFIRM = "div[role='dialog'] button:has-text('OK')"
    RENDER_TIMEOUT_MS = 15_000

    # «Записати оголошення за ціною 1 600 000 грн в Мої угоди» → 1600000
    _PRICE_RE = re.compile(r"за ціною\s*([\d\s]+)")

    def __init__(self, page: Page) -> None:
        self.page = page

    # ── чиста логіка (юніт-тести) ────────────────────────────────────

    @classmethod
    def _parse_dialog_price(cls, dialog_text: str | None) -> int | None:
        """Ціна оголошення з тексту діалогу. None, якщо не розпізнано або < 1.

        Беремо саме з діалогу, а не з БД/CRM: так число гарантовано в тій валюті,
        яку показує сайт у полі «Ціна угоди».
        """
        if not dialog_text:
            return None
        m = cls._PRICE_RE.search(dialog_text)
        if not m:
            return None
        digits = re.sub(r"\D", "", m.group(1))
        if not digits:
            return None
        price = int(digits)
        return price if price >= 1 else None  # сайт вимагає min="1"

    def move_offers_to_deals(self, rieltor_ids: list[str], dry_run: bool = False) -> list[str]:
        """Записати список оголошень у «Мої угоди».

        Returns:
            Список ID, які реально перенесено (при dry_run — усі, без дій на сайті).
        """
        if dry_run:
            for rid in rieltor_ids:
                logger.info("[dry-run] Записав би оголошення %s у «Мої угоди»", rid)
            return list(rieltor_ids)

        done: list[str] = []
        for rid in rieltor_ids:
            if self.move_to_deals(rid):
                done.append(rid)
                logger.info("Оголошення %s записано у «Мої угоди» (%d/%d)", rid, len(done), len(rieltor_ids))
            else:
                logger.warning("Не вдалося записати оголошення %s у «Мої угоди»", rid)
        return done

    # ── браузерні методи (перевіряються на живому сайті) ─────────────

    def _open_tab(self, url: str) -> None:
        """Перейти на вкладку керування оголошеннями й дочекатися таблиці."""
        try:
            self.page.goto(url, wait_until="networkidle")
        except PWTimeout:
            pass  # навігація відбулась, але мережа не «затихла» — продовжуємо
        try:
            self.page.wait_for_selector(self.TABLE, timeout=self.RENDER_TIMEOUT_MS)
        except PWTimeout:
            logger.debug("Таблиця не з'явилася за %d мс (%s)", self.RENDER_TIMEOUT_MS, url)

    def _row_locator(self, rieltor_offer_id: str):
        """Локатор рядка, що містить посилання редагування з цим ID."""
        return self.page.locator(f"tr:has(a[href*='/offers/edit/{rieltor_offer_id}'])").first

    def _find_row(self, url: str, rieltor_offer_id: str) -> bool:
        """Відкрити вкладку й відфільтрувати її по ID. False — рядка там немає."""
        self._open_tab(url)
        box = self.page.locator(self.ID_FILTER).first
        try:
            box.wait_for(state="visible", timeout=self.RENDER_TIMEOUT_MS)
        except PWTimeout:
            logger.debug("Фільтр «Id» не знайдено (%s)", url)
            return False
        box.fill("")
        box.fill(str(rieltor_offer_id))
        try:
            self._row_locator(rieltor_offer_id).wait_for(state="visible", timeout=self.RENDER_TIMEOUT_MS)
            return True
        except PWTimeout:
            return False

    def _record_deal(self, rieltor_offer_id: str) -> bool:
        """Провести вибраний рядок через діалог «Записати оголошення в Мої угоди»."""
        self._row_locator(rieltor_offer_id).locator(self.ROW_RADIO).first.click()
        btn = self.page.locator(self.TO_DEALS_BUTTON).first
        btn.wait_for(state="visible", timeout=self.RENDER_TIMEOUT_MS)
        btn.click()

        dialog = self.page.locator(self.DIALOG).first
        dialog.wait_for(state="visible", timeout=self.RENDER_TIMEOUT_MS)

        price = self._parse_dialog_price(dialog.inner_text())
        if price is None:
            logger.warning(
                "Оголошення %s: ціну з діалогу не розпізнано — лишаємо поле як є",
                rieltor_offer_id,
            )
        else:
            self.page.locator(self.DIALOG_PRICE).first.fill(str(price))
            logger.info("Оголошення %s: ціна угоди %s", rieltor_offer_id, price)

        self.page.locator(self.DIALOG_CONFIRM).first.click()
        dialog.wait_for(state="detached", timeout=self.RENDER_TIMEOUT_MS)
        return True

    def move_to_deals(self, rieltor_offer_id: str) -> bool:
        """Записати одне оголошення у «Мої угоди». False, якщо не знайдено/помилка.

        Шукає рядок спершу серед опублікованих, потім серед чернеток.
        """
        for url, tab in ((self.PUBLISHED_URL, "Опубліковані"), (self.DRAFTS_URL, "Чорновики")):
            if not self._find_row(url, rieltor_offer_id):
                continue
            logger.debug("Оголошення %s знайдено на вкладці «%s»", rieltor_offer_id, tab)
            try:
                return self._record_deal(rieltor_offer_id)
            except Exception as e:
                logger.warning("Запис %s у «Мої угоди» не вдався: %s", rieltor_offer_id, e)
                return False
        logger.warning("Оголошення %s не знайдено ні в «Опублікованих», ні в «Чорновиках»", rieltor_offer_id)
        return False
