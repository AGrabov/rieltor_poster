"""Tests for cadastral number lookup: strict house+street match + parsers."""

from __future__ import annotations

from crm_data_parser import cadastral_lookup as cl


# ── _pick_verified: strict house + street match ───────────────────────────
def test_pick_verified_returns_exact_house_on_matching_street():
    candidates = [
        ("8000000000:75:214:0033", "м.Київ, вулиця Львівська, 19-а"),
        ("8000000000:75:214:0010", "м.Київ, вулиця Львівська, 19"),
        ("8000000000:75:214:0012", "м.Київ, вулиця Львівська, 19-і"),
    ]
    assert cl._pick_verified(candidates, "Львівська", "19") == "8000000000:75:214:0010"


def test_pick_verified_no_exact_house_returns_none():
    # Only suffixed houses (19-а, 19-і) — strict match must NOT fill.
    candidates = [
        ("8000000000:75:214:0033", "м.Київ, вулиця Львівська, 19-а"),
        ("8000000000:75:214:0012", "м.Київ, вулиця Львівська, 19-і"),
    ]
    assert cl._pick_verified(candidates, "Львівська", "19") is None


def test_pick_verified_street_mismatch_returns_none():
    # House 19 matches but the street is different → do not fill.
    candidates = [
        ("8000000000:75:214:0010", "м.Київ, вулиця Садова, 19"),
    ]
    assert cl._pick_verified(candidates, "Львівська", "19") is None


def test_pick_verified_empty_house_returns_none():
    candidates = [
        ("8000000000:75:214:0010", "м.Київ, вулиця Львівська, 19"),
    ]
    assert cl._pick_verified(candidates, "Львівська", "") is None


def test_pick_verified_tolerates_ru_ua_spelling():
    # CRM street "Пушкинська" (RU и) vs registry "Пушкінська" (UA і).
    candidates = [
        ("8000000000:76:024:0044", "м. Київ, вул. Пушкінська, 1"),
    ]
    assert cl._pick_verified(candidates, "Пушкинська", "1") == "8000000000:76:024:0044"


def test_pick_verified_empty_candidates_returns_none():
    assert cl._pick_verified([], "Львівська", "19") is None


# ── street-type disambiguation (вул./пров./пл. Шевченка) ──────────────────
_SHEVCHENKA = [
    ("8000000000:01:001:0001", "м.Київ, вулиця Шевченка, 19"),
    ("8000000000:01:001:0002", "м.Київ, провулок Шевченка, 19"),
    ("8000000000:01:001:0003", "м.Київ, площа Шевченка, 19"),
]


def test_pick_verified_picks_matching_street_type():
    assert cl._pick_verified(_SHEVCHENKA, "вул. Шевченка", "19") == "8000000000:01:001:0001"


def test_pick_verified_russian_type_maps_to_ukrainian():
    # CRM "пер." (RU) must select провулок, not вулиця.
    assert cl._pick_verified(_SHEVCHENKA, "пер. Шевченка", "19") == "8000000000:01:001:0002"


def test_pick_verified_type_known_but_absent_returns_none():
    # CRM says бульвар, but no бульвар candidate → do not guess.
    assert cl._pick_verified(_SHEVCHENKA, "бул. Шевченка", "19") is None


def test_pick_verified_ambiguous_types_without_crm_type_returns_none():
    # CRM has no type and the registry offers several types → cannot disambiguate.
    assert cl._pick_verified(_SHEVCHENKA, "Шевченка", "19") is None


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
    assert cl._search_zem_center("Київ Львівська 19", "Львівська", "19") == "8000000000:75:214:0010"


def test_search_zem_center_handles_error(monkeypatch):
    def fake_get(url, **kwargs):
        raise cl.requests.exceptions.Timeout("slow")

    monkeypatch.setattr(cl.requests, "get", fake_get)
    assert cl._search_zem_center("Київ Львівська 19", "Львівська", "19") is None


_KK_HTML = """
<a data-action="search#linkClicked">
  <div class="font-bold">8000000000:75:214:0033</div>
  <div class="text-gray-500">м.Київ, вулиця Львівська, 19-а</div>
</a>
<a data-action="search#linkClicked">
  <div class="font-bold">8000000000:75:214:0010</div>
  <div class="text-gray-500">м.Київ, вулиця Львівська, 19</div>
</a>
"""


def test_search_kadastrova_karta_picks_exact_house(monkeypatch):
    def fake_get(url, **kwargs):
        return _FakeResp(text=_KK_HTML)

    monkeypatch.setattr(cl.requests, "get", fake_get)
    got = cl._search_kadastrova_karta("Київ Львівська 19", "Львівська", "19")
    assert got == "8000000000:75:214:0010"


def test_lookup_normalizes_city_and_strips_street(monkeypatch):
    seen = {}

    def fake_zem(query, street, house):
        seen["query"] = query
        seen["street"] = street
        return "8000000000:75:214:0010"

    monkeypatch.setattr(cl, "_search_zem_center", fake_zem)
    # The QUERY must be normalized (RU city → UA, street type stripped);
    # the street arg keeps the original so the type stays available for selection.
    result = cl.lookup_cadastral_number("Киев", "ул. Львівська", "19")
    assert result == "8000000000:75:214:0010"
    assert "Київ" in seen["query"]
    assert "ул." not in seen["query"]
    assert "Львівська" in seen["query"]
    assert seen["street"] == "ул. Львівська"


def test_lookup_uses_zem_first(monkeypatch):
    calls = []
    monkeypatch.setattr(cl, "_search_zem_center", lambda q, s, h: calls.append(("zem", q)) or "8000000000:75:214:0010")
    monkeypatch.setattr(cl, "_search_kadastrova_karta", lambda q, s, h: calls.append(("kk", q)) or None)
    result = cl.lookup_cadastral_number("Київ", "вул. Львівська", "19")
    assert result == "8000000000:75:214:0010"
    assert calls[0][0] == "zem"
    assert all(c[0] != "kk" for c in calls)


def test_lookup_falls_back_to_kadastrova(monkeypatch):
    monkeypatch.setattr(cl, "_search_zem_center", lambda q, s, h: None)
    monkeypatch.setattr(cl, "_search_kadastrova_karta", lambda q, s, h: "8000000000:75:214:0099")
    result = cl.lookup_cadastral_number("Київ", "вул. Львівська", "19")
    assert result == "8000000000:75:214:0099"


def test_lookup_no_kadastr_live_references():
    # kadastr.live is dead — ensure it is fully removed from the module
    import inspect

    src = inspect.getsource(cl)
    assert "kadastr.live" not in src
    assert "_search_raw" not in src
