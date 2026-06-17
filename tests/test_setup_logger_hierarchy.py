"""Regression tests for setup_logger name resolution.

The bug: a package whose name *starts with the string* ``APP_NAME`` but is a
separate hierarchy — e.g. ``rieltor_handler`` vs APP_NAME ``rieltor`` — was left
un-prefixed, so its records never propagated to the ``rieltor`` base logger and
were silently dropped from the file/console handlers. The fix checks the dot
boundary (``rieltor.``), not the bare prefix.
"""

from __future__ import annotations

import logging

import setup_logger as sl_mod
from setup_logger import setup_logger


def test_handler_subpackage_is_prefixed_under_base(monkeypatch):
    monkeypatch.setattr(sl_mod, "APP_NAME", "rieltor")
    lg = setup_logger("rieltor_handler.new_offer_poster.dict_filler")
    assert lg.name == "rieltor.rieltor_handler.new_offer_poster.dict_filler"


def test_handler_subpackage_propagates_to_base_handler(monkeypatch):
    monkeypatch.setattr(sl_mod, "APP_NAME", "rieltor")
    base = logging.getLogger("rieltor")
    base.setLevel(logging.DEBUG)

    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record)

    handler = _Capture()
    base.addHandler(handler)
    try:
        lg = setup_logger("rieltor_handler.new_offer_poster.dict_filler")
        lg.setLevel(logging.NOTSET)  # inherit effective level from base
        lg.warning("form-filler message")
    finally:
        base.removeHandler(handler)

    assert any(r.getMessage() == "form-filler message" for r in captured)


def test_plain_module_is_prefixed(monkeypatch):
    monkeypatch.setattr(sl_mod, "APP_NAME", "rieltor")
    assert setup_logger("__main__").name == "rieltor.__main__"
    assert setup_logger("crm_data_parser.cadastral_lookup").name == "rieltor.crm_data_parser.cadastral_lookup"


def test_already_prefixed_not_doubled(monkeypatch):
    monkeypatch.setattr(sl_mod, "APP_NAME", "rieltor")
    assert setup_logger("rieltor.__main__").name == "rieltor.__main__"
    assert setup_logger("rieltor").name == "rieltor"


def test_empty_app_name_leaves_name_untouched(monkeypatch):
    monkeypatch.setattr(sl_mod, "APP_NAME", "")
    assert setup_logger("foo.bar").name == "foo.bar"
