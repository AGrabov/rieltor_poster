"""Tests for CRM price-actuality detection (parse + compare + check_actuality).

The CRM actuality check now also reads the current price so a changed price can be
logged ("було X → стало Y"). These cover the pure pieces without a real browser.
"""

from __future__ import annotations

from crm_data_parser.estate_list_collector import (
    EstateActuality,
    EstateListCollector,
    parse_estate_price_from_html,
)
from crm_data_parser.html_parser import parse_price
from main import _price_changed


class _FakePage:
    """Мінімальна підробка Playwright Page."""

    def __init__(self, html: str) -> None:
        self._html = html
        self.last_url: str | None = None

    def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        self.last_url = url

    def wait_for_selector(self, selector: str, timeout: int | None = None) -> None:
        return None

    def content(self) -> str:
        return self._html


# ── parse_price ──
def test_parse_price_dollars():
    assert parse_price("182 000 $") == (182000, "доларів")


def test_parse_price_hryvnia():
    assert parse_price("50 000 грн") == (50000, "гривень")


def test_parse_price_empty():
    assert parse_price("—") == (None, None)


# ── parse_estate_price_from_html ──
def test_extract_price_from_html():
    html = "<div class='page-content'><span class='price-per-object'>182 000 $</span></div>"
    assert parse_estate_price_from_html(html) == (182000, "доларів")


def test_extract_price_missing():
    assert parse_estate_price_from_html("<div class='page-content'></div>") == (None, None)


# ── check_actuality (closure + price, single page load) ──
def test_check_actuality_open_with_price():
    page = _FakePage("<div class='page-content'><span class='price-per-object'>100 000 $</span></div>")
    actuality = EstateListCollector(page).check_actuality(1)
    assert isinstance(actuality, EstateActuality)
    assert actuality.closed is False
    assert actuality.price == 100000
    assert actuality.currency == "доларів"


def test_check_actuality_closed():
    page = _FakePage("<div class='page-content'><div class='alert'>Причина закриття: продано</div></div>")
    actuality = EstateListCollector(page).check_actuality(1)
    assert actuality.closed is True


# ── _price_changed ──
def test_price_changed_amount():
    assert _price_changed(100000, "доларів", 120000, "доларів") is True


def test_price_unchanged():
    assert _price_changed(100000, "доларів", 100000, "доларів") is False


def test_price_changed_currency():
    assert _price_changed(100000, "доларів", 100000, "гривень") is True


def test_price_changed_missing_current_is_not_change():
    assert _price_changed(100000, "доларів", None, None) is False


def test_price_stored_as_string_equal():
    assert _price_changed("100000", "доларів", 100000, "доларів") is False
