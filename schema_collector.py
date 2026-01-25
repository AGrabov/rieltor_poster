from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from playwright.sync_api import Locator, Page

from rieltor_session import RieltorCredentials, RieltorSession


def _norm(s: str) -> str:
    return " ".join((s or "").replace("\xa0", " ").split()).strip()


def _cf(s: str) -> str:
    return _norm(s).casefold()


def _xpath_literal(s: str) -> str:
    s = "" if s is None else str(s)
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    parts = s.split("'")
    return "concat(" + ", ".join(
        [f"'{p}'" if i == len(parts) - 1 else f"'{p}', \"'\"" for i, p in enumerate(parts)]
    ) + ")"


@dataclass
class FieldInfo:
    nav: str
    section: str
    label: str
    widget: str
    required: bool
    options: List[str]
    meta: Dict[str, Any]


class OfferCreateSchemaCollector:
    CREATE_URL = "https://my.rieltor.ua/offers/create"

    # do not dump "Тип угоди"; everything else (incl. Extra params) must be collected
    _NAV_EXCLUDE = {"Тип угоди"}

    def __init__(
        self,
        page: Page,
        *,
        ui_delay_ms: int = 350,
        radio_follow_window: int = 4,
        enable_radio_probe: bool = True,
    ) -> None:
        self.page = page
        self.ui_delay_ms = int(ui_delay_ms)
        self.radio_follow_window = int(radio_follow_window)
        self.enable_radio_probe = bool(enable_radio_probe)

        self._epoch = 0

    # ---------------- open / root ----------------
    def open(self) -> None:
        self.page.goto(self.CREATE_URL, wait_until="domcontentloaded")
        self._wait_ready()

    def _wait_ready(self) -> None:
        self.page.locator(
            "xpath=//h5[normalize-space(.)='Нове оголошення' or .//*[normalize-space(.)='Нове оголошення']]"
        ).first.wait_for(state="visible", timeout=30_000)
        try:
            self.page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass

    def _offer_container(self) -> Locator:
        h5 = self.page.locator(
            "xpath=//h5[normalize-space(.)='Нове оголошення' or .//*[normalize-space(.)='Нове оголошення']]"
        ).first
        if h5.count():
            return h5.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][1]").first

        h6 = self.page.locator("h6", has_text="Тип угоди").first
        if h6.count():
            return h6.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][4]").first

        return self.page.locator("css=body")

    def _root(self) -> Locator:
        return self._offer_container()

    # ---------------- helpers ----------------
    def _scroll_to(self, y: int) -> None:
        try:
            self.page.evaluate("(yy)=>window.scrollTo(0, yy)", y)
        except Exception:
            pass

    def _scroll_to_top(self) -> None:
        self._scroll_to(0)

    def _scroll_to_bottom(self) -> None:
        try:
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass

    def _click_best_effort(self, el: Locator, *, timeout: int = 2500) -> bool:
        # do not click while listbox/dialog is open
        try:
            if self.page.locator("css=[role='listbox']:visible,[role='dialog']:visible").count():
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
                self.page.wait_for_timeout(self.ui_delay_ms)
        except Exception:
            pass

        try:
            el.scroll_into_view_if_needed(timeout=timeout)
        except Exception:
            pass
        try:
            el.click(timeout=timeout)
            return True
        except Exception:
            try:
                el.click(force=True, timeout=timeout)
                return True
            except Exception:
                return False

    def _is_action_button_text(self, t: str) -> bool:
        t = _cf(t)
        return ("зберегти" in t) or ("опублікувати" in t)

    # ---------------- collapse detection ----------------
    def _toggle_button_for_h6(self, h6: Locator) -> Locator:
        return h6.locator("xpath=ancestor::button[1]").first

    def _collapse_container_for_toggle_button(self, btn: Locator) -> Locator:
        # search following-sibling collapse on several wrapper levels
        for lvl in range(1, 11):
            wrap = btn.locator(f"xpath=ancestor::div[contains(@class,'MuiBox-root')][{lvl}]").first
            if not wrap.count():
                break
            sib = wrap.locator("xpath=following-sibling::div[contains(@class,'MuiCollapse-container')][1]").first
            if sib.count():
                return sib

        # parent sibling fallback
        parent = btn.locator("xpath=parent::*").first
        sib = parent.locator("xpath=following-sibling::div[contains(@class,'MuiCollapse-container')][1]").first
        if sib.count():
            return sib

        # nested fallback
        return btn.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][1]//div[contains(@class,'MuiCollapse-container')][1]").first

    def _collapse_container_for_h6(self, h6: Locator) -> Locator:
        btn = self._toggle_button_for_h6(h6)
        if not btn.count():
            return self.page.locator("css=__none__")
        return self._collapse_container_for_toggle_button(btn)

    def _is_collapse_entered(self, collapse: Locator) -> bool:
        try:
            if not collapse.count():
                return False
            cls = collapse.get_attribute("class") or ""
            return "MuiCollapse-entered" in cls
        except Exception:
            return False

    def _wait_collapse_entered(self, collapse: Locator, timeout_ms: int = 9000) -> None:
        if not collapse.count():
            self.page.wait_for_timeout(self.ui_delay_ms)
            return
        try:
            eh = collapse.element_handle()
            if eh:
                self.page.wait_for_function(
                    """
                    (el) => {
                      const cls = el.className || '';
                      if (cls.includes('MuiCollapse-entered')) return true;
                      const r = el.getBoundingClientRect();
                      return !!r && r.height > 1;
                    }
                    """,
                    eh,
                    timeout=timeout_ms,
                )
        except Exception:
            self.page.wait_for_timeout(self.ui_delay_ms)

    def _is_toggle_open(self, h6: Locator) -> bool:
        # IMPORTANT: trust only collapse container, NOT aria-expanded (it can be stale/incorrect)
        col = self._collapse_container_for_h6(h6)
        return bool(col.count() and self._is_collapse_entered(col))

    def _open_toggle_if_closed(self, h6: Locator) -> bool:
        col = self._collapse_container_for_h6(h6)
        if col.count() and self._is_collapse_entered(col):
            return False

        btn = self._toggle_button_for_h6(h6)
        clicked = False
        if btn.count():
            clicked = self._click_best_effort(btn)
        if not clicked:
            clicked = self._click_best_effort(h6)

        if clicked:
            self.page.wait_for_timeout(self.ui_delay_ms)
            if col.count():
                self._wait_collapse_entered(col)
        return clicked

    # ---------------- expand all blocks (open-only) ----------------
    def expand_all_collapsibles(self, scope: Locator | None = None, *, max_rounds: int = 12) -> None:
        """Open all accordion-like blocks under scope.

        Robust selector: ANY button containing an h6 (svg is optional).
        Open-only: click only if collapse container is not entered.
        """
        scope = scope or self._root()

        for _ in range(max_rounds):
            changed = 0

            # Important: collect in DOM order
            h6s = scope.locator("xpath=.//button[.//h6]//h6")
            for i in range(h6s.count()):
                h6 = h6s.nth(i)
                title = _norm(h6.inner_text() or "")
                if not title:
                    continue
                if title in self._NAV_EXCLUDE:
                    continue
                if self._is_action_button_text(title):
                    continue

                # Only treat as collapsible if we can find a collapse container near it
                col = self._collapse_container_for_h6(h6)
                if not col.count():
                    continue

                if self._is_collapse_entered(col):
                    continue

                if self._open_toggle_if_closed(h6):
                    changed += 1

            if changed == 0:
                break

    def open_all_blocks_sticky(self) -> None:
        """Open everything once (top+bottom), helps UI keep blocks open across type switches."""
        root = self._root()
        self._scroll_to_bottom()
        self.page.wait_for_timeout(self.ui_delay_ms + 250)
        self.expand_all_collapsibles(root, max_rounds=18)
        self._scroll_to_top()
        self.page.wait_for_timeout(self.ui_delay_ms + 250)
        self.expand_all_collapsibles(root, max_rounds=18)

    # ---------------- property type ----------------
    def select_property_type(self, ui_text: str) -> None:
        root = self._root()

        # ensure blocks are opened BEFORE switch
        self.open_all_blocks_sticky()

        sec = root.locator(
            "xpath=.//h6[normalize-space(.)='Тип нерухомості']/ancestor::div[contains(@class,'MuiBox-root')][2]"
        ).first
        sec.wait_for(state="visible", timeout=15_000)

        target = _cf(ui_text)
        cards = sec.locator(
            "xpath=.//div[contains(@class,'MuiBox-root')][.//img[@alt] and .//span and not(.//div[contains(@class,'MuiBox-root')][.//img[@alt] and .//span])]"
        )

        chosen: Optional[Locator] = None
        for i in range(cards.count()):
            c = cards.nth(i)
            alt = _cf(c.locator("css=img[alt]").first.get_attribute("alt") or "")
            spans = _cf(" ".join(_norm(t) for t in c.locator("css=span").all_inner_texts() if _norm(t)))
            if (alt and target in alt) or (spans and target in spans):
                chosen = c
                break
        if not chosen:
            raise RuntimeError(f"Property type card not found: {ui_text}")

        inner = chosen.locator("xpath=.//span[normalize-space()]" ).first
        self._click_best_effort(inner if inner.count() else chosen)

        self._wait_ready()
        try:
            self.page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        self.page.wait_for_timeout(self.ui_delay_ms + 450)

        self._epoch += 1

        # after render, open everything again (open-only)
        self.open_all_blocks_sticky()

    # ---------------- navigation items (h6) ----------------
    def list_navigation_items(self) -> List[Tuple[str, int]]:
        """Collect all h6 under offer container, in DOM order."""
        root = self._root()
        self.open_all_blocks_sticky()

        h6s = root.locator("css=h6")
        seen: Dict[str, int] = {}
        out: List[Tuple[str, int]] = []

        for i in range(h6s.count()):
            h = h6s.nth(i)
            try:
                if h.locator("xpath=ancestor::*[@role='dialog' or @role='listbox'][1]").count():
                    continue
            except Exception:
                pass

            title = _norm(h.inner_text() or "")
            if not title:
                continue
            if title in self._NAV_EXCLUDE:
                continue
            if self._is_action_button_text(title):
                continue

            k = _cf(title)
            occ = seen.get(k, 0)
            seen[k] = occ + 1
            out.append((title, occ))

        # remove adjacent duplicates
        compact: List[Tuple[str, int]] = []
        prev: Optional[str] = None
        for t, occ in out:
            if prev is not None and _cf(prev) == _cf(t):
                continue
            compact.append((t, occ))
            prev = t
        return compact

    def _h6_by_title_occ(self, title: str, occ: int) -> Optional[Locator]:
        root = self._root()
        h6s = root.locator("css=h6", has_text=title)
        if h6s.count() <= occ:
            return None
        return h6s.nth(occ)

    def _scope_for_nav_item(self, title: str, occ: int) -> Optional[Locator]:
        h6 = self._h6_by_title_occ(title, occ)
        if not h6 or not h6.count():
            return None

        try:
            h6.scroll_into_view_if_needed(timeout=1500)
        except Exception:
            pass

        # If h6 is a toggle header -> scope is its collapse container
        if h6.locator("xpath=ancestor::button[1]").count():
            col = self._collapse_container_for_h6(h6)
            if col.count() and (not self._is_collapse_entered(col)):
                self._open_toggle_if_closed(h6)
                self.page.wait_for_timeout(self.ui_delay_ms)

            if col.count():
                self.expand_all_collapsibles(col, max_rounds=10)
                self.page.wait_for_timeout(self.ui_delay_ms)
                return col

        # else: scope is a wider section container
        box = h6.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][2]").first
        if not box.count():
            box = h6.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][1]").first
        if not box.count():
            return None

        self.expand_all_collapsibles(box, max_rounds=10)
        self.page.wait_for_timeout(self.ui_delay_ms)
        return box

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

    def _radiogroup_title_from_p(self, node: Locator) -> str:
        """DEPRECATED: kept for backward compatibility."""
        try:
            box = node.locator(
                "xpath=ancestor::div[contains(@class,'MuiBox-root')][.//p and .//*[@role='radiogroup']][1]"
            ).first
            p = box.locator("css=p").first
            if p.count():
                t = _norm(p.inner_text() or "")
                if t:
                    return t
        except Exception:
            pass
        return ""

    def _radiogroup_title_from_rg(self, rg: Locator) -> str:
        """Get radiogroup title.

        Priority:
          1) MUI group label: label.MuiFormLabel-root (e.g. 'Загальний стан')
          2) <p> title above radiogroup (some blocks use <p>)
        """
        # 1) label.MuiFormLabel-root near radiogroup
        try:
            wrap = rg.locator("xpath=ancestor::div[contains(@class,'MuiFormControl-root')][1]").first
            if wrap.count():
                lab = wrap.locator("css=label.MuiFormLabel-root").first
                if lab.count():
                    t = _norm(lab.inner_text() or "").replace("*", "").strip()
                    if t:
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
                    if t:
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
        # Radiogroups: prefer group label, NOT option labels
        rg = form.locator("css=[role='radiogroup']").first
        if rg.count():
            t = self._radiogroup_title_from_rg(rg)
            if t:
                return t
            # last fallback: try any label that is not an option label
            try:
                lab = form.locator("xpath=.//label[contains(@class,'MuiFormLabel-root')][1]").first
                if lab.count():
                    t = _norm(lab.inner_text() or "").replace("*", "").strip()
                    if t:
                        return t
            except Exception:
                pass

        # Standard inputs: prefer InputLabel/FormLabel; avoid grabbing option labels
        try:
            lab = form.locator("css=label.MuiInputLabel-root, label.MuiFormLabel-root").first
            if lab.count():
                t = _norm(lab.inner_text() or "").replace("*", "").strip()
                if t:
                    return t
        except Exception:
            pass

        # fallback to first label, but skip option labels
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
        t = self._radiogroup_title_from_p(node)
        if t:
            return t
        return "field"

    # ---------------- widgets/options ----------------
    def _open_listbox(self, select_btn: Locator, menu_id: str | None) -> Optional[Locator]:
        for _ in range(2):
            self._click_best_effort(select_btn)
            self.page.wait_for_timeout(self.ui_delay_ms)

            if menu_id:
                try:
                    lit = _xpath_literal(menu_id)
                    lb = self.page.locator(f"xpath=//div[@id={lit}]//*[@role='listbox']").first
                    if lb.count():
                        lb.wait_for(state="visible", timeout=5000)
                        return lb
                except Exception:
                    pass

            lb = self.page.locator("css=[role='listbox']:visible").last
            if lb.count():
                try:
                    lb.wait_for(state="visible", timeout=5000)
                    return lb
                except Exception:
                    pass
        return None

    def _list_listbox_options(self, listbox: Locator) -> List[str]:
        out: List[str] = []
        opts = listbox.locator("[role='option']")
        for i in range(opts.count()):
            o = opts.nth(i)
            try:
                t = _norm(o.inner_text() or "")
            except Exception:
                continue
            if t:
                out.append(t)

        seen: Set[str] = set()
        uniq: List[str] = []
        for t in out:
            k = t.casefold()
            if k in seen:
                continue
            seen.add(k)
            uniq.append(t)
        return uniq

    def _radio_options(self, rg: Locator) -> List[str]:
        out: List[str] = []
        labels = rg.locator("xpath=.//label[.//input[@type='radio']]")
        for i in range(labels.count()):
            lbl = labels.nth(i)
            try:
                t = _norm(lbl.locator("css=span.MuiFormControlLabel-label").inner_text() or "")
            except Exception:
                t = ""
            if not t:
                try:
                    t = _norm(lbl.inner_text() or "")
                except Exception:
                    t = ""
            if t:
                out.append(t)

        seen: Set[str] = set()
        uniq: List[str] = []
        for t in out:
            k = t.casefold()
            if k in seen:
                continue
            seen.add(k)
            uniq.append(t)
        return uniq

    def _detect_widget_and_options_formcontrol(self, form: Locator) -> Tuple[str, List[str], Dict[str, Any]]:
        meta: Dict[str, Any] = {}

        rg = form.locator("css=[role='radiogroup']").first
        if rg.count():
            return "radio", self._radio_options(rg), meta

        if form.locator("css=input[type='checkbox']").count():
            return "checkbox", [], meta

        select_btn = form.locator("css=div.MuiSelect-select[role='button']").first
        if select_btn.count():
            menu_id = None
            try:
                menu_id = select_btn.get_attribute("aria-controls")
            except Exception:
                pass
            lb = self._open_listbox(select_btn, menu_id)
            if not lb:
                return "select", [], meta
            opts = self._list_listbox_options(lb)
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
            self.page.wait_for_timeout(self.ui_delay_ms)
            return "select", opts, meta

        if form.locator("css=.MuiAutocomplete-root").count():
            if form.locator("css=.MuiChip-root, .MuiChip-label").count():
                return "autocomplete_multi", [], meta
            return "text_autocomplete", [], meta

        if form.locator("css=textarea").count():
            return "multiline_text", [], meta

        if form.locator("css=input:not([type='radio']):not([type='checkbox']):not([type='file'])").count():
            inp = form.locator("css=input").first
            try:
                meta["input_type"] = inp.get_attribute("type") or "text"
            except Exception:
                pass
            return "text", [], meta

        if form.locator("css=input[type='file']").count():
            inp = form.locator("css=input[type='file']").first
            try:
                meta["accept"] = inp.get_attribute("accept") or ""
            except Exception:
                meta["accept"] = ""
            try:
                meta["multiple"] = bool(inp.get_attribute("multiple"))
            except Exception:
                meta["multiple"] = False
            return "file_upload", [], meta

        btn = form.locator("css=button").first
        if btn.count():
            return "button", [], meta

        return "unknown", [], meta

    # ---------------- field collection ----------------
    def _collect_forms_in_scope(self, scope: Locator) -> List[Locator]:
        roots = scope.locator(
            "xpath=.//div[contains(@class,'MuiFormControl-root') or contains(@class,'MuiTextField-root')]"
        )
        return [roots.nth(i) for i in range(roots.count())]

    def _collect_label_controls_in_scope(self, scope: Locator, nav_title: str) -> List[FieldInfo]:
        out: List[FieldInfo] = []
        labels = scope.locator(
            "xpath=.//label[contains(@class,'MuiFormControlLabel-root')][.//input[@type='checkbox' or @type='radio']]"
        )
        for i in range(labels.count()):
            lab = labels.nth(i)
            if lab.locator("xpath=ancestor::*[@role='radiogroup'][1]").count():
                continue
            txt = self._label_text_labelcontrol(lab)
            if not txt:
                continue

            inp = lab.locator("css=input").first
            itype = ""
            try:
                itype = (inp.get_attribute("type") or "").casefold()
            except Exception:
                itype = ""

            widget = "checkbox" if itype == "checkbox" else "radio"
            out.append(
                FieldInfo(
                    nav=nav_title,
                    section=self._nearest_h6_title(lab) or nav_title,
                    label=txt,
                    widget=widget,
                    required=False,
                    options=[],
                    meta={"standalone": True},
                )
            )
        return out

    def _collect_fields_in_scope(self, scope: Locator, nav_title: str) -> List[FieldInfo]:
        out: List[FieldInfo] = []

        for form in self._collect_forms_in_scope(scope):
            section = self._nearest_h6_title(form) or nav_title
            required = self._is_required(form)
            widget, options, meta = self._detect_widget_and_options_formcontrol(form)

            # exclude action buttons from dump
            if widget == "button":
                try:
                    bt = _norm(form.locator("css=button").first.inner_text() or "")
                except Exception:
                    bt = ""
                if bt and self._is_action_button_text(bt):
                    continue

            label = self._label_text_formcontrol(form)
            if not label:
                try:
                    inp = form.locator("css=input, textarea").first
                    if inp.count():
                        label = _norm(inp.get_attribute("aria-label") or "") or _norm(inp.get_attribute("placeholder") or "")
                except Exception:
                    pass

            if not label:
                has_any = (
                    form.locator("css=div.MuiSelect-select[role='button']").count()
                    or form.locator("css=input").count()
                    or form.locator("css=textarea").count()
                    or form.locator("css=[role='radiogroup']").count()
                )
                if not has_any:
                    continue
                label = self._fallback_label_from_context(form)

            out.append(
                FieldInfo(
                    nav=nav_title,
                    section=section,
                    label=label,
                    widget=widget,
                    required=required,
                    options=options,
                    meta=meta,
                )
            )

        out.extend(self._collect_label_controls_in_scope(scope, nav_title))

        files = scope.locator("css=input[type='file']")
        for i in range(files.count()):
            fi = files.nth(i)
            accept = ""
            multiple = False
            try:
                accept = fi.get_attribute("accept") or ""
            except Exception:
                pass
            try:
                multiple = bool(fi.get_attribute("multiple"))
            except Exception:
                multiple = False

            label = "Завантажити файл"
            try:
                wrap = fi.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][1]").first
                b = wrap.locator("css=button").first
                if b.count():
                    bt = _norm(b.inner_text() or "")
                    if bt:
                        label = bt
            except Exception:
                pass

            out.append(
                FieldInfo(
                    nav=nav_title,
                    section=self._nearest_h6_title(fi) or nav_title,
                    label=label,
                    widget="file_upload",
                    required=False,
                    options=[],
                    meta={"accept": accept, "multiple": multiple},
                )
            )

        return out

    # ---------------- schema dump ----------------
    def collect_schema_dynamic_h6(self) -> Dict[str, Any]:
        root = self._root()
        self.open_all_blocks_sticky()

        nav_items = self.list_navigation_items()

        all_fields: List[FieldInfo] = []
        for title, occ in nav_items:
            if title in self._NAV_EXCLUDE:
                continue
            scope = self._scope_for_nav_item(title, occ)
            if scope is None:
                continue
            self.page.wait_for_timeout(self.ui_delay_ms)
            all_fields.extend(self._collect_fields_in_scope(scope, title))

        uniq: List[FieldInfo] = []
        seen: Set[Tuple[str, str, str, str]] = set()
        for f in all_fields:
            k = (f.nav.casefold(), f.section.casefold(), f.label.casefold(), f.widget.casefold())
            if k in seen:
                continue
            seen.add(k)
            uniq.append(f)

        return {
            "navigation": [t for (t, _) in nav_items if t not in self._NAV_EXCLUDE],
            "fields": [
                {
                    "nav": f.nav,
                    "section": f.section,
                    "label": f.label,
                    "widget": f.widget,
                    "required": f.required,
                    "options": f.options,
                    "meta": f.meta,
                }
                for f in uniq
            ],
        }

    # ---------------- RADIO probe (windowed) ----------------
    def _set_radio_option(self, option_label: Locator) -> bool:
        inp = option_label.locator("css=input[type='radio']").first
        if not inp.count():
            return False

        clicked = False
        try:
            txt = option_label.locator("css=span.MuiFormControlLabel-label").first
            if txt.count():
                clicked = self._click_best_effort(txt)
        except Exception:
            clicked = False

        if not clicked:
            clicked = self._click_best_effort(option_label)

        if not clicked:
            try:
                inp.check(force=True)
            except Exception:
                pass

        self.page.wait_for_timeout(self.ui_delay_ms + 250)

        try:
            return inp.is_checked()
        except Exception:
            return True

    def _radio_group_name(self, option_labels: Locator) -> str:
        try:
            inp = option_labels.nth(0).locator("css=input[type='radio']").first
            return _norm(inp.get_attribute("name") or "")
        except Exception:
            return ""

    def _collect_field_nodes_in_scope(self, scope: Locator) -> List[Locator]:
        """Collect *all* field-like nodes in DOM order.

        Includes:
          - MUI FormControl/TextField roots
          - standalone FormControlLabel (checkbox/radio not inside radiogroup)
          - file inputs

        NOTE: we intentionally do NOT include radio option labels inside radiogroups.
        """
        nodes = scope.locator(
            "xpath=(.//div[contains(@class,'MuiFormControl-root') or contains(@class,'MuiTextField-root')]"
            " | .//label[contains(@class,'MuiFormControlLabel-root')][.//input[@type='checkbox' or @type='radio']]"
            " | .//input[@type='file'])"
        )
        out: List[Locator] = []
        for i in range(nodes.count()):
            n = nodes.nth(i)

            # Skip radio options inside radiogroup
            try:
                if n.evaluate("(el)=>el.tagName==='LABEL'"):
                    if n.locator("xpath=ancestor::*[@role='radiogroup'][1]").count():
                        continue
            except Exception:
                pass

            out.append(n)

        return out

    def _node_has_radio_name(self, node: Locator, radio_name: str) -> bool:
        if not radio_name:
            return False
        try:
            lit = _xpath_literal(radio_name)
            return bool(node.locator(f"xpath=.//input[@type='radio' and @name={lit}]").count())
        except Exception:
            return False

    def _sig_node(self, node: Locator, title_fallback: str) -> Tuple[str, str, str]:
        """Signature for a field-like node (section, label, widget)."""
        # File inputs
        try:
            if node.evaluate("(el)=>el.tagName==='INPUT' && el.type==='file'"):
                sec = self._nearest_h6_title(node) or title_fallback
                label = "Завантажити файл"
                try:
                    wrap = node.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][1]").first
                    b = wrap.locator("css=button").first
                    if b.count():
                        bt = _norm(b.inner_text() or "")
                        if bt:
                            label = bt
                except Exception:
                    pass
                return (sec.casefold(), label.casefold(), "file_upload")
        except Exception:
            pass

        # Standalone checkbox/radio labels
        try:
            if node.evaluate("(el)=>el.tagName==='LABEL'"):
                sec = self._nearest_h6_title(node) or title_fallback
                label = self._label_text_labelcontrol(node) or self._fallback_label_from_context(node)
                itype = ""
                try:
                    itype = (node.locator("css=input").first.get_attribute("type") or "").casefold()
                except Exception:
                    itype = ""
                widget = "checkbox" if itype == "checkbox" else "radio"
                return (sec.casefold(), label.casefold(), widget)
        except Exception:
            pass

        # Default: treat as formcontrol/textfield root
        sec = self._nearest_h6_title(node) or title_fallback
        lbl = self._label_text_formcontrol(node)
        if not lbl:
            try:
                inp = node.locator("css=input, textarea").first
                if inp.count():
                    lbl = _norm(inp.get_attribute("aria-label") or "") or _norm(inp.get_attribute("placeholder") or "")
            except Exception:
                pass
        if not lbl:
            lbl = self._fallback_label_from_context(node)

        w, _, _ = self._detect_widget_and_options_formcontrol(node)
        return (sec.casefold(), lbl.casefold(), w.casefold())

    def _find_radio_field_index(self, scope: Locator, radio_name: str) -> Optional[int]:
        if not radio_name:
            return None
        nodes = self._collect_field_nodes_in_scope(scope)
        for i, n in enumerate(nodes):
            if self._node_has_radio_name(n, radio_name):
                return i
        return None

    def _window_after_radio(self, scope: Locator, radio_name: str, title_fallback: str, k: int) -> List[Tuple[str, str, str]]:
        nodes = self._collect_field_nodes_in_scope(scope)
        idx = self._find_radio_field_index(scope, radio_name)
        if idx is None:
            return []
        win = nodes[idx + 1 : idx + 1 + k]
        return [self._sig_node(n, title_fallback) for n in win]

    def _seq_diff(self, before: List[Tuple[str, str, str]], after: List[Tuple[str, str, str]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        n = max(len(before), len(after))
        for i in range(n):
            b = before[i] if i < len(before) else None
            a = after[i] if i < len(after) else None
            if b == a:
                continue
            out.append(
                {
                    "index": i,
                    "before": None if b is None else {"section": b[0], "label": b[1], "widget": b[2]},
                    "after": None if a is None else {"section": a[0], "label": a[1], "widget": a[2]},
                }
            )
        return out

    def probe_radios_dynamic(self) -> List[Dict[str, Any]]:
        """Probe conditional fields after radio changes.

        We compare the NEXT K parameters *by position* (sequence), not just set-diff.
        The window is built from *all* field-like nodes (formcontrols, standalone checkboxes, file inputs).
        """
        if not self.enable_radio_probe:
            return []

        root = self._root()
        self.open_all_blocks_sticky()
        nav_items = self.list_navigation_items()

        results: List[Dict[str, Any]] = []

        for title, occ in nav_items:
            if title in self._NAV_EXCLUDE:
                continue
            scope = self._scope_for_nav_item(title, occ)
            if scope is None:
                continue

            self.page.wait_for_timeout(self.ui_delay_ms)

            nodes = self._collect_field_nodes_in_scope(scope)
            for host_node in nodes:
                rg = host_node.locator("css=[role='radiogroup']").first
                if not rg.count():
                    continue

                option_labels = rg.locator("xpath=.//label[.//input[@type='radio']]")
                if option_labels.count() <= 1:
                    continue

                radio_name = self._radio_group_name(option_labels)

                field_label = self._label_text_formcontrol(host_node)
                if not field_label:
                    field_label = self._radiogroup_title_from_rg(rg) or self._fallback_label_from_context(host_node)

                field_section = self._nearest_h6_title(host_node) or title

                base_seq = self._window_after_radio(scope, radio_name, title, self.radio_follow_window)

                orig_idx = 0
                for oi in range(option_labels.count()):
                    try:
                        if option_labels.nth(oi).locator("css=input[type='radio']").first.is_checked():
                            orig_idx = oi
                            break
                    except Exception:
                        pass

                group_info: Dict[str, Any] = {
                    "nav": title,
                    "section": field_section,
                    "label": field_label,
                    "window_size": self.radio_follow_window,
                    "base_window": [{"section": b[0], "label": b[1], "widget": b[2]} for b in base_seq],
                    "options": [],
                }

                for oi in range(option_labels.count()):
                    o = option_labels.nth(oi)
                    opt_text = _norm(o.locator("css=span.MuiFormControlLabel-label").inner_text() or "") or f"option_{oi}"

                    ok = self._set_radio_option(o)
                    if not ok:
                        group_info["options"].append(
                            {"value": opt_text, "select_failed": True, "after_window": [], "diff": []}
                        )
                        continue

                    self.page.wait_for_timeout(self.ui_delay_ms + 350)
                    self.expand_all_collapsibles(scope, max_rounds=4)
                    self.page.wait_for_timeout(self.ui_delay_ms)

                    after_seq = self._window_after_radio(scope, radio_name, title, self.radio_follow_window)
                    diff = self._seq_diff(base_seq, after_seq)

                    group_info["options"].append(
                        {
                            "value": opt_text,
                            "select_failed": False,
                            "after_window": [{"section": a[0], "label": a[1], "widget": a[2]} for a in after_seq],
                            "diff": diff,
                        }
                    )

                try:
                    self._set_radio_option(option_labels.nth(orig_idx))
                    self.page.wait_for_timeout(self.ui_delay_ms)
                except Exception:
                    pass

                if any((not x["select_failed"]) and x.get("diff") for x in group_info["options"]):
                    results.append(group_info)

        return results


def run_collection(
    *,
    phone: str,
    password: str,
    headless: bool = False,
    slow_mo_ms: int = 0,
    out_path: str = "schema_dump.json",
    ui_delay_ms: int = 350,
    radio_follow_window: int = 4,
    enable_radio_probe: bool = True,
) -> str:
    creds = RieltorCredentials(phone=phone, password=password)

    property_types = [
        "Квартира",
        "Кімната",
        "Будинок",
        "Комерційна",
        "Ділянка",
        "Паркомісце",
    ]

    dump: Dict[str, Any] = {}

    with RieltorSession(creds=creds, headless=headless, slow_mo_ms=slow_mo_ms) as sess:
        sess.login()
        page = sess.page
        if page is None:
            raise RuntimeError("No page")

        collector = OfferCreateSchemaCollector(
            page,
            ui_delay_ms=ui_delay_ms,
            radio_follow_window=radio_follow_window,
            enable_radio_probe=enable_radio_probe,
        )
        collector.open()

        # first open everything
        collector.open_all_blocks_sticky()

        for pt in property_types:
            collector.select_property_type(pt)

            schema = collector.collect_schema_dynamic_h6()
            cond = collector.probe_radios_dynamic() if enable_radio_probe else []

            dump[pt] = {
                "url": page.url,
                "ui_delay_ms": ui_delay_ms,
                "navigation": schema["navigation"],
                "fields": schema["fields"],
                "conditionals": {
                    "radio_dynamic_h6_windowed": cond,
                    "window_size": radio_follow_window,
                },
            }

            page.wait_for_timeout(ui_delay_ms)

    Path(out_path).write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    phone = (os.getenv("PHONE") or "").strip()
    password = (os.getenv("PASSWORD") or "").strip()
    if not phone or not password:
        raise SystemExit("Set PHONE and PASSWORD in env (.env supported)")

    out = run_collection(
        phone=phone,
        password=password,
        headless=False,
        slow_mo_ms=0,
        out_path="models/schema_dump.json",
        ui_delay_ms=350,
        radio_follow_window=4,
        enable_radio_probe=True,
    )
    print(f"Saved: {out}")
