"""Збирач списку об'єктів CRM.

Навігує по посторінкових списках об'єктів у CRM, парсить картки .estate-item,
фільтрує за "Можна рекламувати" та отримує HTML-сторінки окремих об'єктів
для подальшого парсингу за допомогою HTMLOfferParser.

Використання:
    collector = EstateListCollector(page, debug=True)
    items = collector.collect_advertisable(max_pages=5)
    for item in items:
        if item.is_closed:
            continue
        html = collector.get_estate_html(item.estate_id)
        parser = HTMLOfferParser(html)
        offer_data = parser.parse()
        collector.enrich_with_commission(offer_data, item)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from bs4 import BeautifulSoup, Tag
from playwright.sync_api import Page
import os

from setup_logger import setup_logger

logger = setup_logger(__name__)

CRM_BASE_URL = "https://crm-capital.realtsoft.net"

# Default commission settings (value + unit)
# Unit options from schema: "%", "гривнях", "долларах"
COMMISSION_SALE = os.getenv("COMMISSION_SALE", "3")
COMMISSION_SALE_UNIT = os.getenv("COMMISSION_SALE_UNIT", "%")
COMMISSION_RENT = os.getenv("COMMISSION_RENT", "50")
COMMISSION_RENT_UNIT = os.getenv("COMMISSION_RENT_UNIT", "%")


@dataclass
class EstateListItem:
    """Одна картка об'єкта зі сторінки списку CRM."""

    estate_id: int
    title: str
    url: str
    price: Optional[str] = None
    category: Optional[str] = None
    property_type: Optional[str] = None
    deal_type: Optional[str] = None
    city: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    can_advertise: bool = False
    buyer_pays_commission: bool = False
    is_closed: bool = False


