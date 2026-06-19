"""Тести: рядковий дефолт Поверховості та очищення поля Будинок без номера.

Обходять __init__ DictOfferFormFiller і підмінюють хелпери, що працюють з
Playwright (як test_save_error_recovery).
"""

from __future__ import annotations

from rieltor_handler.new_offer_poster.dict_filler import DictOfferFormFiller


# ── Q1: дефолт Поверховості — рядок, не int ───────────────────────────────
def test_storeys_default_is_string():
    f = DictOfferFormFiller.__new__(DictOfferFormFiller)
    f.property_type = "Квартира"
    f._schema = {"label_to_field": {"поверховість": {"label": "Поверховість"}}}
    offer: dict = {}
    f._apply_required_defaults(offer)
    assert offer["Поверховість"] == "2"
    assert isinstance(offer["Поверховість"], str)


def test_storeys_cap_is_string():
    # Понад ліміт 50 → скидаємо до "2" (рядок).
    f = DictOfferFormFiller.__new__(DictOfferFormFiller)
    f.property_type = "Квартира"
    f._schema = {"label_to_field": {"поверховість": {"label": "Поверховість"}}}
    offer = {"Поверховість": "99"}
    f._apply_required_defaults(offer)
    assert offer["Поверховість"] == "2"
    assert isinstance(offer["Поверховість"], str)


# ── Q2: очищення поля Будинок, коли номера немає ──────────────────────────
class _FakeInput:
    def __init__(self, value: str = "") -> None:
        self._value = value
        self.filled: str | None = None
        self.clicked = False

    def count(self) -> int:
        return 1

    def input_value(self) -> str:
        return self._value

    def click(self) -> None:
        self.clicked = True

    def fill(self, v: str) -> None:
        self.filled = v
        self._value = v

    def press(self, _key: str) -> None:
        pass


class _FakeLoc:
    def __init__(self, first: _FakeInput) -> None:
        self.first = first


class _FakeCtrl:
    def __init__(self, inp: _FakeInput) -> None:
        self._inp = inp

    def locator(self, _sel: str) -> _FakeLoc:
        return _FakeLoc(self._inp)


def _house_filler(inp: _FakeInput) -> DictOfferFormFiller:
    f = DictOfferFormFiller.__new__(DictOfferFormFiller)
    f._find_control_by_label = lambda sec, label: _FakeCtrl(inp)
    f._mark_touched = lambda x: None
    return f


def test_clear_house_field_clears_site_autofilled_junk():
    # Сайт автозаповнив у поле нечисловий фрагмент — прибираємо його.
    inp = _FakeInput("Бажова")
    f = _house_filler(inp)
    f._clear_house_field(object())
    assert inp.filled == ""


def test_clear_house_field_noop_when_already_empty():
    inp = _FakeInput("")
    f = _house_filler(inp)
    f._clear_house_field(object())
    assert inp.filled is None
