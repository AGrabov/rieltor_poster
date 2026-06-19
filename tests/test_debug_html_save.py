"""Tests for the CRM HTML debug saver in EstateListCollector.

Gated by LOG_LEVEL=debug: lets us capture a real CRM estate card (e.g. to
inspect the Контакти/owner markup) whenever debug logging is on, without a
separate env key. Normal (INFO) runs stay disabled.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from crm_data_parser.estate_list_collector import EstateListCollector


def _collector(tmp_path):
    c = EstateListCollector.__new__(EstateListCollector)
    c._DEBUG_HTML_DIR = tmp_path / "debug_html"
    return c


def test_debug_html_dir_is_absolute_project_logs():
    # Має бути АБСОЛЮТНИЙ шлях у теці logs проєкту, а не відносний підкаталог:
    # на машині користувача (запуск з іншого CWD на диску C) відносний шлях
    # вів у неіснуюче місце, тож дамп не зберігався.
    d = EstateListCollector._DEBUG_HTML_DIR
    assert d.is_absolute()
    assert d.name == "logs"


def test_save_disabled_when_log_level_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    c = _collector(tmp_path)
    assert c._save_debug_html(123, "<html></html>") is None
    assert not (tmp_path / "debug_html").exists()


def test_save_enabled_when_log_level_debug(tmp_path, monkeypatch):
    # Case-insensitive: .env may hold "debug", dashboard injects "DEBUG".
    monkeypatch.setenv("LOG_LEVEL", "debug")
    c = _collector(tmp_path)
    path = c._save_debug_html(123, "<html>hi</html>")
    assert path is not None
    # Один файл-зразок із фіксованою назвою (не по одному на об'єкт).
    assert Path(path).name == "crm_estate.html"
    assert Path(path).read_text(encoding="utf-8") == "<html>hi</html>"


def test_save_skips_when_file_already_exists(tmp_path, monkeypatch):
    # Якщо зразок уже є — пропускаємо (не перезаписуємо першу збережену картку).
    monkeypatch.setenv("LOG_LEVEL", "debug")
    c = _collector(tmp_path)
    first = c._save_debug_html(1, "<html>first</html>")
    assert first is not None
    second = c._save_debug_html(2, "<html>second</html>")
    assert second is None
    assert Path(first).read_text(encoding="utf-8") == "<html>first</html>"


def test_save_disabled_when_log_level_info(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
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
