"""Tests for the floor sanity guard in HTMLOfferParser._fill_missing_with_defaults.

A floor higher than the building's storey count is physically impossible. When the
CRM "Поверх" cell holds such a value and the description offers no correction, the
parser must drop the impossible floor rather than publish it.
"""

from __future__ import annotations

from crm_data_parser.html_parser import HTMLOfferParser


def _parser():
    p = HTMLOfferParser.__new__(HTMLOfferParser)
    p.label_to_field = {}
    return p


def test_floor_inversion_drops_floor():
    data = {"Поверх": "40", "Поверховість": "7", "property_type": "Комерційна"}
    out = _parser()._fill_missing_with_defaults(data)
    assert "Поверх" not in out
    assert out["Поверховість"] == "7"


def test_valid_floor_kept():
    data = {"Поверх": "3", "Поверховість": "7", "property_type": "Комерційна"}
    out = _parser()._fill_missing_with_defaults(data)
    assert out["Поверх"] == "3"
    assert out["Поверховість"] == "7"


def test_equal_floor_kept():
    # Single-storey: floor 1 of 1 is valid.
    data = {"Поверх": "1", "Поверховість": "1", "property_type": "Комерційна"}
    out = _parser()._fill_missing_with_defaults(data)
    assert out["Поверх"] == "1"


# ── description must NOT overwrite a valid CRM floor (regression: 9 → 40) ──
def test_crm_floor_authoritative_when_consistent():
    # CRM 9/16 is physically possible → CRM wins, description guess ignored.
    assert HTMLOfferParser._crm_floor_authoritative({"Поверх": "9", "Поверховість": "16"}) is True


def test_crm_floor_not_authoritative_when_impossible():
    # CRM 40/7 is impossible → description is allowed to correct it.
    assert HTMLOfferParser._crm_floor_authoritative({"Поверх": "40", "Поверховість": "7"}) is False


def test_crm_floor_authoritative_when_storeys_missing():
    # No storey count to contradict the floor → keep the CRM floor.
    assert HTMLOfferParser._crm_floor_authoritative({"Поверх": "9"}) is True


def test_crm_floor_authoritative_when_non_numeric():
    # Garbage in the cell must not crash the precedence check.
    assert HTMLOfferParser._crm_floor_authoritative({"Поверх": "цоколь", "Поверховість": "5"}) is True
