"""Тести класифікації папок фото для команди clean-pics (чиста логіка, без ФС)."""

from __future__ import annotations

from main import _classify_pic_folder

_KEEP = {"a100", "a101"}  # об'єкти в черзі на постинг (status='new')
_KNOWN = {"a100", "a101", "a200", "a300"}  # усі артикули в БД


def test_keeps_new_article():
    """Папка артикула у статусі 'new' зберігається (None = не видаляти)."""
    assert _classify_pic_folder("A100", _KEEP, _KNOWN, all_pics=False) is None


def test_deletes_non_new_article():
    """Артикул є в БД, але не 'new' (posted/failed/skipped) → видалити як not-new."""
    assert _classify_pic_folder("A200", _KEEP, _KNOWN, all_pics=False) == "not-new"


def test_deletes_orphan_article():
    """Артикула немає в БД зовсім → осиротіла папка."""
    assert _classify_pic_folder("A999", _KEEP, _KNOWN, all_pics=False) == "orphan"


def test_all_pics_deletes_even_new():
    """--all зносить усе, навіть папки об'єктів у статусі 'new'."""
    assert _classify_pic_folder("A100", _KEEP, _KNOWN, all_pics=True) == "all"


def test_matching_is_case_insensitive():
    """Назва папки зіставляється з артикулами без огляду на регістр і пробіли."""
    assert _classify_pic_folder(" a100 ", _KEEP, _KNOWN, all_pics=False) is None
    assert _classify_pic_folder("A200", _KEEP, _KNOWN, all_pics=False) == "not-new"


# ── phase_clean_pics (тимчасова ФС + тимчасова БД) ──────────────────────────


def _seed(tmp_path, monkeypatch):
    """Папки A100(new), A200(posted), A999(orphan) + БД, перенаправлені у tmp."""
    import crm_data_parser.photo_downloader as pd
    import main
    import offer_db

    pics = tmp_path / "pics"
    for art in ("A100", "A200", "A999"):
        d = pics / art
        d.mkdir(parents=True)
        (d / "photo_000.jpg").write_bytes(b"x" * 1000)
    monkeypatch.setattr(pd, "PICS_DIR", pics)

    db_path = tmp_path / "offers.db"
    real_db = offer_db.OfferDB
    with real_db(db_path) as db:
        db.insert_offer(1, {}, article="A100", status="new")
        db.insert_offer(2, {}, article="A200", status="new")
        db.mark_posted(2, "r2")  # A200 → posted (фото вже на сайті)
    monkeypatch.setattr(offer_db, "OfferDB", lambda: real_db(db_path))
    return pics, main


def test_clean_pics_keeps_new_deletes_posted_and_orphan(tmp_path, monkeypatch):
    pics, main = _seed(tmp_path, monkeypatch)
    res = main.phase_clean_pics(all_pics=False, dry_run=False)
    assert (pics / "A100").exists()  # new — збережено
    assert not (pics / "A200").exists()  # posted — видалено
    assert not (pics / "A999").exists()  # orphan — видалено
    assert res["deleted"] == 2
    assert res["kept"] == 1
    assert res["orphans"] == 1
    assert res["freed_bytes"] > 0


def test_clean_pics_dry_run_deletes_nothing(tmp_path, monkeypatch):
    pics, main = _seed(tmp_path, monkeypatch)
    res = main.phase_clean_pics(all_pics=False, dry_run=True)
    assert (pics / "A100").exists()
    assert (pics / "A200").exists()  # dry-run — нічого не чіпаємо
    assert (pics / "A999").exists()
    assert res["deleted"] == 2  # «видалив би»


def test_clean_pics_all_deletes_everything(tmp_path, monkeypatch):
    pics, main = _seed(tmp_path, monkeypatch)
    res = main.phase_clean_pics(all_pics=True, dry_run=False)
    assert not (pics / "A100").exists()
    assert not (pics / "A200").exists()
    assert not (pics / "A999").exists()
    assert res["deleted"] == 3
    assert res["kept"] == 0
