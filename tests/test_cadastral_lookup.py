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


class _FakeResp:
    def __init__(self, json_data=None, status_code=200, text=""):
        self._json = json_data if json_data is not None else {}
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


_ZEM_SAMPLE = {
    "items": [
        {"cadnum": "8000000000:75:214:0033", "address": "м.Київ, вулиця Львівська, 19-а"},
        {"cadnum": "8000000000:75:214:0010", "address": "м.Київ, вулиця Львівська, 19"},
        {"cadnum": "not-a-cadnum", "address": "junk"},
    ]
}


def test_search_zem_center_picks_exact_house(monkeypatch):
    def fake_get(url, **kwargs):
        assert "api.zem.center" in url
        return _FakeResp(json_data=_ZEM_SAMPLE)

    monkeypatch.setattr(cl.requests, "get", fake_get)
    assert cl._search_zem_center("Київ Львівська 19", "19") == "8000000000:75:214:0010"


def test_search_zem_center_handles_error(monkeypatch):
    def fake_get(url, **kwargs):
        raise cl.requests.exceptions.Timeout("slow")

    monkeypatch.setattr(cl.requests, "get", fake_get)
    assert cl._search_zem_center("Київ Львівська 19", "19") is None
