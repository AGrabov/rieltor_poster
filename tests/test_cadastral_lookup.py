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
    assert cl._pick_verified(candidates, "Львівська", "19")[0] == "8000000000:75:214:0010"


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
    assert cl._pick_verified(candidates, "Пушкинська", "1")[0] == "8000000000:76:024:0044"


def test_pick_verified_empty_candidates_returns_none():
    assert cl._pick_verified([], "Львівська", "19") is None


# ── house-format tolerance (19А = 19-а = 19 а) ────────────────────────────
def test_pick_verified_house_letter_variants_match():
    candidates = [("8000000000:75:214:0033", "м.Київ, вулиця Львівська, 19-а")]
    for crm_house in ("19А", "19-а", "19 а", "19а"):
        assert cl._pick_verified(candidates, "Львівська", crm_house)[0] == "8000000000:75:214:0033"


def test_pick_verified_bare_number_differs_from_lettered():
    # "19" and "19-а" are different parcels — must not match.
    candidates = [("8000000000:75:214:0033", "м.Київ, вулиця Львівська, 19-а")]
    assert cl._pick_verified(candidates, "Львівська", "19") is None


# ── street-type disambiguation (вул./пров./пл. Шевченка) ──────────────────
_SHEVCHENKA = [
    ("8000000000:01:001:0001", "м.Київ, вулиця Шевченка, 19"),
    ("8000000000:01:001:0002", "м.Київ, провулок Шевченка, 19"),
    ("8000000000:01:001:0003", "м.Київ, площа Шевченка, 19"),
]


def test_pick_verified_picks_matching_street_type():
    assert cl._pick_verified(_SHEVCHENKA, "вул. Шевченка", "19")[0] == "8000000000:01:001:0001"


def test_pick_verified_russian_type_maps_to_ukrainian():
    # CRM "пер." (RU) must select провулок, not вулиця.
    assert cl._pick_verified(_SHEVCHENKA, "пер. Шевченка", "19")[0] == "8000000000:01:001:0002"


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
    assert cl._search_zem_center("Київ Львівська 19", "Львівська", "19")[0] == "8000000000:75:214:0010"


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
    assert got[0] == "8000000000:75:214:0010"


def test_lookup_normalizes_city_and_strips_street(monkeypatch):
    seen = {}

    def fake_zem(query, street, house):
        seen["query"] = query
        seen["street"] = street
        return ("8000000000:75:214:0010", "м.Київ, вулиця Львівська, 19")

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
    monkeypatch.setattr(
        cl,
        "_search_zem_center",
        lambda q, s, h: calls.append(("zem", q)) or ("8000000000:75:214:0010", "м.Київ, вул. Львівська, 19"),
    )
    monkeypatch.setattr(cl, "_search_kadastrova_karta", lambda q, s, h: calls.append(("kk", q)) or None)
    result = cl.lookup_cadastral_number("Київ", "вул. Львівська", "19")
    assert result == "8000000000:75:214:0010"
    assert calls[0][0] == "zem"
    assert all(c[0] != "kk" for c in calls)


def test_lookup_falls_back_to_kadastrova(monkeypatch):
    monkeypatch.setattr(cl, "_search_zem_center", lambda q, s, h: None)
    monkeypatch.setattr(
        cl, "_search_kadastrova_karta", lambda q, s, h: ("8000000000:75:214:0099", "м.Київ, вул. Львівська, 19")
    )
    result = cl.lookup_cadastral_number("Київ", "вул. Львівська", "19")
    assert result == "8000000000:75:214:0099"


# ── registry address parsing (Район + street type) ───────────────────────
def test_parse_registry_address_extracts_raion_and_street_with_type():
    parsed = cl.parse_registry_address("м.Київ, Дарницький р-н, шосе Харківське, 201-203")
    assert parsed["Район"] == "Дарницький"
    assert parsed["Вулиця"] == "Харківське шосе"


def test_parse_registry_address_drops_default_vulytsia_type():
    parsed = cl.parse_registry_address("Київська обл., Бучанський р-н, вул. Воскресенська, 16")
    assert parsed["Район"] == "Бучанський"
    assert parsed["Вулиця"] == "Воскресенська"


def test_format_registry_street_reorders_type_last():
    assert cl._format_registry_street("шосе Харківське") == "Харківське шосе"
    assert cl._format_registry_street("проспект Перемоги") == "Перемоги проспект"
    assert cl._format_registry_street("вул. Хрещатик") == "Хрещатик"


def test_street_base_ignores_type_for_comparison():
    # Same street with/without type must compare equal (so we can add the type).
    assert cl._street_base("Харківське") == cl._street_base("Харківське шосе")
    assert cl._street_base("вул. Воскресенська") == cl._street_base("Воскресенська")


# ── registry overwrite gated by the same match criteria as the cadnum ─────
# (house+suffix exact, street type matches when known, name fuzzy)
_REG = "м.Київ, Дарницький р-н, шосе Харківське, 201-203"


def test_registry_matches_when_house_and_name_match_no_crm_type():
    # CRM type missing (historical) — name + house match, type unambiguous → match.
    addr = {"Вулиця": "Харківське", "Будинок": "201-203"}
    assert cl._registry_matches_crm(addr, _REG) is True


def test_registry_no_match_on_house_mismatch():
    addr = {"Вулиця": "Харківське", "Будинок": "16"}
    assert cl._registry_matches_crm(addr, _REG) is False


