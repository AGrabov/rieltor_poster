from __future__ import annotations

from typing import Any, Dict, Set

from setup_logger import setup_logger
logger = setup_logger(__name__)

from playwright.sync_api import Locator

from .helpers import (_cf, _norm, _key4)


class _SmokeFillMixin:
    # ---------------- SMOKE FILL (DISCOVERY) ----------------
    def _set_radio_in_form(self, form: Locator) -> bool:
        rg = form.locator("css=[role='radiogroup']").first
        if not rg.count():
            return False
        values = self._radio_options(rg)
        if not values:
            return False
        # Skip parking type selector radio (Гараж/Паркомісце) - handled by select_parking_type
        values_cf = {_cf(v) for v in values}
        if _cf("Гараж") in values_cf and _cf("Паркомісце") in values_cf:
            return False
        pref = self._preferred_radio_value(values)
        labs = rg.locator("xpath=.//label[.//input[@type='radio']]")
        for i in range(labs.count()):
            l = labs.nth(i)
            try:
                t = _norm(l.locator("css=span.MuiFormControlLabel-label").inner_text() or "")
            except Exception:
                t = ""
            if _cf(t) == _cf(pref):
                ok = self._click_best_effort(l.locator("css=span.MuiFormControlLabel-label").first) or self._click_best_effort(l)
                self.page.wait_for_timeout(self.ui_delay_ms + 250)
                return ok
        return False

    def smoke_fill_visible_fields(self, *, skip_nav_titles: Set[str] | None = None) -> int:
        """Fill only safe widgets to trigger dynamic fields: text/textarea/select/radio.
        Skips checkboxes & file uploads.
        """
        skip_nav_titles = skip_nav_titles or set()
        root = self._root()
        self.open_all_blocks_sticky()

        forms = root.locator("xpath=.//div[contains(@class,'MuiFormControl-root') or contains(@class,'MuiTextField-root')]")
        actions = 0

        for i in range(forms.count()):
            f = forms.nth(i)
            try:
                if not f.is_visible():
                    continue
            except Exception:
                pass

            nav = self._nearest_h6_title(f) or ""
            if nav and any(_cf(nav) == _cf(s) for s in skip_nav_titles):
                continue

            label = _norm(self._label_text_formcontrol(f) or "")
            if label and self._is_helper_text(label):
                continue

            widget, _, _ = self._detect_widget_and_options_formcontrol(f)

            if widget == "button":
                try:
                    bt = _norm(f.locator("css=button").first.inner_text() or "")
                except Exception:
                    bt = ""
                if bt and self._is_action_button_text(bt):
                    continue

            if widget == "select":
                if self._select_pick_first(f):
                    actions += 1
                    self.page.wait_for_timeout(self.ui_delay_ms + 250)
                continue

            if widget == "radio":
                if self._set_radio_in_form(f):
                    actions += 1
                    self.page.wait_for_timeout(self.ui_delay_ms + 350)
                continue

            if widget in ("text", "multiline_text"):
                inp = f.locator("css=input").first if widget == "text" else f.locator("css=textarea").first
                if not inp.count():
                    continue
                try:
                    cur = (inp.input_value() or "").strip()
                except Exception:
                    cur = ""
                if cur:
                    continue
                try:
                    inp.click(timeout=1500)
                except Exception:
                    try:
                        inp.click(force=True, timeout=1500)
                    except Exception:
                        continue
                self.page.wait_for_timeout(self.ui_delay_ms)
                try:
                    inp.fill("1")
                    actions += 1
                except Exception:
                    pass
                self.page.wait_for_timeout(self.ui_delay_ms + 250)
                continue

            if widget in ("text_autocomplete", "autocomplete_multi"):
                # For autocomplete fields during discovery, try to trigger options with a short query
                inp = f.locator("css=input").first
                if not inp.count():
                    continue
                try:
                    cur = (inp.input_value() or "").strip()
                except Exception:
                    cur = ""
                if cur:
                    continue
                try:
                    inp.click(timeout=1500)
                except Exception:
                    try:
                        inp.click(force=True, timeout=1500)
                    except Exception:
                        continue
                self.page.wait_for_timeout(self.ui_delay_ms)
                # Type a minimal query to trigger autocomplete dropdown
                try:
                    inp.fill("")
                    inp.type("а", delay=35)  # Common Cyrillic letter
                    self.page.wait_for_timeout(self.ui_delay_ms + 300)
                    # Try to pick first option if any appear
                    debug_label = f"smoke_fill({label})"
                    visible = self._wait_autocomplete_options(inp, timeout_s=2.0, debug_label=debug_label)
                    if visible > 0:
                        click_ok, clicked_text = self._click_autocomplete_option_contains(inp, "", allow_first_fallback=True, debug_label=debug_label)
                        if click_ok:
                            actions += 1
                            logger.debug("Smoke filled autocomplete: %s with '%s'", label, clicked_text)
                    else:
                        # No options appeared, just clear the field
                        inp.fill("")
                except Exception as e:
                    logger.debug("Smoke fill autocomplete failed for %s: %s", label, e)
                self.page.wait_for_timeout(self.ui_delay_ms + 250)
                continue

        logger.info("Smoke fill actions=%d", actions)
        return actions

    def discover_schema_until_stable(
        self,
        *,
        seed_address_city: str = "Київ",
        max_rounds: int = 3,
        smoke_fill: bool = True,
    ) -> Dict[str, Any]:
        """Seed address -> collect -> (optional smoke fill -> collect) until schema stops growing."""
        prev_keys: Set[str] = set()
        last_schema: Dict[str, Any] = {}

        for r in range(1, max_rounds + 1):
            logger.info("Discovery round %d/%d", r, max_rounds)

            try:
                self.seed_fill_address(seed_address_city)
            except Exception as e:
                logger.warning("Seed fill address failed: %s", e)

            self.open_all_blocks_sticky()
            schema = self.collect_schema_dynamic_h6()
            last_schema = schema

            keys = set(
                _key4(f.get("nav", ""), f.get("section", ""), f.get("label", ""), f.get("widget", ""))
                for f in (schema.get("fields") or [])
            )
            logger.info("Schema keys=%d (prev=%d, +%d)", len(keys), len(prev_keys), max(0, len(keys) - len(prev_keys)))

            if keys.issubset(prev_keys) and prev_keys:
                logger.info("Schema stable; stop discovery")
                break

            prev_keys = keys

            if smoke_fill:
                try:
                    self.smoke_fill_visible_fields(skip_nav_titles={"Адреса об'єкта"})
                except Exception as e:
                    logger.warning("Smoke fill failed: %s", e)

            self.page.wait_for_timeout(self.ui_delay_ms + 500)

        return last_schema
