"""Unit tests for the generic save-error recovery (clear non-required text fields).

The generic fallback is the final ``else`` branch of ``_attempt_error_recovery``:
for an error on a field that is not required (per JSON schema) and not in the
address section, it clears the value if (and only if) the control is a plain text
field. The existing save flow already calls this and re-saves once, so a
persisting error simply means the offer is dropped.

Tests bypass __init__ and stub the Playwright-touching helpers.
"""

from __future__ import annotations

from rieltor_handler.new_offer_poster.dict_filler import DictOfferFormFiller


class _FakeCtrl:
    def __init__(self, tag: str = "input", role: str = "") -> None:
        self._tag = tag
        self._role = role
        self.filled: str | None = None
        self.clicked = False

    def count(self) -> int:
        return 1

    def evaluate(self, _js: str) -> str:
        return self._tag

    def get_attribute(self, name: str) -> str | None:
        return self._role if name == "role" else None

    def click(self) -> None:
        self.clicked = True

    def fill(self, value: str) -> None:
        self.filled = value


def _filler_with_ctrl(ctrl: _FakeCtrl) -> DictOfferFormFiller:
    f = DictOfferFormFiller.__new__(DictOfferFormFiller)
    f._last_offer_data = {}
    f._schema = {
        "label_to_field": {
            "опис": {"label": "Опис", "required": False},
            "площа": {"label": "Площа", "required": True},
        }
    }
    f._section = lambda root, sec: object()
    f._find_control_by_label = lambda section, label: ctrl
    return f


def test_clears_nonrequired_text_field():
    ctrl = _FakeCtrl(tag="input", role="")
    f = _filler_with_ctrl(ctrl)
    errors = [{"section": "Інформація про об'єкт", "field": "Опис", "message": "невірний формат"}]
    assert f._attempt_error_recovery(object(), errors) is True
    assert ctrl.clicked is True
    assert ctrl.filled == ""


def test_skips_required_field_per_schema():
    ctrl = _FakeCtrl(tag="input")
    f = _filler_with_ctrl(ctrl)
    errors = [{"section": "Цінові параметри", "field": "Площа *", "message": "невірне значення"}]
    assert f._attempt_error_recovery(object(), errors) is False
    assert ctrl.filled is None


def test_skips_address_section():
    ctrl = _FakeCtrl(tag="input")
    f = _filler_with_ctrl(ctrl)
    errors = [{"section": "Адреса об'єкта", "field": "Будинок", "message": "щось не так"}]
    assert f._attempt_error_recovery(object(), errors) is False
    assert ctrl.filled is None


def test_skips_select_widget():
    # MUI Select renders the control as a <div>, not a text input.
    ctrl = _FakeCtrl(tag="div")
    f = _filler_with_ctrl(ctrl)
    errors = [{"section": "Інформація про об'єкт", "field": "Опис", "message": "невірно"}]
    assert f._attempt_error_recovery(object(), errors) is False
    assert ctrl.filled is None


def test_skips_autocomplete_combobox():
    ctrl = _FakeCtrl(tag="input", role="combobox")
    f = _filler_with_ctrl(ctrl)
    errors = [{"section": "Інформація про об'єкт", "field": "Опис", "message": "невірно"}]
    assert f._attempt_error_recovery(object(), errors) is False
    assert ctrl.filled is None


def test_required_detected_via_asterisk_when_absent_from_schema():
    ctrl = _FakeCtrl(tag="input")
    f = _filler_with_ctrl(ctrl)
    errors = [{"section": "Інша секція", "field": "Невідоме поле *", "message": "невірно"}]
    assert f._attempt_error_recovery(object(), errors) is False
    assert ctrl.filled is None
