"""Tests for floor/storey extraction in DescriptionAnalyzer._extract_numeric_fields.

Covers the common commercial format "N/M поверх" (digit/digit BEFORE the word),
which previously slipped through and let bad CRM floor values stand uncorrected.
"""

from __future__ import annotations

from crm_data_parser.description_analyzer import DescriptionAnalyzer


def _analyzer():
    # Floor extraction adds "Поверх"/"Поверховість" directly, independent of schema.
    return DescriptionAnalyzer([])


def test_slash_floor_before_word():
    # "3/7 поверх" → floor 3 of 7 (real example from CRM estate A31037)
    res = _analyzer().analyze("офісне приміщення, 3/7 поверх, доступ 24/7", {})
    assert res.get("Поверх") == "3"
    assert res.get("Поверховість") == "7"


def test_slash_floor_does_not_confuse_total():
    res = _analyzer().analyze("здається офіс на 5/9 поверх", {})
    assert res.get("Поверх") == "5"
    assert res.get("Поверховість") == "9"


def test_existing_format_still_works():
    # The pre-existing "N поверх з M" wording must keep working.
    res = _analyzer().analyze("квартира на 12 поверх з 31", {})
    assert res.get("Поверх") == "12"
    assert res.get("Поверховість") == "31"


def test_slash_floor_implausible_total_rejected():
    # "40/7" would be floor 40 of 7 — impossible; both must be dropped, not stored.
    res = _analyzer().analyze("приміщення 40/7 поверх", {})
    assert res.get("Поверх") is None
    assert res.get("Поверховість") is None
