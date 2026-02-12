"""CRM Estate List Collector.

Navigates paginated estate lists in the CRM, parses .estate-item cards,
filters by "Можна рекламувати", and fetches individual estate HTML pages
for downstream parsing by HTMLOfferParser.

Usage:
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

CRM_BASE_URL = "https://crm-primes.realtsoft.net"

# Default commission settings (value + unit)
# Unit options from schema: "%", "гривнях", "долларах"
COMMISSION_SALE = os.getenv("COMMISSION_SALE", "3")
COMMISSION_SALE_UNIT = os.getenv("COMMISSION_SALE_UNIT", "%")
COMMISSION_RENT = os.getenv("COMMISSION_RENT", "50")
COMMISSION_RENT_UNIT = os.getenv("COMMISSION_RENT_UNIT", "%")

@dataclass
class EstateListItem:
    """Single estate card from the CRM list page."""
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
    """Collects estate items from CRM list pages via Playwright.

    Works with a live Playwright Page — assumes user is already logged in.
    """

    ESTATE_LIST_PATH = "/estate/index"
    # CRM filter: "Реклама" = "Можна рекламувати" (value=2)
    ADVERTISABLE_FILTER = "property_17[]=2"
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

    def collect_advertisable(self, max_pages: Optional[int] = None) -> List[EstateListItem]:
        """Collect all advertisable estates from the filtered CRM list.

        Opens the estate list with the "Можна рекламувати" filter applied,
        then iterates through all pages collecting estate items.
        Automatically skips closed estates.

        Args:
            max_pages: Maximum number of pages to process (None = all).

        Returns:
            List of active EstateListItem (closed ones are excluded).
        """
        url = (
            f"{CRM_BASE_URL}{self.ESTATE_LIST_PATH}"
            f"?{self.ADVERTISABLE_FILTER}"
            f"&per-page={self.PER_PAGE}"
            f"&status[0]=active"
        )

        logger.info("Opening filtered estate list: %s", url)
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_selector(".estate-list", timeout=15_000)

        all_items: List[EstateListItem] = []
        page_num = 1

        while True:
            logger.info("Parsing page %d...", page_num)
            items = self.collect_page()
            active = [i for i in items if not i.is_closed]
            skipped = len(items) - len(active)
            all_items.extend(active)
            logger.info(
                "Page %d: %d items (%d active, %d closed), total: %d",
                page_num, len(items), len(active), skipped, len(all_items),
            )

            if max_pages and page_num >= max_pages:
                logger.info("Reached max_pages=%d, stopping", max_pages)
                break

            if not self._has_next_page():
                logger.info("No more pages")
                break

            self._go_next_page()
            page_num += 1

        logger.info("Collected %d active advertisable estates", len(all_items))
        return all_items

    def collect_page(self) -> List[EstateListItem]:
        """Parse all estate items on the current page.

        Returns:
            List of EstateListItem from current page.
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
                logger.exception("Failed to parse estate item")

        return items

    def get_estate_html(self, estate_id: int) -> Optional[str]:
        """Navigate to a single estate page and return its HTML.

        Checks for "Причина закриття" alert — returns None if estate is closed.

        Args:
            estate_id: CRM estate ID.

        Returns:
            Full HTML content, or None if the estate page shows a closure alert.
        """
        url = f"{CRM_BASE_URL}/estate/{estate_id}"
        logger.info("Fetching estate page: %s", url)
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_selector(".page-content", timeout=15_000)

        html = self.page.content()

        if self._html_has_closure_alert(html):
            logger.warning("Estate %d is closed (closure alert found), skipping", estate_id)
            return None

        return html

    def enrich_with_commission(self, offer_data: dict, item: EstateListItem) -> None:
        """Add commission fields to offer_data based on list item tags.

        If seller/landlord does NOT pay commission (buyer_pays_commission=True),
        sets "Комісія з покупця/орендатора": "Є" and commission size/unit.
        Otherwise does nothing (field defaults to "Немає" on the form).

        Args:
            offer_data: Dict being built for DictOfferFormFiller (modified in place).
            item: EstateListItem with parsed tag info.
        """
        if not item.buyer_pays_commission:
            logger.debug("Estate %d: seller pays commission, skipping commission fields", item.estate_id)
            return

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
            "Estate %d: buyer pays commission → %s %s",
            item.estate_id, rate, unit,
        )

    # ── Internal ──

    def _parse_estate_item(self, elem: Tag) -> Optional[EstateListItem]:
        """Parse a single .estate-item element."""
        # Estate ID from data-key
        estate_id_str = elem.get("data-key", "")
        if not estate_id_str:
            return None
        estate_id = int(estate_id_str)

        # Title from .estate-title a
        title_elem = elem.select_one(".estate-title a")
        title = title_elem.get_text(strip=True) if title_elem else f"Estate #{estate_id}"

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

        # Can advertise
        can_advertise = any("реклам" in t for t in tags_lower)

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
        """Parse .estate-extra-item pairs into a dict."""
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
        """Check if an estate-item element has a closure/closing alert."""
        for alert in elem.select(".alert"):
            text = alert.get_text(strip=True).lower()
            if "причина закриття" in text or "закрито" in text:
                return True
        return False

    def _html_has_closure_alert(self, html: str) -> bool:
        """Check if a full estate page HTML contains a closure alert."""
        soup = BeautifulSoup(html, "html.parser")
        page_content = soup.select_one(".page-content")
        scope = page_content if page_content else soup

        for alert in scope.select(".alert"):
            text = alert.get_text(strip=True).lower()
            if "причина закриття" in text or "закрито" in text:
                return True
        return False

    def _has_next_page(self) -> bool:
        """Check if there's a next page in pagination."""
        next_btn = self.page.locator("ul.pagination li.next:not(.disabled)")
        return next_btn.count() > 0

    def _go_next_page(self) -> None:
        """Click the 'next' pagination button and wait for page load."""
        next_link = self.page.locator("ul.pagination li.next:not(.disabled) a")
        if next_link.count() == 0:
            return

        next_link.click()
        self.page.wait_for_load_state("domcontentloaded")
        self.page.wait_for_selector(".estate-list", timeout=15_000)
