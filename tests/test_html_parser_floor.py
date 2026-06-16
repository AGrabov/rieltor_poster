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
