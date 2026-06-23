"""Tests for parse-time address normalization in HTMLOfferParser._extract_address."""

from __future__ import annotations

from bs4 import BeautifulSoup

from crm_data_parser.html_parser import HTMLOfferParser

_HTML = """
<table class="detail-view"><tbody>
  <tr><th>Місто</th><td>Киев</td></tr>
  <tr><th>Вулиця</th><td>ул. Лесі Українки</td></tr>
  <tr><th>Будинок</th><td>19</td></tr>
</tbody></table>
"""


def _make_parser():
    """Build a parser instance without running __init__ (which needs schema)."""
    parser = HTMLOfferParser.__new__(HTMLOfferParser)
    parser.soup = BeautifulSoup(_HTML, "html.parser")
    # Map each CRM <th> label straight to a schema field with the same label.
    parser._look_up_field_by_html_label = lambda label: {"label": label}
    return parser


def test_extract_address_normalizes_russian_city():
    address = _make_parser()._extract_address()
    assert address["Місто"] == "Київ"


def test_extract_address_keeps_street_type():
    # Street type is preserved at parse time — needed to pick the right option on
    # the site (шосе/проспект/площа); the autocomplete strips it only for search.
    address = _make_parser()._extract_address()
    assert address["Вулиця"] == "ул. Лесі Українки"


# ── _recover_address_from_description ──────────────────────────────────────
# When the structured CRM address is incomplete, fill missing Район / Вулиця /
# Будинок from the free-text description (which often carries the fuller address).
def _bare_parser():
    return HTMLOfferParser.__new__(HTMLOfferParser)


def test_recover_address_fills_missing_district_and_street():
    # Real case A30171: address has only Місто; description carries район + street.
    address = {"Місто": "Кодаки"}
    text = "Київська область, Васильківський район, с. Кодаки, вул. Набережна Пропонується до продажу"
    _bare_parser()._recover_address_from_description(address, text)
    assert address["Район"] == "Васильківський"
    assert address["Вулиця"] == "вул. Набережна"


def test_recover_address_fills_missing_house_from_description():
    # Real case A30300: street is set, house number sits in the description.
    address = {"Місто": "Старі Петрівці", "Вулиця": "вул. Юрківська"}
    text = "земельна ділянка у с. Старі Петрівці, вул. Юрківська, 14А. Ділянка має"
    _bare_parser()._recover_address_from_description(address, text)
    assert address["Будинок"] == "14А"


def test_recover_address_skips_district_for_big_city():
    # Real case A31765: Київ district is a масив (never the admin raion). Do NOT
    # take "Голосіївський район" from text — but DO recover the house number.
    address = {"Місто": "Київ", "Вулиця": "вул. Велика Васильківська"}
    text = "Офіс у Голосіївському районі, БЦ Велика Васильківська 62/64 — у центрі"
    _bare_parser()._recover_address_from_description(address, text)
    assert "Район" not in address
    assert address["Будинок"] == "62/64"


def test_recover_address_does_not_overwrite_existing_fields():
    address = {"Місто": "Кодаки", "Район": "Васильківський", "Вулиця": "вул. Набережна"}
    text = "Обухівський район, вул. Інша, 5"
    _bare_parser()._recover_address_from_description(address, text)
    assert address["Район"] == "Васильківський"
    assert address["Вулиця"] == "вул. Набережна"


# ── explicit "Адреса:" line overrides structured address (B) ────────────────
# An explicit "Адреса: …" line in the description is the marketing-authoritative
# address and overrides a conflicting structured CRM value.
def test_explicit_address_overrides_conflicting_house():
    # Real case A31251: CRM house "16" is wrong; description states 14Б.
    address = {"Місто": "Київ", "Вулиця": "Воскресенська", "Будинок": "16"}
    text = "КОМЕРЦІЙНЕ ПРИМІЩЕННЯ Адреса: м. Київ, вул. Воскресенська, 14Б Площа: 61 м²"
    _bare_parser()._recover_address_from_description(address, text)
    assert address["Будинок"] == "14Б"


def test_explicit_address_keeps_house_when_same():
    address = {"Місто": "Київ", "Вулиця": "Воскресенська", "Будинок": "14Б"}
    text = "Адреса: м. Київ, вул. Воскресенська, 14Б"
    _bare_parser()._recover_address_from_description(address, text)
    assert address["Будинок"] == "14Б"


def test_explicit_address_overrides_different_street():
    address = {"Місто": "Київ", "Вулиця": "Стара", "Будинок": "1"}
    text = "Адреса: м. Київ, вул. Воскресенська, 14Б"
    _bare_parser()._recover_address_from_description(address, text)
    assert address["Вулиця"] == "вул. Воскресенська"
    assert address["Будинок"] == "14Б"


def test_no_explicit_label_does_not_overwrite_existing_house():
    # Without an "Адреса:" label, a different house in prose must NOT override.
    address = {"Місто": "Київ", "Вулиця": "вул. Набережна", "Будинок": "5"}
    text = "поруч вул. Набережна, 99 інша будівля"
    _bare_parser()._recover_address_from_description(address, text)
    assert address["Будинок"] == "5"
