"""Тести дозаливання фото/опису у неповні чернетки (чиста логіка, без браузера)."""

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
    def __init__(self, by_id):
        self._m = by_id

    def get_offer(self, estate_id):
        return self._m.get(estate_id)


class _FakeFixer(DraftPhotoFixer):
    def __init__(self, rows, edit_state, db, *, dry_run):
        super().__init__(page=None, db=db, dry_run=dry_run)
        self._rows = rows  # [(rid, href)]
        self._state = edit_state  # href -> {photos:int, desc:str, estate_id:int}
        self._cur = {}
        self.applied = []  # (offer_data, pt, dt)

    def list_draft_rows(self):
        return self._rows

    def open_edit(self, href):
        self._cur = self._state.get(href, {})

    def site_photo_count(self):
        return self._cur.get("photos", 0)

    def site_description(self):
        return self._cur.get("desc", "")

    def read_estate_id(self):
        return self._cur.get("estate_id")

    def apply_fix(self, offer_data, pt, dt):
        self.applied.append((offer_data, pt, dt))
        return True


def _offer(desc="db desc", photos=("x",), pt="Квартира", dt="Продаж"):
    return _Rec({"property_type": pt, "offer_type": dt, "apartment": {"description": desc, "photos": list(photos)}})


def test_cycle_skips_complete_drafts():
    rows = [("100", "/offers/edit/100")]
    state = {"/offers/edit/100": {"photos": 5, "desc": "є опис", "estate_id": 1}}
    fx = _FakeFixer(rows, state, _FakeDB({1: _offer()}), dry_run=False)
    s = fx.fix_drafts()
    assert s.already == [1]
    assert fx.applied == []


def test_cycle_fixes_photos_only_when_desc_present(monkeypatch):
    monkeypatch.setattr("rieltor_handler.draft_photo_fixer.local_photos_for_offer", lambda od: ["pA", "pB"])
    rows = [("100", "/offers/edit/100")]
    state = {"/offers/edit/100": {"photos": 0, "desc": "вже є опис", "estate_id": 1}}
    fx = _FakeFixer(rows, state, _FakeDB({1: _offer()}), dry_run=False)
    s = fx.fix_drafts()
    assert s.fixed == [(1, ["photos:2"])]
    assert fx.applied == [({"apartment": {"photos": ["pA", "pB"]}}, "Квартира", "Продаж")]
    assert s.needs_crm == []


def test_cycle_fixes_desc_and_flags_crm_when_no_local_photos(monkeypatch):
    monkeypatch.setattr("rieltor_handler.draft_photo_fixer.local_photos_for_offer", lambda od: [])
    rows = [("100", "/offers/edit/100")]
    state = {"/offers/edit/100": {"photos": 0, "desc": "", "estate_id": 1}}
    fx = _FakeFixer(rows, state, _FakeDB({1: _offer(desc="реальний опис")}), dry_run=False)
    s = fx.fix_drafts()
    assert s.needs_crm == [1]
    assert s.fixed == [(1, ["desc"])]
    assert fx.applied == [({"apartment": {"description": "реальний опис"}}, "Квартира", "Продаж")]


def test_cycle_no_action_when_only_photos_needed_but_no_local(monkeypatch):
    monkeypatch.setattr("rieltor_handler.draft_photo_fixer.local_photos_for_offer", lambda od: [])
    rows = [("100", "/offers/edit/100")]
    state = {"/offers/edit/100": {"photos": 0, "desc": "опис є", "estate_id": 1}}
    fx = _FakeFixer(rows, state, _FakeDB({1: _offer()}), dry_run=False)
    s = fx.fix_drafts()
    assert s.needs_crm == [1]
    assert s.fixed == []
    assert fx.applied == []


def test_cycle_records_crm_targets_for_needs_crm(monkeypatch):
    """needs_crm-чернетки записують (estate_id, rid, href) у crm_targets для фолбеку."""
    monkeypatch.setattr("rieltor_handler.draft_photo_fixer.local_photos_for_offer", lambda od: [])
    rows = [("100", "/offers/edit/100")]
    state = {"/offers/edit/100": {"photos": 0, "desc": "опис є", "estate_id": 1}}
    fx = _FakeFixer(rows, state, _FakeDB({1: _offer()}), dry_run=False)
    s = fx.fix_drafts()
    assert s.crm_targets == [(1, "100", "/offers/edit/100")]


def test_cycle_no_crm_target_when_local_photos_exist(monkeypatch):
    """Якщо локальні фото є — чернетка не потрапляє у crm_targets."""
    monkeypatch.setattr("rieltor_handler.draft_photo_fixer.local_photos_for_offer", lambda od: ["pA"])
    rows = [("100", "/offers/edit/100")]
    state = {"/offers/edit/100": {"photos": 0, "desc": "опис є", "estate_id": 1}}
    fx = _FakeFixer(rows, state, _FakeDB({1: _offer()}), dry_run=False)
    s = fx.fix_drafts()
    assert s.crm_targets == []


def test_cycle_dry_run_does_not_apply(monkeypatch):
    monkeypatch.setattr("rieltor_handler.draft_photo_fixer.local_photos_for_offer", lambda od: ["pA"])
    rows = [("100", "/offers/edit/100")]
    state = {"/offers/edit/100": {"photos": 0, "desc": "опис", "estate_id": 1}}
    fx = _FakeFixer(rows, state, _FakeDB({1: _offer()}), dry_run=True)
    s = fx.fix_drafts()
    assert s.fixed == [(1, ["photos:1"])]
    assert fx.applied == []


def test_cycle_no_db_when_estate_id_unmatched():
    rows = [("100", "/offers/edit/100")]
    state = {"/offers/edit/100": {"photos": 0, "desc": "", "estate_id": None}}
    fx = _FakeFixer(rows, state, _FakeDB({}), dry_run=False)
    s = fx.fix_drafts()
    assert s.no_db == ["100"]
    assert fx.applied == []


def test_cycle_max_count_limits_applies(monkeypatch):
    monkeypatch.setattr("rieltor_handler.draft_photo_fixer.local_photos_for_offer", lambda od: ["p"])
    rows = [(str(i), f"/offers/edit/{i}") for i in range(5)]
    state = {f"/offers/edit/{i}": {"photos": 0, "desc": "опис", "estate_id": i} for i in range(5)}
    db = _FakeDB({i: _offer() for i in range(5)})
    fx = _FakeFixer(rows, state, db, dry_run=False)
    s = fx.fix_drafts(max_count=2)
    assert len(fx.applied) == 2
    assert len(s.fixed) == 2
