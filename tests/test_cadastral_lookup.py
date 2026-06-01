"""Tests for cadastral number lookup: house matching + source parsers."""

from __future__ import annotations

from crm_data_parser import cadastral_lookup as cl


def test_pick_by_house_prefers_exact_over_suffix():
    # API returns 19-а, 19, 19-і in this order; exact "19" must win
    candidates = [
        ("8000000000:75:214:0033", "м.Київ, вулиця Львівська, 19-а"),
        ("8000000000:75:214:0010", "м.Київ, вулиця Львівська, 19"),
        ("8000000000:75:214:0012", "м.Київ, вулиця Львівська, 19-і"),
    ]
    assert cl._pick_by_house(candidates, "19") == "8000000000:75:214:0010"


def test_pick_by_house_falls_back_to_suffix_when_no_exact():
    candidates = [
        ("8000000000:75:214:0033", "м.Київ, вулиця Львівська, 19-а"),
        ("8000000000:75:214:0012", "м.Київ, вулиця Львівська, 19-і"),
    ]
    # No bare "19" → first suffix match returned
    assert cl._pick_by_house(candidates, "19") == "8000000000:75:214:0033"


def test_pick_by_house_no_house_returns_first():
    candidates = [
        ("8000000000:75:214:0033", "м.Київ, вулиця Львівська, 19-а"),
        ("8000000000:75:214:0010", "м.Київ, вулиця Львівська, 19"),
    ]
    assert cl._pick_by_house(candidates, "") == "8000000000:75:214:0033"


def test_pick_by_house_empty_candidates_returns_none():
    assert cl._pick_by_house([], "19") is None
