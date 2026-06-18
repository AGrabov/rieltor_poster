"""Tests for schema-driven conditional-field controllers.

A field like "Тип опалення" (Комерційна) is only rendered when its controller
"Опалення" is toggled to "Є". The form filler must set that controller first;
otherwise the dependent select never appears and the label match lands on a
neighbouring control (the bug that filled "Тип опалення" from a transport select).
"""

from __future__ import annotations

from rieltor_handler.new_offer_poster.dict_filler import conditional_controllers
from schemas import load_offer_schema


def test_conditional_controllers_extracts_heating_controller():
    schema = load_offer_schema("Оренда", "Комерційна")
    field = schema["label_to_field"]["тип опалення"]
    # Two duplicate visible_when probes in the schema → deduped to one pair.
    assert conditional_controllers(field) == [("Опалення", "Є")]


def test_conditional_controllers_empty_when_no_visible_when():
    assert conditional_controllers({"label": "Ціна"}) == []
    assert conditional_controllers({}) == []
    assert conditional_controllers({"meta": {}}) == []


def test_conditional_controllers_skips_blank_label_or_value():
    field = {
        "meta": {
            "visible_when": [
                {"controller": {"label": ""}, "value": "Є"},
                {"controller": {"label": "Опалення"}, "value": ""},
                {"controller": {"label": "Газ"}, "value": "Є"},
            ]
        }
    }
    assert conditional_controllers(field) == [("Газ", "Є")]
