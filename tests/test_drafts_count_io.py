from __future__ import annotations

from main import read_drafts_count, write_drafts_count


def test_write_then_read_roundtrip(tmp_path):
    f = tmp_path / "drafts_count.json"
    write_drafts_count(7, path=f)
    assert read_drafts_count(path=f) == 7


def test_read_missing_file_returns_none(tmp_path):
    assert read_drafts_count(path=tmp_path / "nope.json") is None