def test_registry_no_match_on_street_type_mismatch():
    # CRM says площа, registry is шосе → type conflict, do not overwrite.
    addr = {"Вулиця": "Харківська площа", "Будинок": "201-203"}
    assert cl._registry_matches_crm(addr, _REG) is False


def test_registry_no_match_on_name_mismatch():
    addr = {"Вулиця": "Львівська", "Будинок": "201-203"}
    assert cl._registry_matches_crm(addr, _REG) is False


# ── street type recovery from description ─────────────────────────────────
def test_recover_street_type_finds_type_in_text():
    from crm_data_parser.address_normalize import recover_street_type

    assert recover_street_type("Харківське", "офіс на Харківське шосе, 201") == "Харківське шосе"
    assert recover_street_type("Перемоги", "проспект Перемоги, 5") == "Перемоги проспект"


def test_recover_street_type_keeps_default_and_existing():
    from crm_data_parser.address_normalize import recover_street_type

    # default "вул" is implicit — not appended; already-typed street unchanged.
    assert recover_street_type("Воскресенська", "вул. Воскресенська 16") == "Воскресенська"
    assert recover_street_type("Харківське шосе", "anything") == "Харківське шосе"


def test_lookup_no_kadastr_live_references():
    # kadastr.live is dead — ensure it is fully removed from the module
    import inspect

    src = inspect.getsource(cl)
    assert "kadastr.live" not in src
    assert "_search_raw" not in src


# ── word-order / honorific tolerance ──────────────────────────────────────
# CRM stores "Туполєва Академіка"; the registry's canonical form is
# "Академіка Туполєва" (the honorific title precedes the surname).
_TUPOLEVA_REG = "м.Київ, Солом'янський р-н, вулиця Академіка Туполєва, 18Д"


def test_street_matches_ignores_word_order():
    # Same words, swapped order must still verify as the same street.
    assert cl._street_matches("Туполєва Академіка", _TUPOLEVA_REG) is True
    assert cl._street_matches("Академіка Туполєва", _TUPOLEVA_REG) is True


def test_street_matches_rejects_when_a_word_is_absent():
    # All query words must be present — a different surname must not pass.
    assert cl._street_matches("Академіка Глушкова", _TUPOLEVA_REG) is False


def test_pick_verified_accepts_swapped_honorific_street():
    candidates = [("8000000000:69:001:0001", _TUPOLEVA_REG)]
    got = cl._pick_verified(candidates, "Туполєва Академіка", "18д")
    assert got is not None
    assert got[0] == "8000000000:69:001:0001"


def test_street_variants_fronts_the_honorific():
    variants = cl._street_variants("Туполєва Академіка")
    assert "Туполєва Академіка" in variants
    assert "Академіка Туполєва" in variants


def test_street_variants_no_honorific_is_single():
    # Ordinary given-name + surname must NOT spawn a reversed query (avoid noise).
    assert cl._street_variants("Григорія Кочура") == ["Григорія Кочура"]


def test_lookup_finds_via_reordered_query(monkeypatch):
    # zem.center only returns the parcel for the corrected word order.
    seen_queries = []

    def fake_get(url, **kwargs):
        q = kwargs["params"]["q"]
        seen_queries.append(q)
        if "Академіка Туполєва" in q:
            return _FakeResp(json_data={"items": [{"cadnum": "8000000000:69:001:0001", "address": _TUPOLEVA_REG}]})
        return _FakeResp(json_data={"items": []})

    monkeypatch.setattr(cl.requests, "get", fake_get)
    got = cl.lookup_cadastral_record("Київ", "Туполєва Академіка", "18д")
    assert got is not None
    assert got[0] == "8000000000:69:001:0001"
    # The CRM word order was tried first, then the corrected variant.
    assert any("Академіка Туполєва" in q for q in seen_queries)


def test_lookup_finds_ru_street_via_transliteration(monkeypatch):
    # CRM stores the street in Russian; the registry only answers to Ukrainian.
    reg = "м.Київ, Печерський р-н, вулиця Звіринецька, 72"

    def fake_get(url, **kwargs):
        q = kwargs["params"]["q"]
        # Rule-based translit yields "Зверинецька" (stem е→і is lexical); the
        # registry answers to that and returns the "Звіринецька" parcel.
        if "Зверинецька" in q:
            return _FakeResp(json_data={"items": [{"cadnum": "8000000000:82:262:0002", "address": reg}]})
        return _FakeResp(json_data={"items": []})

    monkeypatch.setattr(cl.requests, "get", fake_get)
    got = cl.lookup_cadastral_record("Киев", "Зверинецкая", "72")
    assert got is not None
    assert got[0] == "8000000000:82:262:0002"


# ── kadastrova-karta.com unavailable: quiet, no traceback, returns None ────
def test_search_kadastrova_karta_503_is_quiet(monkeypatch, caplog):
    import logging

    def fake_get(url, **kwargs):
        return _FakeResp(status_code=503, text="Service Unavailable")

    monkeypatch.setattr(cl.requests, "get", fake_get)
    with caplog.at_level(logging.WARNING):
        got = cl._search_kadastrova_karta("Київ Туполєва Академіка", "Туполєва Академіка", "18д")
    assert got is None
    # An expected "site down" must not be logged at WARNING (no traceback spam).
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
