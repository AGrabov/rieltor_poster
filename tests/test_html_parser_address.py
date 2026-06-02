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


def test_extract_address_strips_russian_street_type():
    address = _make_parser()._extract_address()
    assert address["Вулиця"] == "Лесі Українки"
