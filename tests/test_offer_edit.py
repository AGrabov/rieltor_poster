"""Тести чистого помічника злиття ручних правок offer_data (offer_edit)."""

from __future__ import annotations

import json

import pytest

from offer_edit import merge_offer_edits


def test_address_form_field_overrides_raw_json():
    raw = json.dumps({"property_type": "Ділянка", "address": {"Місто": "Плюти", "Район": ""}})
    merged = merge_offer_edits(raw, {"Район": "Обухівський"})
    assert merged["address"]["Район"] == "Обухівський"
    # Поле поза формою лишається з raw JSON.
    assert merged["address"]["Місто"] == "Плюти"
    assert merged["property_type"] == "Ділянка"


def test_non_form_address_keys_from_raw_json_preserved():
    # Ключі адреси, яких немає у формі (Новобудова), беруться лише з raw JSON.
    raw = json.dumps({"address": {"Новобудова": "ЖК Панорама", "Вулиця": "стара"}})
    merged = merge_offer_edits(raw, {"Вулиця": "Хрещатик"})
    assert merged["address"]["Новобудова"] == "ЖК Панорама"
    assert merged["address"]["Вулиця"] == "Хрещатик"


def test_creates_address_dict_when_missing():
    raw = json.dumps({"property_type": "Будинок"})
    merged = merge_offer_edits(raw, {"Місто": "Київ"})
    assert merged["address"]["Місто"] == "Київ"


def test_empty_form_field_clears_value():
    # Порожнє поле форми = свідоме очищення хибного значення.
    raw = json.dumps({"address": {"Район": "Хибний"}})
    merged = merge_offer_edits(raw, {"Район": ""})
    assert merged["address"]["Район"] == ""


def test_invalid_json_raises_value_error():
    with pytest.raises(ValueError):
        merge_offer_edits("{not valid json", {"Місто": "Київ"})


def test_non_object_json_raises_value_error():
    # offer_data має бути об'єктом (dict), а не списком/числом.
    with pytest.raises(ValueError):
        merge_offer_edits("[1, 2, 3]", {})
