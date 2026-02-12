from __future__ import annotations

from setup_logger import setup_logger
logger = setup_logger(__name__)

from playwright.sync_api import Locator

from .helpers import _norm, _cf


class _LabelMixin:
    # ---------------- labels / required ----------------
    def _nearest_h6_title(self, node: Locator) -> str:
        try:
            sec = node.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][.//h6][1]").first
            h6 = sec.locator("css=h6").first
            if h6.count():
                return _norm(h6.inner_text() or "")
        except Exception:
            pass
        return ""

    def _is_helper_text(self, t: str) -> bool:
        t = _cf(t)
        return (
            "положення мітки" in t
            or "мітки на карті" in t
            or "змінити положення" in t
            or "перетяг" in t
        )

    def _radiogroup_title_from_rg(self, rg: Locator) -> str:
        # 1) label.MuiFormLabel-root near radiogroup
        try:
            wrap = rg.locator("xpath=ancestor::div[contains(@class,'MuiFormControl-root')][1]").first
            if wrap.count():
                lab = wrap.locator("css=label.MuiFormLabel-root").first
                if lab.count():
                    t = _norm(lab.inner_text() or "").replace("*", "").strip()
                    if t and not self._is_helper_text(t):
                        return t
        except Exception:
            pass

        # 2) <p> title (legacy)
        try:
            box = rg.locator(
                "xpath=ancestor::div[contains(@class,'MuiBox-root')][.//p[normalize-space()] and .//*[@role='radiogroup']][1]"
            ).first
            if box.count():
                p = box.locator("xpath=.//p[normalize-space()][1]").first
                if p.count():
                    t = _norm(p.inner_text() or "")
                    if t and not self._is_helper_text(t):
                        return t
        except Exception:
            pass

        # 3) fallback: nearest h6
        try:
            t = _norm(self._nearest_h6_title(rg) or "")
            if t and not self._is_helper_text(t):
                return t
        except Exception:
            pass

        return ""

    def _is_required(self, form: Locator) -> bool:
        try:
            lbl = form.locator("css=label").first
            if lbl.count():
                if lbl.locator("css=span.MuiFormLabel-asterisk").count():
                    return True
                if "*" in (lbl.inner_text() or ""):
                    return True
        except Exception:
            pass
        try:
            return bool(form.locator("css=input[required], textarea[required]").count())
        except Exception:
            return False

    def _label_text_formcontrol(self, form: Locator) -> str:
        rg = form.locator("css=[role='radiogroup']").first
        if rg.count():
            t = self._radiogroup_title_from_rg(rg)
            if t:
                return t

        try:
            lab = form.locator("css=label.MuiInputLabel-root, label.MuiFormLabel-root").first
            if lab.count():
                t = _norm(lab.inner_text() or "").replace("*", "").strip()
                if t:
                    return t
        except Exception:
            pass

        try:
            labs = form.locator("css=label")
            for i in range(labs.count()):
                lab = labs.nth(i)
                cls = (lab.get_attribute("class") or "")
                if "MuiFormControlLabel-root" in cls:
                    continue
                t = _norm(lab.inner_text() or "").replace("*", "").strip()
                if t:
                    return t
        except Exception:
            pass

        return ""

    def _label_text_labelcontrol(self, label_el: Locator) -> str:
        try:
            t = _norm(label_el.locator("css=span.MuiFormControlLabel-label").inner_text() or "")
            if t:
                return t
        except Exception:
            pass
        try:
            return _norm(label_el.inner_text() or "")
        except Exception:
            return ""

    def _fallback_label_from_context(self, node: Locator) -> str:
        sec = self._nearest_h6_title(node)
        if sec:
            return sec
        return "field"
