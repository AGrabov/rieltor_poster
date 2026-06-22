"""Тести БД-сервісу ремонту failed-об'єктів (repair.py)."""

from __future__ import annotations

import repair
from offer_db import OfferDB


# ── classify_errors: категоризація помилок валідації ──────────────────────
def test_classify_raion():
    errs = [{"section": "Адреса об'єкта", "field": "Район *", "message": "Необхідно вибрати елемент зі списку"}]
    assert repair.classify_errors(errs) == {"raion"}


def test_classify_cadastral():
    errs = [{"section": "Адреса об'єкта", "field": "Кадастровий номер *", "message": "Необхідно заповнити поле"}]
    assert repair.classify_errors(errs) == {"cadastral"}


def test_classify_house():
    errs = [{"section": "Адреса об'єкта", "field": "Будинок *", "message": "Має починатись з цифри"}]
    assert repair.classify_errors(errs) == {"house"}


def test_classify_other_exception():
    assert repair.classify_errors([{"error": "unexpected error"}]) == {"other"}


def test_classify_mixed_raion_and_house():
    errs = [
        {"field": "Район *", "message": "Необхідно вибрати"},
        {"field": "Будинок *", "message": "Має починатись з цифри"},
    ]
    assert repair.classify_errors(errs) == {"raion", "house"}


def test_classify_empty():
    assert repair.classify_errors(None) == set()
    assert repair.classify_errors([]) == set()


# ── repair_failed_offers: помічники ───────────────────────────────────────
def _insert_failed(db, estate_id, offer_data, errors, property_type=None):
    db.insert_offer(estate_id=estate_id, offer_data=offer_data, status="failed", property_type=property_type)
    db.mark_failed(estate_id, errors)


# ── repair_failed_offers: поведінка ───────────────────────────────────────
def test_repair_requeues_when_raion_fixed(tmp_path, monkeypatch):
    # Комерційна з мікрорайоном у Районі: збагачення підставляє адмінрайон → у чергу.
    monkeypatch.setattr(
        repair,
        "enrich_offer_data_with_cadastral",
        lambda od: od.setdefault("address", {}).update({"Район": "Голосіївський"}) or True,
    )
    db_path = tmp_path / "offers.db"
    with OfferDB(db_path) as db:
        _insert_failed(
            db,
            1,
            {"property_type": "Комерційна", "address": {"Місто": "Київ", "Вулиця": "вул. Деміївська", "Будинок": "12", "Район": "Корчувате"}},
            [{"field": "Район *", "message": "Необхідно вибрати елемент зі списку"}],
            property_type="Комерційна",
        )
    stats = repair.repair_failed_offers(db_path=db_path)
    assert stats["requeued"] == 1
    assert stats["raion_fixed"] == 1
    with OfferDB(db_path) as db:
        rec = db.get_offer(1)
        assert rec.status == "new"
        assert rec.errors is None
        assert rec.offer_data["address"]["Район"] == "Голосіївський"


def test_repair_attempts_house_error_and_requeues_when_raion_changes(tmp_path, monkeypatch):
    # «Будинок має починатись з цифри» — наслідок невибраного Району; виправлення
    # Району повертає об'єкт у чергу (каскад адреси відпрацює наступного разу).
    monkeypatch.setattr(
        repair,
        "enrich_offer_data_with_cadastral",
        lambda od: od.setdefault("address", {}).update({"Район": "Дарницький"}) or True,
    )
    db_path = tmp_path / "offers.db"
    with OfferDB(db_path) as db:
        _insert_failed(
            db,
            2,
            {"property_type": "Комерційна", "address": {"Місто": "Київ", "Вулиця": "вул. Садова", "Будинок": "48", "Район": "Осокорки"}},
            [{"field": "Будинок *", "message": "Має починатись з цифри"}],
            property_type="Комерційна",
        )
    stats = repair.repair_failed_offers(db_path=db_path)
    assert stats["requeued"] == 1
    with OfferDB(db_path) as db:
        assert db.get_offer(2).status == "new"


def test_repair_keeps_failed_when_no_improvement(tmp_path, monkeypatch):
    # Збагачення нічого не змінило (район той самий, кадастру нема) → лишається failed.
    monkeypatch.setattr(repair, "enrich_offer_data_with_cadastral", lambda od: False)
    db_path = tmp_path / "offers.db"
    with OfferDB(db_path) as db:
        _insert_failed(
            db,
            3,
            {"property_type": "Комерційна", "address": {"Місто": "Київ", "Вулиця": "вул. Межова", "Будинок": "1", "Район": "Центр"}},
            [{"field": "Район *", "message": "Необхідно вибрати"}],
            property_type="Комерційна",
        )
    stats = repair.repair_failed_offers(db_path=db_path)
    assert stats["requeued"] == 0
    assert stats["still_failed"] == 1
    with OfferDB(db_path) as db:
        assert db.get_offer(3).status == "failed"


def test_repair_requeues_when_cadastral_filled(tmp_path, monkeypatch):
    monkeypatch.setattr(
        repair,
        "enrich_offer_data_with_cadastral",
        lambda od: od.setdefault("address", {}).update({"Кадастровий номер": "3223151000:04:016:0013"}) or True,
    )
    db_path = tmp_path / "offers.db"
    with OfferDB(db_path) as db:
        _insert_failed(
            db,
            4,
            {"property_type": "Ділянка", "address": {"Місто": "Плюти", "Вулиця": "вул. Лісова", "Будинок": "3"}},
            [{"field": "Кадастровий номер *", "message": "Необхідно заповнити поле"}],
            property_type="Ділянка",
        )
    stats = repair.repair_failed_offers(db_path=db_path)
    assert stats["requeued"] == 1
    assert stats["cadastral_fixed"] == 1
    with OfferDB(db_path) as db:
        assert db.get_offer(4).status == "new"


def test_repair_skips_non_address_errors(tmp_path, monkeypatch):
    # Не адресна помилка (виняток) → район/кадастр не допоможуть, збагачення НЕ викликається.
    def boom(od):
        raise AssertionError("збагачення не має викликатись для не-адресних помилок")

    monkeypatch.setattr(repair, "enrich_offer_data_with_cadastral", boom)
    db_path = tmp_path / "offers.db"
    with OfferDB(db_path) as db:
        _insert_failed(db, 5, {"property_type": "Квартира"}, [{"error": "unexpected error"}], property_type="Квартира")
    stats = repair.repair_failed_offers(db_path=db_path)
    assert stats["skipped"] == 1
    assert stats["requeued"] == 0
    with OfferDB(db_path) as db:
        assert db.get_offer(5).status == "failed"
