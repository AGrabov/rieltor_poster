"""Тести відновлення Району на публікації за кадастровим номером.

Перевіряють гілку Району в ``_attempt_error_recovery`` та помічник
``_lookup_district_by_cadnum``. Як і test_save_error_recovery, обходять __init__
і підмінюють хелпери, що працюють з Playwright.
"""

from __future__ import annotations

import crm_data_parser.cadastral_lookup as cl
from rieltor_handler.new_offer_poster.dict_filler import DictOfferFormFiller


def _make_filler(offer_data: dict, district_from_cadnum: str | None = None) -> DictOfferFormFiller:
    f = DictOfferFormFiller.__new__(DictOfferFormFiller)
    f._last_offer_data = offer_data
    f._schema = {"label_to_field": {}}
    f._section = lambda root, sec: object()
    f._lookup_district_by_cadnum = lambda cadnum: district_from_cadnum
    f._find_control_by_label = lambda section, label: None  # немає контролів → пропуск re-fill
    f._control_has_value = lambda ctrl: True
    f._refill_house_if_cleared = lambda sec, house: None
    f._fill_calls = []
    f._fill_autocomplete = lambda section, key, value, *a, **k: f._fill_calls.append((key, value))
    return f


_NEED_SELECT = {"section": "Адреса об'єкта", "field": "Район *", "message": "Необхідно вибрати елемент зі списку"}
_MAP_OTHER = {
    "section": "Адреса об'єкта",
    "field": "",
    "message": "Точка на карті знаходиться в іншому районі ніж той, що вказано",
}


def test_district_recovery_uses_cadnum_value_on_empty_error():
    offer = {"address": {"Кадастровий номер": "3223151000:04:016:0013", "Район": "Старий"}}
    f = _make_filler(offer, district_from_cadnum="Обухівський")
    assert f._attempt_error_recovery(object(), [_NEED_SELECT]) is True
    assert ("Район", "Обухівський") in f._fill_calls
    # використане значення зберігається назад у дані об'єкта
    assert offer["address"]["Район"] == "Обухівський"


def test_district_recovery_handles_map_other_district_error():
    offer = {"address": {"Кадастровий номер": "3223151000:04:016:0013"}}
    f = _make_filler(offer, district_from_cadnum="Обухівський")
    assert f._attempt_error_recovery(object(), [_MAP_OTHER]) is True
    assert ("Район", "Обухівський") in f._fill_calls


def test_district_recovery_falls_back_to_offer_data_without_cadnum():
    offer = {"address": {"Район": "Голосіївський"}}  # без кадастру
    f = _make_filler(offer, district_from_cadnum=None)
    assert f._attempt_error_recovery(object(), [_NEED_SELECT]) is True
    assert ("Район", "Голосіївський") in f._fill_calls


def test_district_recovery_returns_false_without_any_district():
    offer = {"address": {}}
    f = _make_filler(offer, district_from_cadnum=None)
    assert f._attempt_error_recovery(object(), [_NEED_SELECT]) is False
    assert f._fill_calls == []


def test_lookup_district_by_cadnum_parses_registry(monkeypatch):
    monkeypatch.setattr(cl, "lookup_address_by_cadnum", lambda c: "Київська обл., Обухівський р-н, с. Плюти")
    f = DictOfferFormFiller.__new__(DictOfferFormFiller)
    assert f._lookup_district_by_cadnum("3223151000:04:016:0013") == "Обухівський"


def test_lookup_district_by_cadnum_empty_returns_none():
    f = DictOfferFormFiller.__new__(DictOfferFormFiller)
    assert f._lookup_district_by_cadnum("") is None
