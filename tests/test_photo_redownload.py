"""Тести перезавантаження відсутніх фото у Фазі 2.

Покриває корінь помилки «деякі чернетки збереглися без фото»:
  * Фаза 1 має зберігати оригінальні CRM-URL фото (а не лише локальні шляхи),
    бо саме вони — джерело для перезавантаження у Фазі 2.
  * Перезавантаження має повторювати спроби (retry) і вести детальний лог.
"""

from __future__ import annotations

import main
from crm_data_parser.estate_list_collector import parse_estate_photos_from_html


class _FakeOffer:
    def __init__(self, estate_id, article, offer_data):
        self.estate_id = estate_id
        self.article = article
        self.offer_data = offer_data


class _FakeDB:
    def __init__(self):
        self.updated = {}

    def update_offer_data(self, estate_id, offer_data):
        self.updated[estate_id] = offer_data


# ── _store_downloaded_photos (Фаза 1: збереження джерела) ────────────


def test_store_downloaded_photos_preserves_source_urls():
    od = {"apartment": {"photos": ["/img/a.jpg", "/img/b.jpg"]}}
    main._store_downloaded_photos(od, ["/img/a.jpg", "/img/b.jpg"], ["loc0", "loc1"])
    assert od["apartment"]["photos"] == ["loc0", "loc1"]
    assert od["apartment"]["source_photos"] == ["/img/a.jpg", "/img/b.jpg"]


def test_store_downloaded_photos_creates_apartment_block():
    od = {}
    main._store_downloaded_photos(od, ["/img/a.jpg"], ["loc0"])
    assert od["apartment"]["photos"] == ["loc0"]
    assert od["apartment"]["source_photos"] == ["/img/a.jpg"]


# ── _photo_source_urls (Фаза 2: вибір джерела для перезакачки) ───────


def test_source_urls_from_preserved_source_photos():
    od = {
        "apartment": {
            "source_photos": ["/estate/1/img/a.jpg", "http://x/b.jpg"],
            "photos": ["C:/local/photo_000.jpg"],
        }
    }
    assert main._photo_source_urls(od) == ["/estate/1/img/a.jpg", "http://x/b.jpg"]


def test_source_urls_fallback_to_http_in_photos():
    od = {"apartment": {"photos": ["http://x/b.jpg", "C:/local/photo_000.jpg"]}}
    assert main._photo_source_urls(od) == ["http://x/b.jpg"]


def test_source_urls_empty_when_only_local_paths():
    od = {"apartment": {"photos": ["C:/local/photo_000.jpg"]}}
    assert main._photo_source_urls(od) == []


# ── _download_photos_with_retry ─────────────────────────────────────


def test_retry_returns_immediately_on_full_success(monkeypatch):
    calls = {"n": 0}

    def fake(page, urls, article):
        calls["n"] += 1
        return ["p0", "p1"]

    monkeypatch.setattr("crm_data_parser.download_estate_photos", fake)
    result = main._download_photos_with_retry(None, ["u0", "u1"], "A1", max_attempts=3)
    assert result == ["p0", "p1"]
    assert calls["n"] == 1


def test_retry_keeps_best_partial_across_attempts(monkeypatch):
    seq = [["p0"], ["p0", "p1"]]  # спроба 1 часткова, спроба 2 повна
    calls = {"n": 0}

    def fake(page, urls, article):
        r = seq[calls["n"]]
        calls["n"] += 1
        return r

    monkeypatch.setattr("crm_data_parser.download_estate_photos", fake)
    result = main._download_photos_with_retry(None, ["u0", "u1"], "A1", max_attempts=3)
    assert result == ["p0", "p1"]
    assert calls["n"] == 2


def test_retry_exhausts_and_returns_best(monkeypatch):
    calls = {"n": 0}

    def fake(page, urls, article):
        calls["n"] += 1
        return ["p0"]  # завжди часткова

    monkeypatch.setattr("crm_data_parser.download_estate_photos", fake)
    result = main._download_photos_with_retry(None, ["u0", "u1"], "A1", max_attempts=3)
    assert result == ["p0"]
    assert calls["n"] == 3


