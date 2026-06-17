"""Tests for MUI ToggleButtonGroup support (site replaced radio groups with buttons).

Covers both sides of the change:
  * schema collector detects the group as widget="radio" with options;
  * form filler clicks the correct toggle button.

Uses a real (headless) Chromium with static HTML via set_content — no live site.
"""

from __future__ import annotations

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from rieltor_handler.new_offer_poster.dict_filler import DictOfferFormFiller  # noqa: E402
from rieltor_handler.new_offer_poster.fields import FieldsMixin  # noqa: E402
from rieltor_handler.schema_collector._widget import _WidgetMixin  # noqa: E402


def test_match_toggle_index_exact_and_ci():
    labels = ["Є", "Немає"]
    assert FieldsMixin._match_toggle_index(labels, "Є") == 0
    assert FieldsMixin._match_toggle_index(labels, "немає") == 1


def test_match_toggle_index_numeric_rooms():
    # Site toggle options are "1".."6+"; CRM gives "5 кімнат"
    labels = ["1", "2", "3", "4", "5", "6+"]
    assert FieldsMixin._match_toggle_index(labels, "5 кімнат") == 4
    assert FieldsMixin._match_toggle_index(labels, "2") == 1


def test_match_toggle_index_numeric_ceiling():
    labels = ["1", "2", "3", "4", "5", "6+"]
    # 8 rooms exceeds max → ceiling "6+"
    assert FieldsMixin._match_toggle_index(labels, "8 кімнат") == 5


def test_match_toggle_index_missing():
    labels = ["Є", "Немає"]
    assert FieldsMixin._match_toggle_index(labels, "Підключення можливе") is None

# One toggle field (Газ) with three options, second option pre-selected.
TOGGLE_HTML = """
<div id="root">
  <div class="MuiFormControl-root MuiFormControl-fullWidth">
    <label class="MuiFormLabel-root">Газ</label>
    <div class="MuiToggleButtonGroup-root" role="group">
      <button class="MuiButtonBase-root MuiToggleButton-root" type="button" value="1" aria-pressed="false">
        <span class="MuiToggleButton-label">Є</span></button>
      <button class="MuiButtonBase-root MuiToggleButton-root Mui-selected" type="button" value="2" aria-pressed="true">
        <span class="MuiToggleButton-label">Немає</span></button>
      <button class="MuiButtonBase-root MuiToggleButton-root" type="button" value="3" aria-pressed="false">
        <span class="MuiToggleButton-label">Підключення можливе</span></button>
    </div>
  </div>
  <script>
    // Mimic MUI ToggleButtonGroup: clicking a button presses it, unpresses siblings.
    document.querySelectorAll('.MuiToggleButtonGroup-root button').forEach(function (b) {
      b.addEventListener('click', function () {
        b.parentElement.querySelectorAll('button').forEach(function (x) {
          x.setAttribute('aria-pressed', 'false');
          x.classList.remove('Mui-selected');
        });
        b.setAttribute('aria-pressed', 'true');
        b.classList.add('Mui-selected');
      });
    });
  </script>
</div>
"""


class _Widget(_WidgetMixin):
    def __init__(self, page):
        self.page = page
        self.ui_delay_ms = 0


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        pg = browser.new_page()
        yield pg
        browser.close()


def test_collector_detects_toggle_as_radio_with_options(page):
    page.set_content(TOGGLE_HTML)
    w = _Widget(page)
    form = page.locator("css=.MuiFormControl-root").first
    widget, options, meta = w._detect_widget_and_options_formcontrol(form)
    assert widget == "radio"
    assert options == ["Є", "Немає", "Підключення можливе"]
    assert meta.get("control") == "toggle_button_group"


def test_filler_clicks_matching_toggle_button(page):
    page.set_content(TOGGLE_HTML)
    filler = DictOfferFormFiller.__new__(DictOfferFormFiller)
    filler.page = page
    container = page.locator("css=.MuiFormControl-root").first

    handled = filler._try_fill_toggle_group(container, "Додаткові параметри", "Газ", "Підключення можливе")
    assert handled is True

    pressed = page.locator(
        "xpath=//button[contains(@class,'MuiToggleButton-root')]"
        "[.//span[normalize-space(.)='Підключення можливе']]"
    ).first
    assert pressed.get_attribute("aria-pressed") == "true"


def test_filler_skips_when_no_toggle_group(page):
    page.set_content("<div class='MuiFormControl-root'><input type='text'/></div>")
    filler = DictOfferFormFiller.__new__(DictOfferFormFiller)
    filler.page = page
    container = page.locator("css=.MuiFormControl-root").first
    # No toggle group present → returns False so caller can try other widget types.
    assert filler._try_fill_toggle_group(container, "sec", "Поле", "Є") is False


def test_filler_reports_handled_even_if_option_missing(page):
    page.set_content(TOGGLE_HTML)
    filler = DictOfferFormFiller.__new__(DictOfferFormFiller)
    filler.page = page
    container = page.locator("css=.MuiFormControl-root").first
    # Group exists but option absent → return True (don't fall through to text fill).
    assert filler._try_fill_toggle_group(container, "sec", "Газ", "Неіснуюча") is True


MULTI_HTML = """
<div id="root">
  <div class="MuiFormControl-root MuiFormControl-fullWidth">
    <label class="MuiFormLabel-root">У будинку є</label>
    <div class="MuiToggleButtonGroup-root" role="group">
      <button class="MuiButtonBase-root MuiToggleButton-root" type="button" aria-pressed="false">
        <span class="MuiToggleButton-label">Камін</span></button>
      <button class="MuiButtonBase-root MuiToggleButton-root" type="button" aria-pressed="false">
        <span class="MuiToggleButton-label">Кондиціонер</span></button>
      <button class="MuiButtonBase-root MuiToggleButton-root" type="button" aria-pressed="false">
        <span class="MuiToggleButton-label">Холодильник</span></button>
    </div>
  </div>
  <script>
    document.querySelectorAll('.MuiToggleButtonGroup-root button').forEach(function (b) {
      b.addEventListener('click', function () {
        // multi-select: toggle just this button (do NOT clear siblings)
        var on = b.getAttribute('aria-pressed') === 'true';
        b.setAttribute('aria-pressed', on ? 'false' : 'true');
      });
    });
  </script>
</div>
"""


def test_filler_multiselect_clicks_each_value(page):
    page.set_content(MULTI_HTML)
    filler = DictOfferFormFiller.__new__(DictOfferFormFiller)
    filler.page = page
    container = page.locator("css=.MuiFormControl-root").first

    handled = filler._try_fill_toggle_group(container, "Про будинок", "У будинку є", ["Камін", "Холодильник"])
    assert handled is True

    def pressed(name):
        return (
            page.locator(
                f"xpath=//button[contains(@class,'MuiToggleButton-root')][.//span[normalize-space(.)='{name}']]"
            ).first.get_attribute("aria-pressed")
            == "true"
        )

    assert pressed("Камін")
    assert pressed("Холодильник")
    assert not pressed("Кондиціонер")  # not requested → stays off
