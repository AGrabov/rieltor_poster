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


def test_floor_types_coerced_to_string():
    # Числовий поверх/поверховість зберігаємо як РЯДОК (єдиний тип у даних).
    data = {"Поверх": 5, "Поверховість": 9, "property_type": "Квартира"}
    out = _parser()._fill_missing_with_defaults(data)
    assert out["Поверх"] == "5"
    assert out["Поверховість"] == "9"
    assert isinstance(out["Поверх"], str)
    assert isinstance(out["Поверховість"], str)


# ── description vs CRM floor precedence (_accept_description_floor) ────────
# Signature: _accept_description_floor(crm_floor, crm_storeys, text_floor) -> bool
# True  → take the description's floor (CRM cell is broken AND text is a downward fix)
# False → keep the CRM floor (it's plausible, or the text "fix" is not smaller)
_accept = HTMLOfferParser._accept_description_floor


def test_valid_crm_floor_not_overwritten():
    # Regression (log: 9 → 40). CRM 9/16 is plausible → ignore description guess.
    assert _accept("9", "16", "40") is False


def test_impossible_crm_floor_corrected_downward():
    # CRM 40/7 is impossible; description's 3 is smaller → accept the correction.
    assert _accept("40", "7", "3") is True


def test_impossible_crm_floor_not_corrected_upward():
    # CRM 40/7 is impossible but description's 50 is even bigger → likely garbage, reject.
    assert _accept("40", "7", "50") is False


def test_high_floor_above_threshold_corrected_downward():
    # CRM 35/50 is possible but implausibly high (>30) → accept the smaller text floor.
    assert _accept("35", "50", "3") is True


def test_high_floor_above_threshold_not_corrected_upward():
    # >30 and possible, but the text proposes an even higher floor → reject.
    assert _accept("35", "50", "38") is False


def test_floor_at_threshold_is_trusted():
    # Exactly 30 is the cutoff (> 30 is suspicious) and ≤ storeys → keep CRM.
    assert _accept("30", "50", "5") is False


def test_non_numeric_floor_keeps_crm():
    # Garbage in either cell must not crash and must not trigger an overwrite.
    assert _accept("цоколь", "5", "3") is False
    assert _accept("40", "7", "поверх") is False