class EstateListCollector:
    """Збирає об'єкти зі сторінок списку CRM через Playwright.

    Працює з активною Playwright Page — передбачає, що користувач вже увійшов.
    """

    ESTATE_LIST_PATH = "/estate/index"
    # CRM filter: "Закритий/відкритий продаж" = "Відкритий продаж можна рекламувати" (value=2)
    ADVERTISABLE_FILTER = "property_69[]=2"
    PER_PAGE = 50

    def __init__(
        self,
        page: Page,
        commission_sale: str = COMMISSION_SALE,
        commission_sale_unit: str = COMMISSION_SALE_UNIT,
        commission_rent: str = COMMISSION_RENT,
        commission_rent_unit: str = COMMISSION_RENT_UNIT,
        debug: bool = False,
    ) -> None:
        self.page = page
        self.commission_sale = commission_sale
        self.commission_sale_unit = commission_sale_unit
        self.commission_rent = commission_rent
        self.commission_rent_unit = commission_rent_unit
        if debug:
            logger.setLevel("DEBUG")

    def collect_advertisable(
        self, max_pages: Optional[int] = None
    ) -> List[EstateListItem]:
        """Зібрати всі рекламовані об'єкти з відфільтрованого списку CRM.

        Відкриває список об'єктів з фільтром "Можна рекламувати",
        потім ітерує всі сторінки, збираючи картки об'єктів.
        Автоматично пропускає закриті об'єкти.

        Args:
            max_pages: Максимальна кількість сторінок для обробки (None = всі).

        Returns:
            Список активних EstateListItem (закриті виключені).
        """
        url = (
            f"{CRM_BASE_URL}{self.ESTATE_LIST_PATH}"
            f"?{self.ADVERTISABLE_FILTER}"
            f"&per-page={self.PER_PAGE}"
            f"&status[0]=active"
        )

        logger.info("Відкриття відфільтрованого списку об'єктів: %s", url)
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_selector(".estate-list", timeout=15_000)

        all_items: List[EstateListItem] = []
        page_num = 1

        while True:
            logger.info("Парсинг сторінки %d...", page_num)
            items = self.collect_page()
            active = [i for i in items if not i.is_closed and i.can_advertise]
            skipped_closed = sum(1 for i in items if i.is_closed)
            skipped_no_ads = sum(
                1 for i in items if not i.is_closed and not i.can_advertise
            )
            all_items.extend(active)
            logger.info(
                "Сторінка %d: %d об'єктів (%d активних, %d закритих, %d не рекламованих), всього: %d",
                page_num,
                len(items),
                len(active),
                skipped_closed,
                skipped_no_ads,
                len(all_items),
            )

            if max_pages and page_num >= max_pages:
                logger.info("Досягнуто max_pages=%d, зупинка", max_pages)
                break

            if not self._has_next_page():
                logger.info("Більше сторінок немає")
                break

            self._go_next_page()
            page_num += 1

        logger.info("Зібрано %d активних рекламованих об'єктів", len(all_items))
        return all_items

    def collect_page(self) -> List[EstateListItem]:
        """Розпарсити всі картки об'єктів на поточній сторінці.

        Returns:
            Список EstateListItem поточної сторінки.
        """
        html = self.page.content()
        soup = BeautifulSoup(html, "html.parser")

        items: List[EstateListItem] = []
        for elem in soup.select(".estate-item[data-key]"):
            try:
                item = self._parse_estate_item(elem)
                if item:
                    items.append(item)
            except Exception:
                logger.exception("Помилка парсингу картки об'єкта")

        return items

    def get_estate_html(self, estate_id: int) -> Optional[str]:
        """Перейти на сторінку окремого об'єкта та повернути його HTML.

        Перевіряє наявність сповіщення "Причина закриття" — повертає None, якщо об'єкт закрито.

        Args:
            estate_id: ID об'єкта в CRM.

        Returns:
            Повний HTML-вміст або None, якщо на сторінці є сповіщення про закриття.
        """
        url = f"{CRM_BASE_URL}/estate/{estate_id}"
        logger.info("Отримання сторінки об'єкта: %s", url)
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_selector(".page-content", timeout=15_000)

        html = self.page.content()

        if self._html_has_closure_alert(html):
            logger.warning(
                "Об'єкт %d закрито (знайдено сповіщення про закриття), пропускаємо", estate_id
            )
            return None

        return html

    def enrich_with_commission(self, offer_data: dict, item: EstateListItem) -> None:
        """Додати поля комісії до offer_data безумовно.

        Завжди встановлює "Комісія з покупця/орендатора": "Є" з розміром та
        одиницею комісії, щоб поле заповнювалось на сайті незалежно від того,
        чи платить продавець комісію.

        Args:
            offer_data: Словник, що будується для DictOfferFormFiller (змінюється на місці).
            item: EstateListItem з розпарсеною інформацією тегів.
        """
        offer_data["Комісія з покупця/орендатора"] = "Є"

        # Determine rate and unit based on deal type
        deal = (item.deal_type or offer_data.get("offer_type", "")).lower()
        if "оренда" in deal:
            rate = self.commission_rent
            unit = self.commission_rent_unit
        else:
            rate = self.commission_sale
            unit = self.commission_sale_unit

        offer_data["Розмір комісії"] = rate
        offer_data["Комісія у"] = unit

        logger.info(
            "Об'єкт %d: комісія → %s %s",
            item.estate_id,
            rate,
            unit,
        )

    def enrich_with_responsible_contacts(self, offer_data: dict) -> None:
        """Отримати контакти відповідального зі сторінки його профілю в CRM.

        Якщо offer_data містить словник 'responsible_person' з 'profile_url',
        переходить за цим URL, вилучає телефон/email та додає їх як 'contacts'
        до словника responsible_person. Також оновлює personal_notes відповідно.

        Args:
            offer_data: Словник, що будується для DictOfferFormFiller (змінюється на місці).
        """
        rp = offer_data.get("responsible_person")
        if not rp or not rp.get("profile_url"):
            return

        profile_url = rp["profile_url"]
        if not profile_url.startswith("http"):
            profile_url = f"{CRM_BASE_URL}{profile_url}"

        try:
            logger.info("Отримання профілю відповідального: %s", profile_url)
            self.page.goto(profile_url, wait_until="domcontentloaded")
            self.page.wait_for_selector(".page-content", timeout=15_000)
            html = self.page.content()
            contacts = self._parse_user_contacts(html)
            if contacts:
                rp["contacts"] = contacts
                logger.info("Контакти відповідального: %s", contacts)
                # Update personal_notes with contacts
                self._update_notes_with_contacts(offer_data)
        except Exception:
            logger.exception("Помилка отримання контактів відповідального")

    def _parse_user_contacts(self, html: str) -> str:
        """Розпарсити телефон та email зі сторінки профілю користувача CRM.

        Args:
            html: Повний HTML сторінки профілю користувача.

        Returns:
            Рядок контактів вигляду "тел: +380..., email: user@example.com" або порожній рядок.
        """
        soup = BeautifulSoup(html, "html.parser")
        parts: list[str] = []

        # Look for phone/email in the user profile detail table
        for table in soup.select("table.detail-view"):
            for row in table.select("tr"):
                th = row.select_one("th")
                td = row.select_one("td")
                if not th or not td:
                    continue
                label = th.get_text(strip=True).lower()
                value = td.get_text(strip=True)
                if not value:
                    continue
                if "телефон" in label or "phone" in label or "моб" in label:
                    parts.append(f"тел: {value}")
                elif "email" in label or "пошта" in label or "e-mail" in label:
                    parts.append(f"email: {value}")

        return ", ".join(parts)

    @staticmethod
    def _update_notes_with_contacts(offer_data: dict) -> None:
        """Оновити personal_notes, включивши контакти відповідального."""
        rp = offer_data.get("responsible_person", {})
        if not rp.get("contacts"):
            return

        notes = offer_data.get("personal_notes", "")
        # Replace the "Відповідальний: Name" line with "Відповідальний: Name (contacts)"
        old_line = f"Відповідальний: {rp['name']}"
        new_line = f"Відповідальний: {rp['name']} ({rp['contacts']})"
        if old_line in notes and rp["contacts"] not in notes:
            offer_data["personal_notes"] = notes.replace(old_line, new_line)

    # ── Internal ──

    def _parse_estate_item(self, elem: Tag) -> Optional[EstateListItem]:
        """Розпарсити один елемент .estate-item."""
        # Estate ID from data-key
        estate_id_str = elem.get("data-key", "")
        if not estate_id_str:
            return None
        estate_id = int(estate_id_str)

        # Title from .estate-title a
        title_elem = elem.select_one(".estate-title a")
        title = (
            title_elem.get_text(strip=True) if title_elem else f"Estate #{estate_id}"
        )

        # URL
        url = f"{CRM_BASE_URL}/estate/{estate_id}"

        # Price
        price_elem = elem.select_one(".price-per-object")
        price = price_elem.get_text(strip=True) if price_elem else None

        # Extra fields (Категорія, Тип, Тип угоди, Місто)
        extras = self._parse_extras(elem)

        # Tags
        tags: List[str] = []
        for badge in elem.select(".estate-tags .badge"):
            tag_text = badge.get_text(strip=True)
            if tag_text:
                tags.append(tag_text)

        tags_lower = [t.lower() for t in tags]

        # Can advertise: blocked only if an explicit non-advertising tag is present.
        # Items fetched via ADVERTISABLE_FILTER are presumed advertisable by default —
        # no positive "реклам" tag is required.
        not_advertisable = any(
            "не реклам" in t or ("закрит" in t and "продаж" in t) for t in tags_lower
        )
        can_advertise = not not_advertisable

        # Commission: "не платит" / "не платить" → seller does NOT pay → buyer pays
        buyer_pays = any(
            ("не плат" in t and "комісі" in t) or ("не плат" in t and "комисси" in t)
            for t in tags_lower
        )
        # Also check for explicit "комиссию не платит" / "комісію не платить"
        if not buyer_pays:
            buyer_pays = any("не плат" in t for t in tags_lower)

        # Closed estate: check for alert with "Причина закриття"
        is_closed = self._elem_has_closure_alert(elem)

        return EstateListItem(
            estate_id=estate_id,
            title=title,
            url=url,
            price=price,
            category=extras.get("Категорія"),
            property_type=extras.get("Тип"),
            deal_type=extras.get("Тип угоди"),
            city=extras.get("Місто"),
            tags=tags,
            can_advertise=can_advertise,
            buyer_pays_commission=buyer_pays,
            is_closed=is_closed,
        )

    def _parse_extras(self, elem: Tag) -> dict:
        """Розпарсити пари .estate-extra-item у словник."""
        result = {}
        for extra in elem.select(".estate-extra-item"):
            title_el = extra.select_one(".estate-extra-title")
            data_el = extra.select_one(".estate-extra-data")
            if title_el and data_el:
                key = title_el.get_text(strip=True)
                value = data_el.get_text(strip=True)
                if key and value:
                    result[key] = value
        return result

    def _elem_has_closure_alert(self, elem: Tag) -> bool:
        """Перевірити, чи елемент estate-item містить сповіщення про закриття."""
        for alert in elem.select(".alert"):
            text = alert.get_text(strip=True).lower()
            if "причина закриття" in text or "закрито" in text:
                return True
        return False

    def _html_has_closure_alert(self, html: str) -> bool:
        """Перевірити, чи повний HTML сторінки об'єкта містить сповіщення про закриття."""
        soup = BeautifulSoup(html, "html.parser")
        page_content = soup.select_one(".page-content")
        scope = page_content if page_content else soup

        for alert in scope.select(".alert"):
            text = alert.get_text(strip=True).lower()
            if "причина закриття" in text or "закрито" in text:
                return True
        return False

    def _has_next_page(self) -> bool:
        """Перевірити, чи є наступна сторінка в пагінації."""
        next_btn = self.page.locator("ul.pagination li.next:not(.disabled)")
        return next_btn.count() > 0

    def _go_next_page(self) -> None:
        """Натиснути кнопку 'наступна' в пагінації та дочекатися завантаження."""
        next_link = self.page.locator("ul.pagination li.next:not(.disabled) a")
        if next_link.count() == 0:
            return

        next_link.click()
        self.page.wait_for_load_state("domcontentloaded")
        self.page.wait_for_selector(".estate-list", timeout=15_000)
