"""Tests for CRM-Тип → Призначення mapping in HTMLOfferParser.

Commercial cards have no "Призначення" field in the CRM, so purpose used to be
guessed from description keywords (unreliable). The CRM "Тип" (Офіс/Склад/…) is
structured data and a far better source — it should win.
"""

from __future__ import annotations

from crm_data_parser.html_parser import HTMLOfferParser

_PURPOSE_OPTS = [
    "Банківське приміщення",
    "Офісне приміщення",
    "Приміщення для надання послуг",
    "Склад",
    "Виробниче приміщення",
    "Приміщення вільного призначення",
    "Торгівельне приміщення",
]


def _parser(property_type="Комерційна"):
    p = HTMLOfferParser.__new__(HTMLOfferParser)
    p.property_type = property_type
    p.label_to_field = {"призначення": {"label": "Призначення", "options": _PURPOSE_OPTS}}
    return p


def test_office_type_maps_to_office_purpose():
    assert _parser()._purpose_from_crm_type("Офіс") == "Офісне приміщення"


def test_warehouse_type_maps_to_warehouse():
    assert _parser()._purpose_from_crm_type("Склад") == "Склад"


def test_shop_type_maps_to_trade():
    assert _parser()._purpose_from_crm_type("Торговельне") == "Торгівельне приміщення"


def test_unknown_type_returns_none():
    assert _parser()._purpose_from_crm_type("Офіс-склад невідомо") is None


def test_non_commercial_returns_none():
    # Apartments etc. have their own purpose semantics — don't apply this map.
    assert _parser("Квартира")._purpose_from_crm_type("Офіс") is None
