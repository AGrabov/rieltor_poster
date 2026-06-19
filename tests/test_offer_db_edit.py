"""Тести ручного редагування об'єктів у БД: get_offer / edit_offer."""

from __future__ import annotations

from offer_db import OfferDB


def test_get_offer_returns_record_or_none(tmp_path):
    db_path = tmp_path / "offers.db"
    with OfferDB(db_path) as db:
        db.insert_offer(estate_id=1, offer_data={"property_type": "Будинок"}, article="A1")
        rec = db.get_offer(1)
        assert rec is not None
        assert rec.estate_id == 1
        assert rec.article == "A1"
        assert db.get_offer(999) is None


def test_edit_offer_updates_data_and_requeues(tmp_path):
    db_path = tmp_path / "offers.db"
    with OfferDB(db_path) as db:
        db.insert_offer(
            estate_id=5,
            offer_data={"property_type": "Ділянка", "offer_type": "Продаж", "address": {"Район": ""}},
            property_type="Ділянка",
            deal_type="Продаж",
            title="стара",
            status="failed",
        )
        db.mark_failed(5, ["щось зламалось"])

        new_data = {
            "property_type": "Ділянка",
            "offer_type": "Продаж",
            "address": {"Район": "Обухівський"},
        }
        db.edit_offer(5, offer_data=new_data, title="нова")

        rec = db.get_offer(5)
        assert rec.status == "new"  # завжди повертаємо у чергу
        assert rec.errors is None  # помилки очищено
        assert rec.title == "нова"
        assert rec.offer_data["address"]["Район"] == "Обухівський"


def test_edit_offer_rederives_type_columns_from_offer_data(tmp_path):
    # Колонки property_type/deal_type мають слідувати за offer_data (джерело істини
    # для публікації), щоб фільтри/відображення лишались узгодженими.
    db_path = tmp_path / "offers.db"
    with OfferDB(db_path) as db:
        db.insert_offer(
            estate_id=7,
            offer_data={"property_type": "Квартира", "offer_type": "Продаж"},
            property_type="Квартира",
            deal_type="Продаж",
            status="failed",
        )
        db.edit_offer(
            7,
            offer_data={"property_type": "Будинок", "offer_type": "Оренда"},
            title="x",
        )
        rec = db.get_offer(7)
        assert rec.property_type == "Будинок"
        assert rec.deal_type == "Оренда"
