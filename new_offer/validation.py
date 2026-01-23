from __future__ import annotations

from typing import List

from playwright.sync_api import Locator

from setup_logger import setup_logger

logger = setup_logger(__name__)


class RequiredFieldError(RuntimeError):
    pass


class FormValidationError(RuntimeError):
    """Raised when the form contains validation errors after save/validation."""

    def __init__(self, errors: List[dict]):
        self.errors = errors
        msg = "Form validation errors: " + "; ".join(
            [f"{e.get('section','')} | {e.get('field','')}: {e.get('message','')}" for e in errors]
        )
        super().__init__(msg)


class ValidationMixin:
    # -------- touched tracking --------
    def _mark_touched(self, ctrl: Locator) -> None:
        try:
            ctrl.evaluate("el => el.setAttribute('data-rieltor-touched','1')")
        except Exception:
            pass

    # -------- required detection --------
    def _is_required_control(self, ctrl: Locator) -> bool:
        # direct required attribute
        try:
            if ctrl.get_attribute("required") is not None:
                return True
        except Exception:
            pass

        # class marker
        try:
            cls = ctrl.get_attribute("class") or ""
            if "Mui-required" in cls or "-required" in cls:
                return True
        except Exception:
            pass

        # label markers: asterisk / Mui-required
        try:
            form = ctrl.locator("xpath=ancestor::div[contains(@class,'MuiFormControl-root')][1]").first
            lbl = form.locator("css=label").first
            if lbl.count():
                lbl_cls = lbl.get_attribute("class") or ""
                if "Mui-required" in lbl_cls:
                    return True
                if lbl.locator("css=span.MuiFormLabel-asterisk").count():
                    return True
                if "*" in ((lbl.inner_text() or "")):
                    return True
        except Exception:
            pass

        return False


    def _filled_value_text(self, ctrl: Locator) -> str:
        """Return non-empty string if control looks filled, else ''."""
        # 1) MUI Select button
        try:
            cls = ctrl.get_attribute("class") or ""
            role = ctrl.get_attribute("role") or ""
            if "MuiSelect-select" in cls or (role == "button" and "MuiSelect-select" in cls):
                t = (ctrl.inner_text() or "").strip()
                return t
        except Exception:
            pass

        # 2) input/textarea value
        try:
            tag = ctrl.evaluate("el => el.tagName.toLowerCase()")
        except Exception:
            tag = ""

        if tag in ("input", "textarea"):
            try:
                v = (ctrl.input_value() or "").strip()
                return v
            except Exception:
                return ""

        # 3) if it's a wrapper containing input
        try:
            inner = ctrl.locator("css=input, textarea").first
            if inner.count():
                v = (inner.input_value() or "").strip()
                if v:
                    return v
        except Exception:
            pass

        # 4) radio group: any checked radio inside
        try:
            if ctrl.locator("css=input[type='radio']:checked").count():
                return "checked"
        except Exception:
            pass

        # 5) autocomplete / chips / rendered value
        # if you have _control_has_value in AutocompleteMixin, use it if present
        try:
            fn = getattr(self, "_control_has_value", None)
            if callable(fn) and fn(ctrl):
                # best-effort: try input value too
                try:
                    inner = ctrl.locator("css=input").first
                    if inner.count():
                        return (inner.input_value() or "").strip() or "selected"
                except Exception:
                    return "selected"
                return "selected"
        except Exception:
            pass

        return ""



    def _assert_required_filled(self, root: Locator) -> None:
        touched = root.locator("[data-rieltor-touched='1']")
        n = touched.count()
        errors = []

        for i in range(n):
            ctrl = touched.nth(i)
            if not self._is_required_control(ctrl):
                continue

            filled = self._filled_value_text(ctrl)
            if not filled:
                label_txt = ""
                try:
                    form = ctrl.locator("xpath=ancestor::div[contains(@class,'MuiFormControl-root')][1]").first
                    label = form.locator("css=label").first
                    if label.count():
                        label_txt = (label.inner_text() or "").strip()
                except Exception:
                    pass
                errors.append(label_txt or "<unknown required field>")

        if errors:
            logger.error("Required fields not filled: %s", errors)
            raise RequiredFieldError("Не заполнены обязательные поля: " + ", ".join(errors))


    # -------- report from page after save --------
    def collect_validation_report(self, root: Locator) -> List[dict]:
        """Collect MUI validation errors into a structured report."""
        errors: List[dict] = []

        # 1) Helper-text errors
        helper_errors = root.locator(".MuiFormHelperText-root.Mui-error")
        for i in range(helper_errors.count()):
            helper = helper_errors.nth(i)
            msg = (helper.inner_text() or "").strip()
            if not msg:
                continue

            field_label = ""
            section_name = ""

            try:
                form = helper.locator("xpath=ancestor::div[contains(@class,'MuiFormControl-root')][1]").first
                lbl = form.locator("css=label").first
                if lbl.count():
                    field_label = (lbl.inner_text() or "").strip()
            except Exception:
                pass

            try:
                sec = helper.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][.//h6][1]").first
                h6 = sec.locator("css=h6").first
                if h6.count():
                    section_name = (h6.inner_text() or "").strip()
            except Exception:
                pass

            errors.append({"section": section_name, "field": field_label, "message": msg})

        # 2) aria-invalid without helper
        invalid_inputs = root.locator("input[aria-invalid='true'], textarea[aria-invalid='true']")
        for i in range(invalid_inputs.count()):
            inp = invalid_inputs.nth(i)

            # skip if already has helper error (to avoid dup)
            try:
                form = inp.locator("xpath=ancestor::div[contains(@class,'MuiFormControl-root')][1]").first
                if form.locator(".MuiFormHelperText-root.Mui-error").count():
                    continue
            except Exception:
                pass

            field_label = ""
            section_name = ""

            try:
                form = inp.locator("xpath=ancestor::div[contains(@class,'MuiFormControl-root')][1]").first
                lbl = form.locator("css=label").first
                if lbl.count():
                    field_label = (lbl.inner_text() or "").strip()
            except Exception:
                pass

            try:
                sec = inp.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][.//h6][1]").first
                h6 = sec.locator("css=h6").first
                if h6.count():
                    section_name = (h6.inner_text() or "").strip()
            except Exception:
                pass

            errors.append({"section": section_name, "field": field_label, "message": "invalid"})

        # de-duplicate
        uniq: List[dict] = []
        seen = set()
        for e in errors:
            k = (e.get("section", ""), e.get("field", ""), e.get("message", ""))
            if k in seen:
                continue
            seen.add(k)
            uniq.append(e)

        return uniq
