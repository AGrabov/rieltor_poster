"""Тест OfferDB.get_posted на тимчасовій БД."""

from __future__ import annotations

from offer_db import OfferDB


def test_get_posted_returns_only_posted_with_rieltor_id(tmp_path):
    db_path = tmp_path / "offers.db"
    with OfferDB(db_path) as db:
        db.insert_offer(estate_id=1, offer_data={}, status="new")
        db.insert_offer(estate_id=2, offer_data={}, status="posted")
        db.mark_posted(2, "555")
        # posted без rieltor_offer_id — має виключатися
        db.insert_offer(estate_id=3, offer_data={}, status="posted")

        posted = db.get_posted()
        result = [(r.estate_id, r.rieltor_offer_id) for r in posted]
        assert result == [(2, "555")]


def test_get_posted_respects_max_count(tmp_path):
    db_path = tmp_path / "offers.db"
    with OfferDB(db_path) as db:
        for eid in (10, 11, 12):
            db.insert_offer(estate_id=eid, offer_data={}, status="posted")
            db.mark_posted(eid, str(eid))
        posted = db.get_posted(max_count=2)
        assert [r.estate_id for r in posted] == [10, 11]
