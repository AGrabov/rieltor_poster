"""Тести перерахунку (re-root) шляхів фото після переносу встановлення.

БД зберігає абсолютні шляхи зі старого місця (Program Files); програму перенесено
в AppData\\Local. Файли лишилися на диску за новим base — їх треба знаходити
відносно поточного PICS_DIR.
"""

from __future__ import annotations

import crm_data_parser.photo_downloader as pd
import main

_OLD_BASE = r"C:\Program Files\RieltorPoster\rieltor\crm_data_parser\offers\pics"


def test_resolve_returns_existing_path(tmp_path):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x")
    assert pd.resolve_local_photo(str(f)) == f


def test_resolve_reroots_to_current_pics_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "PICS_DIR", tmp_path)
    art = tmp_path / "A31251"
    art.mkdir()
    real = art / "photo_000.jpg"
    real.write_bytes(b"x")
    stored = rf"{_OLD_BASE}\A31251\photo_000.jpg"
    assert pd.resolve_local_photo(stored) == real


def test_resolve_none_when_missing_everywhere(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "PICS_DIR", tmp_path)
    assert pd.resolve_local_photo(rf"{_OLD_BASE}\A1\photo_000.jpg") is None


def test_resolve_photo_paths_keeps_original_when_unresolved(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "PICS_DIR", tmp_path)
    art = tmp_path / "A1"
    art.mkdir()
    (art / "photo_000.jpg").write_bytes(b"x")
    paths = [rf"{_OLD_BASE}\A1\photo_000.jpg", rf"{_OLD_BASE}\A1\missing.jpg"]
    resolved = pd.resolve_photo_paths(paths)
    assert resolved[0] == str(art / "photo_000.jpg")  # re-rooted
    assert resolved[1] == paths[1]  # unresolved → original kept


def test_photos_missing_false_when_reroot_finds_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "PICS_DIR", tmp_path)
    art = tmp_path / "A1"
    art.mkdir()
    (art / "photo_000.jpg").write_bytes(b"x")
    od = {"apartment": {"photos": [rf"{_OLD_BASE}\A1\photo_000.jpg"]}}
    assert main._photos_missing(od) is False


def test_photos_missing_true_when_reroot_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "PICS_DIR", tmp_path)
    od = {"apartment": {"photos": [rf"{_OLD_BASE}\A1\photo_000.jpg"]}}
    assert main._photos_missing(od) is True
