"""Тести дозаливання фото у чернетки без фото (чиста логіка, без браузера)."""

from __future__ import annotations

import crm_data_parser.photo_downloader as pd
from rieltor_handler.draft_photo_fixer import DraftPhotoFixer, local_photos_for_offer

_OLD = r"C:\Program Files\RieltorPoster\rieltor\crm_data_parser\offers\pics"


# ── local_photos_for_offer ──────────────────────────────────────────────────


def test_local_photos_reroots_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "PICS_DIR", tmp_path)
    art = tmp_path / "A1"
    art.mkdir()
    (art / "photo_000.jpg").write_bytes(b"x")
    od = {"apartment": {"photos": [rf"{_OLD}\A1\photo_000.jpg", rf"{_OLD}\A1\missing.jpg"]}}
    assert local_photos_for_offer(od) == [str(art / "photo_000.jpg")]


def test_local_photos_empty_when_none_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "PICS_DIR", tmp_path)
    od = {"apartment": {"photos": [rf"{_OLD}\A1\photo_000.jpg"]}}
    assert local_photos_for_offer(od) == []


# ── fix cycle (browser primitives faked) ────────────────────────────────────


class _Rec:
    def __init__(self, offer_data):
        self.offer_data = offer_data


class _FakeDB:
    def __init__(self, by_article):
        self._m = by_article

    def get_by_article(self, article):
        return self._m.get(article)


class _FakeFixer(DraftPhotoFixer):
    """DraftPhotoFixer з підробленими браузерними примітивами."""

    def __init__(self, rows, edit_state, db, *, dry_run):
        super().__init__(page=None, db=db, dry_run=dry_run)
        self._rows = rows  # [(key, href, date)]
        self._state = edit_state  # href -> {"photos": int, "article": str}
        self._cur = {}
        self.uploaded = []

    def list_draft_rows(self):
        return self._rows

    def open_edit(self, href):
        self._cur = self._state.get(href, {})

    def photo_count(self):
        return self._cur.get("photos", 0)

    def read_article(self):
        return self._cur.get("article")

    def upload_photos(self, paths):
        self.uploaded.append((self._cur.get("article"), list(paths)))
        return True


def _od(photos):
    return {"apartment": {"photos": photos}}


def test_cycle_skips_drafts_with_photos(monkeypatch):
    monkeypatch.setattr(
        "rieltor_handler.draft_photo_fixer.local_photos_for_offer", lambda od: ["p"]
    )
    rows = [("100", "/offers/edit/100", None)]
    state = {"/offers/edit/100": {"photos": 5, "article": "A1"}}
    fx = _FakeFixer(rows, state, _FakeDB({"A1": _Rec(_od([]))}), dry_run=False)
    s = fx.fix_drafts()
    assert s.already == ["100"]
    assert fx.uploaded == []


def test_cycle_uploads_when_missing_and_local_present(monkeypatch):
    monkeypatch.setattr(
        "rieltor_handler.draft_photo_fixer.local_photos_for_offer", lambda od: ["pA", "pB"]
    )
    rows = [("100", "/offers/edit/100", None)]
    state = {"/offers/edit/100": {"photos": 0, "article": "A1"}}
    fx = _FakeFixer(rows, state, _FakeDB({"A1": _Rec(_od(["x"]))}), dry_run=False)
    s = fx.fix_drafts()
    assert s.fixed == ["A1"]
    assert fx.uploaded == [("A1", ["pA", "pB"])]


def test_cycle_dry_run_does_not_upload(monkeypatch):
    monkeypatch.setattr(
        "rieltor_handler.draft_photo_fixer.local_photos_for_offer", lambda od: ["pA"]
    )
    rows = [("100", "/offers/edit/100", None)]
    state = {"/offers/edit/100": {"photos": 0, "article": "A1"}}
    fx = _FakeFixer(rows, state, _FakeDB({"A1": _Rec(_od(["x"]))}), dry_run=True)
    s = fx.fix_drafts()
    assert s.fixed == ["A1"]
    assert fx.uploaded == []  # dry-run: нічого не заливаємо


def test_cycle_no_source_when_no_local_photos(monkeypatch):
    monkeypatch.setattr(
        "rieltor_handler.draft_photo_fixer.local_photos_for_offer", lambda od: []
    )
    rows = [("100", "/offers/edit/100", None)]
    state = {"/offers/edit/100": {"photos": 0, "article": "A1"}}
    fx = _FakeFixer(rows, state, _FakeDB({"A1": _Rec(_od([]))}), dry_run=False)
    s = fx.fix_drafts()
    assert s.no_source == ["A1"]
    assert fx.uploaded == []


def test_cycle_max_count_limits_acted(monkeypatch):
    monkeypatch.setattr(
        "rieltor_handler.draft_photo_fixer.local_photos_for_offer", lambda od: ["p"]
    )
    rows = [(str(i), f"/offers/edit/{i}", None) for i in range(5)]
    state = {f"/offers/edit/{i}": {"photos": 0, "article": f"A{i}"} for i in range(5)}
    db = _FakeDB({f"A{i}": _Rec(_od(["x"])) for i in range(5)})
    fx = _FakeFixer(rows, state, db, dry_run=False)
    s = fx.fix_drafts(max_count=2)
    assert len(fx.uploaded) == 2
    assert len(s.fixed) == 2