def test_retry_handles_exception_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake(page, urls, article):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("net blip")
        return ["p0", "p1"]

    monkeypatch.setattr("crm_data_parser.download_estate_photos", fake)
    result = main._download_photos_with_retry(None, ["u0", "u1"], "A1", max_attempts=3)
    assert result == ["p0", "p1"]
    assert calls["n"] == 2


# ── _redownload_photos_in_session ───────────────────────────────────


def test_redownload_updates_db_on_success(monkeypatch):
    monkeypatch.setattr(
        "crm_data_parser.download_estate_photos",
        lambda page, urls, article: ["new0", "new1"],
    )
    offer = _FakeOffer(
        10,
        "A10",
        {"apartment": {"source_photos": ["/img/a.jpg", "/img/b.jpg"], "photos": ["missing.jpg"]}},
    )
    db = _FakeDB()
    main._redownload_photos_in_session(None, [offer], db)
    assert db.updated[10]["apartment"]["photos"] == ["new0", "new1"]


def test_redownload_skips_offer_without_source_urls(monkeypatch):
    called = {"n": 0}

    def fake(page, urls, article):
        called["n"] += 1
        return ["x"]

    monkeypatch.setattr("crm_data_parser.download_estate_photos", fake)
    offer = _FakeOffer(
        11,
        "A11",
        {
            "apartment": {"photos": ["missing_local.jpg"]},
            "photo_download_link": "/estate/11/download-all-watermark-images",
        },
    )
    db = _FakeDB()
    main._redownload_photos_in_session(None, [offer], db)
    assert called["n"] == 0  # немає джерела → завантаження не пробуємо
    assert 11 not in db.updated


def test_redownload_ignores_offers_with_present_photos(monkeypatch, tmp_path):
    p = tmp_path / "photo_000.jpg"
    p.write_bytes(b"x")
    called = {"n": 0}

    def fake(page, urls, article):
        called["n"] += 1
        return ["x"]

    monkeypatch.setattr("crm_data_parser.download_estate_photos", fake)
    offer = _FakeOffer(12, "A12", {"apartment": {"photos": [str(p)]}})
    db = _FakeDB()
    main._redownload_photos_in_session(None, [offer], db)
    assert called["n"] == 0
    assert 12 not in db.updated


# ── parse_estate_photos_from_html (джерело під час перевірки актуальності) ──


def test_parse_estate_photos_from_html_extracts_hrefs():
    html = """
    <div class="gallery">
      <a class="slider-item fancybox" href="/estate/1/img/a.jpg"></a>
      <a class="slider-item fancybox" href="/estate/1/img/b.jpg"></a>
      <a class="other" href="/nope.jpg"></a>
    </div>
    """
    assert parse_estate_photos_from_html(html) == ["/estate/1/img/a.jpg", "/estate/1/img/b.jpg"]


def test_parse_estate_photos_from_html_empty_when_none():
    assert parse_estate_photos_from_html("<div></div>") == []


# ── _backfill_source_photos (поповнення джерела під час preflight) ──────────


def test_backfill_writes_source_photos_when_absent():
    offer = _FakeOffer(20, "A20", {"apartment": {"photos": ["loc.jpg"]}})
    db = _FakeDB()
    changed = main._backfill_source_photos(offer, ["/img/a.jpg", "/img/b.jpg"], db)
    assert changed is True
    assert offer.offer_data["apartment"]["source_photos"] == ["/img/a.jpg", "/img/b.jpg"]
    assert db.updated[20]["apartment"]["source_photos"] == ["/img/a.jpg", "/img/b.jpg"]


def test_backfill_noop_when_source_already_present():
    offer = _FakeOffer(21, "A21", {"apartment": {"source_photos": ["/old.jpg"], "photos": ["loc.jpg"]}})
    db = _FakeDB()
    changed = main._backfill_source_photos(offer, ["/img/new.jpg"], db)
    assert changed is False
    assert 21 not in db.updated
    assert offer.offer_data["apartment"]["source_photos"] == ["/old.jpg"]


def test_backfill_noop_when_no_photos():
    offer = _FakeOffer(22, "A22", {"apartment": {"photos": ["loc.jpg"]}})
    db = _FakeDB()
    changed = main._backfill_source_photos(offer, [], db)
    assert changed is False
    assert 22 not in db.updated
