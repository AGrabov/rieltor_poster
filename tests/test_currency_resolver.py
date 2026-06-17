"""Tests for currency value resolution.

The site migrated currency labels from words ("доларів") to symbols ("$")
inconsistently across property types, while the CRM always emits words. The
resolver maps the offer_data value to whatever the current schema offers.
"""

from __future__ import annotations

from rieltor_handler.new_offer_poster.dict_filler import DictOfferFormFiller


def _filler(options):
    f = DictOfferFormFiller.__new__(DictOfferFormFiller)
    f._schema = {"label_to_field": {"валюта": {"options": options}}}
    return f


SYMBOLS = ["грн", "$", "€", "грн/м²", "$ /м²", "€/м²"]
WORDS = ["гривень", "доларів", "євро", "грн. / м²", "$ /м²", "€/м²"]
PLOT = ["грн", "$", "€", "грн/сот.", "$/сот.", "€/сот."]


def test_resolves_word_to_symbol():
    f = _filler(SYMBOLS)
    assert f._resolve_currency_option("доларів") == "$"
    assert f._resolve_currency_option("гривень") == "грн"
    assert f._resolve_currency_option("євро") == "€"


def test_keeps_word_when_schema_uses_words():
    f = _filler(WORDS)
    assert f._resolve_currency_option("доларів") == "доларів"
    assert f._resolve_currency_option("євро") == "євро"


def test_prefers_total_price_over_per_unit():
    f = _filler(PLOT)
    # Must pick "$", not "$/сот."
    assert f._resolve_currency_option("доларів") == "$"


def test_unknown_value_passthrough():
    f = _filler(SYMBOLS)
    assert f._resolve_currency_option("щось") == "щось"


def test_no_options_passthrough():
    f = _filler([])
    assert f._resolve_currency_option("доларів") == "доларів"
