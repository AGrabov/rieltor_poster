"""Тест перевірки актуальності об'єкта в CRM (без реального браузера)."""

from __future__ import annotations

from crm_data_parser.estate_list_collector import EstateListCollector


class _FakePage:
    """Мінімальна підробка Playwright Page для is_estate_closed."""

    def __init__(self, html: str) -> None:
        self._html = html
        self.last_url: str | None = None

    def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        self.last_url = url

    def wait_for_selector(self, selector: str, timeout: int | None = None) -> None:
        return None

    def content(self) -> str:
        return self._html


_CLOSED_HTML = (
    "<div class='page-content'>"
    "<div class='alert'>Причина закриття: продано</div>"
    "</div>"
)
_ACTIVE_HTML = "<div class='page-content'><div>Активний об'єкт</div></div>"


def test_is_estate_closed_true_for_closure_alert():
    collector = EstateListCollector(_FakePage(_CLOSED_HTML))
    assert collector.is_estate_closed(123) is True


def test_is_estate_closed_false_for_active():
    collector = EstateListCollector(_FakePage(_ACTIVE_HTML))
    assert collector.is_estate_closed(123) is False


def test_is_estate_closed_navigates_to_estate_url():
    page = _FakePage(_ACTIVE_HTML)
    collector = EstateListCollector(page)
    collector.is_estate_closed(456)
    assert page.last_url is not None
    assert page.last_url.endswith("/estate/456")
