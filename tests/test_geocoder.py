"""Tests for the OSM/Nominatim street-name normalizer."""

from __future__ import annotations

from crm_data_parser import geocoder as gc


class _FakeResp:
    def __init__(self, json_data=None, status_code=200):
        self._json = json_data if json_data is not None else []
        self.status_code = status_code

    def json(self):
        return self._json


def _nominatim_hit(road):
    return [{"address": {"road": road, "city": "Київ"}, "lat": "50.4", "lon": "30.5"}]


def _patch(monkeypatch, fake_get):
    # Disable throttle (no real sleeping) and clear the lru_cache between tests.
    monkeypatch.setattr(gc, "_throttle", lambda: None)
    gc.geocode_canonical_street.cache_clear()
    monkeypatch.setattr(gc.requests, "get", fake_get)


def test_geocode_returns_canonical_road(monkeypatch):
    _patch(monkeypatch, lambda url, **kw: _FakeResp(_nominatim_hit("Шовковична вулиця")))
    assert gc.geocode_canonical_street("Київ", "Шелковичная", "30") == "Шовковична вулиця"


def test_geocode_no_result_returns_none(monkeypatch):
    _patch(monkeypatch, lambda url, **kw: _FakeResp([]))
    assert gc.geocode_canonical_street("Київ", "Неіснуюча", "1") is None


def test_geocode_http_error_is_quiet(monkeypatch, caplog):
    import logging

    _patch(monkeypatch, lambda url, **kw: _FakeResp(status_code=503))
    with caplog.at_level(logging.WARNING):
        assert gc.geocode_canonical_street("Київ", "Будь-яка", "1") is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_geocode_timeout_returns_none(monkeypatch):
    def boom(url, **kw):
        raise gc.requests.exceptions.Timeout("slow")

    _patch(monkeypatch, boom)
    assert gc.geocode_canonical_street("Київ", "Будь-яка", "1") is None


def test_geocode_caches_within_process(monkeypatch):
    calls = []

    def counting_get(url, **kw):
        calls.append(kw["params"]["q"])
        return _FakeResp(_nominatim_hit("Дегтярівська вулиця"))

    _patch(monkeypatch, counting_get)
    a = gc.geocode_canonical_street("Київ", "Дегтяревская", "33")
    b = gc.geocode_canonical_street("Київ", "Дегтяревская", "33")
    assert a == b == "Дегтярівська вулиця"
    assert len(calls) == 1  # second call served from the lru_cache


def test_geocode_requires_numeric_house(monkeypatch):
    # No house number → we must NOT geocode (a houseless query returns a random
    # centroid street, a false match verification can't catch). Safe = skip.
    def fail(url, **kw):
        raise AssertionError("must not query Nominatim without a numeric house")

    _patch(monkeypatch, fail)
    assert gc.geocode_canonical_street("Київ", "Шелковичная", "") is None


def test_geocode_anchors_with_base_house_number(monkeypatch):
    # "35-А" must be queried as base "35" (letter suffixes can break Nominatim).
    seen = {}

    def capture(url, **kw):
        seen["q"] = kw["params"]["q"]
        return _FakeResp(_nominatim_hit("вулиця Василя Стуса"))

    _patch(monkeypatch, capture)
    assert gc.geocode_canonical_street("Київ", "Стуса Василия", "35-А") == "вулиця Василя Стуса"
    assert seen["q"].endswith(" 35")  # base number, no letter


def test_geocode_disabled_via_env(monkeypatch):
    gc.geocode_canonical_street.cache_clear()
    monkeypatch.setenv("GEOCODER_ENABLED", "false")

    def fail(url, **kw):  # must never be called when disabled
        raise AssertionError("geocoder should be disabled")

    monkeypatch.setattr(gc.requests, "get", fail)
    assert gc.geocode_canonical_street("Київ", "Шелковичная", "30") is None
