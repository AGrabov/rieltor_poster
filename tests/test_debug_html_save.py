"""Tests for the env-gated CRM HTML debug saver in EstateListCollector.

Lets us capture a real CRM estate card (e.g. to inspect the Контакти/owner
markup) without affecting normal runs, which stay disabled by default.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from crm_data_parser.estate_list_collector import EstateListCollector


def _collector(tmp_path):
    c = EstateListCollector.__new__(EstateListCollector)
    c._DEBUG_HTML_DIR = tmp_path / "debug_html"
    return c


def test_save_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("SAVE_CRM_HTML", raising=False)
    c = _collector(tmp_path)
    assert c._save_debug_html(123, "<html></html>") is None
    assert not (tmp_path / "debug_html").exists()


def test_save_enabled_writes_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SAVE_CRM_HTML", "true")
    c = _collector(tmp_path)
    path = c._save_debug_html(123, "<html>hi</html>")
    assert path is not None
    assert Path(path).name == "estate_123.html"
    assert Path(path).read_text(encoding="utf-8") == "<html>hi</html>"


def test_save_falsey_value_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("SAVE_CRM_HTML", "0")
    c = _collector(tmp_path)
    assert c._save_debug_html(123, "<html></html>") is None


def test_check_actuality_saves_debug_html(tmp_path, monkeypatch):
    """During the post phase the CRM page is fetched via check_actuality, so the
    debug saver must be hooked there too (regression: it was only in get_estate_html)."""
    monkeypatch.setattr(
        "crm_data_parser.estate_list_collector.parse_estate_price_from_html",
        lambda html: (None, None),
    )
    c = _collector(tmp_path)
    c.page = MagicMock()
    c.page.content.return_value = "<html>card</html>"

    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(c, "_save_debug_html", lambda eid, html: calls.append((eid, html)))
    monkeypatch.setattr(c, "_html_has_closure_alert", lambda html: False)

    c.check_actuality(555)

    assert calls and calls[0][0] == 555
    assert calls[0][1] == "<html>card</html>"
