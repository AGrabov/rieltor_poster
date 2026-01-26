from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from collections import Counter

from setup_logger import setup_logger
logger = setup_logger(__name__)

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


def _key4(nav: str, section: str, label: str, widget: str) -> str:
    return "||".join([_cf(nav), _cf(section), _cf(label), _cf(widget)])


def _sig3(section: str, label: str, widget: str) -> str:
    """Key used for dedupe independent of nav."""
    return "||".join([_cf(section), _cf(label), _cf(widget)])


def _slug(s: str) -> str:
    s = _norm(s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^0-9A-Za-zА-Яа-яІіЇїЄєҐґ_\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "item"


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

    # do not dump "Тип угоди"; everything else must be collected
    _NAV_EXCLUDE = {"Тип угоди"}

    def __init__(
        self,
        page: Page,
        *,
        ui_delay_ms: int = 350,
        radio_follow_window: int = 4,   # kept for compatibility (not used directly in v2)
        enable_radio_probe: bool = True,
        debug: bool = False,
    ) -> None:
        self.page = page
        self.ui_delay_ms = int(ui_delay_ms)
        self.radio_follow_window = int(radio_follow_window)
        self.enable_radio_probe = bool(enable_radio_probe)

        # Cache select options+meta to avoid reopening listboxes repeatedly.
        # Reset per property type in select_property_type().
        self._select_options_cache: Dict[str, Dict[str, Any]] = {}

        self._epoch = 0

        if debug:
            logger.setLevel("DEBUG")
        self.debug = debug

    # ---------------- open / root ----------------
    def open(self) -> None:
        logger.info("Open create page: %s", self.CREATE_URL)
        self.page.goto(self.CREATE_URL, wait_until="domcontentloaded")
        self._wait_ready()
        logger.info("Create page ready: %s", _norm(self.page.title() or ""))

    def _wait_ready(self) -> None:
        logger.debug("Wait ready (h5 'Нове оголошення' visible)")
        self.page.locator(
            "xpath=//h5[normalize-space(.)='Нове оголошення' or .//*[normalize-space(.)='Нове оголошення']]"
        ).first.wait_for(state="visible", timeout=30_000)
        try:
            self.page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass
        logger.debug("Ready: url=%s", self.page.url)

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
                logger.debug("UI overlay visible: closing with Escape")
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
        """
        Find the *nearest* collapse container bound to this toggle.

        IMPORTANT: this prevents 'Додаткові параметри' from returning the big parent collapse
        and duplicating fields.
        """
        node = btn
        for _ in range(12):
            sib = node.locator("xpath=following-sibling::*[1][contains(@class,'MuiCollapse-container')]").first
            if sib.count():
                return sib
            parent = node.locator("xpath=..").first
            if not parent.count():
                break
            node = parent

        # fallback
        return btn.locator(
            "xpath=ancestor::div[contains(@class,'MuiBox-root')][1]//div[contains(@class,'MuiCollapse-container')][1]"
        ).first

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
        scope = scope or self._root()

        total_opened = 0
        for _ in range(max_rounds):
            changed = 0
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

                col = self._collapse_container_for_h6(h6)
                if not col.count():
                    continue
                if self._is_collapse_entered(col):
                    continue

                if self._open_toggle_if_closed(h6):
                    logger.debug("Expanded: %s", title)
                    changed += 1

            total_opened += changed
            if changed == 0:
                break

        if total_opened:
            logger.info("Expanded %d collapsibles", total_opened)

    def open_all_blocks_sticky(self) -> None:
        logger.debug("Open all blocks sticky")
        root = self._root()
        self._scroll_to_bottom()
        self.page.wait_for_timeout(self.ui_delay_ms + 250)
        self.expand_all_collapsibles(root, max_rounds=18)
        self._scroll_to_top()
        self.page.wait_for_timeout(self.ui_delay_ms + 250)
        self.expand_all_collapsibles(root, max_rounds=18)
        self._scroll_to_top()
        self.page.wait_for_timeout(self.ui_delay_ms + 250)
        self.expand_all_collapsibles(root, max_rounds=18)

    # ---------------- property type ----------------
    def select_property_type(self, ui_text: str) -> None:
        self._select_options_cache = {}

        logger.info("Select property type: %s", ui_text)
        root = self._root()
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

        inner = chosen.locator("xpath=.//span[normalize-space()]").first
        if not self._click_best_effort(inner if inner.count() else chosen):
            logger.warning("Failed to click property type card: %s", ui_text)

        self._wait_ready()
        try:
            self.page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        self.page.wait_for_timeout(self.ui_delay_ms + 450)

        self._epoch += 1
        self.open_all_blocks_sticky()
        logger.info("Property type selected: %s (epoch=%s)", ui_text, self._epoch)

    # ---------------- navigation items (h6) ----------------
    def list_navigation_items(self) -> List[Tuple[str, int]]:
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

        compact: List[Tuple[str, int]] = []
        prev: Optional[str] = None
        for t, occ in out:
            if prev is not None and _cf(prev) == _cf(t):
                continue
            compact.append((t, occ))
            prev = t

        logger.debug("Navigation items: %d", len(compact))
        logger.debug("Navigation list: %s", [t for (t, _) in compact])
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

        logger.debug("Scope for nav: %s (occ=%d)", title, occ)

        try:
            h6.scroll_into_view_if_needed(timeout=1500)
        except Exception:
            pass

        # If this h6 is a collapsible toggle, prefer collapse content only
        if h6.locator("xpath=ancestor::button[1]").count():
            col = self._collapse_container_for_h6(h6)
            if col.count() and (not self._is_collapse_entered(col)):
                self._open_toggle_if_closed(h6)
                self.page.wait_for_timeout(self.ui_delay_ms)

            if col.count():
                self.expand_all_collapsibles(col, max_rounds=10)
                self.page.wait_for_timeout(self.ui_delay_ms)
                return col

        # Else: a plain section block around this h6
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

        # 2) <p> title
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

        # 3) fallback: nearest h6 (useful for 'Ексклюзивний договір...')
        t = _norm(self._nearest_h6_title(rg) or "")
        if t and not self._is_helper_text(t):
            return t

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

    def _collect_select_options_and_meta(self, form: Locator) -> Tuple[List[str], Dict[str, Any]]:
        """Open listbox for a select in this form and return (options, meta_add)."""
        meta_add: Dict[str, Any] = {}

        select_btn = form.locator("css=div.MuiSelect-select[role='button']").first
        if not select_btn.count():
            return [], meta_add

        menu_id = None
        try:
            menu_id = select_btn.get_attribute("aria-controls")
        except Exception:
            menu_id = None

        lb = self._open_listbox(select_btn, menu_id)
        if not lb:
            logger.debug("Listbox open failed")
            return [], meta_add

        # Detect multi / checkbox-list
        try:
            meta_add["multiselect"] = (lb.get_attribute("aria-multiselectable") or "").lower() == "true"
        except Exception:
            meta_add["multiselect"] = False

        try:
            meta_add["options_have_checkboxes"] = lb.locator("css=input[type='checkbox']").count() > 0
        except Exception:
            meta_add["options_have_checkboxes"] = False

        # Chips in form usually mean multi-select too
        try:
            if form.locator("css=.MuiChip-root, .MuiChip-label").count():
                meta_add["multiselect"] = True
        except Exception:
            pass

        opts = self._list_listbox_options(lb)
        logger.debug("Collected select options: %d (multi=%s, cb_in_opts=%s)", len(opts), meta_add.get("multiselect"), meta_add.get("options_have_checkboxes"))

        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass
        self.page.wait_for_timeout(self.ui_delay_ms)
        return opts, meta_add

    def _detect_widget_and_options_formcontrol(self, form: Locator) -> Tuple[str, List[str], Dict[str, Any]]:
        meta: Dict[str, Any] = {}

        rg = form.locator("css=[role='radiogroup']").first
        if rg.count():
            return "radio", self._radio_options(rg), meta

        if form.locator("css=input[type='checkbox']").count():
            return "checkbox", [], meta

        # options collected lazily
        if form.locator("css=div.MuiSelect-select[role='button']").count():
            return "select", [], meta

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
        out: List[Locator] = []
        for i in range(roots.count()):
            n = roots.nth(i)
            try:
                if not n.is_visible():
                    continue
            except Exception:
                pass
            out.append(n)
        return out

    def _collect_label_controls_in_scope(self, scope: Locator, nav_title: str) -> List[FieldInfo]:
        out: List[FieldInfo] = []
        labels = scope.locator(
            "xpath=.//label[contains(@class,'MuiFormControlLabel-root')][.//input[@type='checkbox' or @type='radio']]"
        )
        for i in range(labels.count()):
            lab = labels.nth(i)
            try:
                if not lab.is_visible():
                    continue
            except Exception:
                pass
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

        logger.debug("Collect fields in scope: %s", nav_title)
        forms = self._collect_forms_in_scope(scope)
        logger.debug("Found forms: %d", len(forms))

        for form in forms:
            section = self._nearest_h6_title(form) or nav_title
            required = self._is_required(form)
            widget, options, meta = self._detect_widget_and_options_formcontrol(form)

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

            # Lazy select options collection with caching (options + meta for listbox)
            if widget == "select":
                cache_key = _key4(nav_title, section, label, "select")
                cached = self._select_options_cache.get(cache_key)
                if cached is not None:
                    options = list(cached.get("options") or [])
                    meta.update({k: v for k, v in (cached.get("meta") or {}).items() if k not in meta})
                    logger.debug("Select cache hit: %s", cache_key)
                else:
                    logger.debug("Select cache miss: %s", cache_key)
                    opts, meta_add = self._collect_select_options_and_meta(form)
                    options = opts
                    meta.update({k: v for k, v in meta_add.items() if k not in meta})
                    self._select_options_cache[cache_key] = {"options": options, "meta": meta_add}

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
        logger.debug("Collected fields: %s => %d", nav_title, len(out))
        return out

    # ---------------- schema dump ----------------
    def collect_schema_dynamic_h6(self) -> Dict[str, Any]:
        root = self._root()
        self.open_all_blocks_sticky()

        nav_items = self.list_navigation_items()
        all_fields: List[FieldInfo] = []

        logger.info("Collect schema: nav_items=%d", len(nav_items))

        for title, occ in nav_items:
            if title in self._NAV_EXCLUDE:
                continue
            logger.debug("Collect nav: %s", title)
            scope = self._scope_for_nav_item(title, occ)
            if scope is None:
                logger.debug("Nav scope not found: %s", title)
                continue
            self.page.wait_for_timeout(self.ui_delay_ms)
            fields = self._collect_fields_in_scope(scope, title)
            all_fields.extend(fields)

        logger.info("Total collected fields (raw): %d", len(all_fields))

        # 1) merge strict duplicates by (nav,section,label,widget)
        by_key4: Dict[Tuple[str, str, str, str], FieldInfo] = {}
        order4: List[Tuple[str, str, str, str]] = []

        def _merge_opts(a: List[str], b: List[str]) -> List[str]:
            if not a:
                return list(b)
            if not b:
                return list(a)
            seeno = set(x.casefold() for x in a)
            outm = list(a)
            for x in b:
                kx = x.casefold()
                if kx in seeno:
                    continue
                seeno.add(kx)
                outm.append(x)
            return outm

        merged_dups4 = 0
        for f in all_fields:
            k4 = (f.nav.casefold(), f.section.casefold(), f.label.casefold(), f.widget.casefold())
            ex = by_key4.get(k4)
            if ex is None:
                by_key4[k4] = f
                order4.append(k4)
                continue
            merged_dups4 += 1
            ex.required = bool(ex.required or f.required)
            ex.options = _merge_opts(ex.options or [], f.options or [])
            for mk, mv in (f.meta or {}).items():
                ex.meta.setdefault(mk, mv)

        uniq4 = [by_key4[k] for k in order4]
        logger.info("Fields unique by key4=%d (merged_dups=%d)", len(uniq4), merged_dups4)

        # 2) merge cross-nav duplicates by (section,label,widget); keep navs in meta
        by_sig: Dict[str, FieldInfo] = {}
        order_sig: List[str] = []
        merged_cross_nav = 0

        for f in uniq4:
            sig = _sig3(f.section, f.label, f.widget)
            ex = by_sig.get(sig)
            if ex is None:
                # init navs
                f.meta = dict(f.meta or {})
                f.meta["navs"] = [f.nav]
                by_sig[sig] = f
                order_sig.append(sig)
                continue

            merged_cross_nav += 1
            ex.required = bool(ex.required or f.required)
            ex.options = _merge_opts(ex.options or [], f.options or [])
            for mk, mv in (f.meta or {}).items():
                # merge navs separately
                if mk == "navs":
                    continue
                ex.meta.setdefault(mk, mv)

            navs = ex.meta.get("navs") or []
            if f.nav not in navs:
                navs.append(f.nav)
            ex.meta["navs"] = navs

        uniq = [by_sig[s] for s in order_sig]
        logger.info("Fields unique by sig3=%d (merged_cross_nav=%d)", len(uniq), merged_cross_nav)

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

    # ---------------- RADIO probe ----------------
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

    def _collect_field_nodes_in_scope(self, scope: Locator) -> List[Locator]:
        nodes = scope.locator(
            "xpath=(.//div[contains(@class,'MuiFormControl-root') or contains(@class,'MuiTextField-root')]"
            " | .//label[contains(@class,'MuiFormControlLabel-root')][.//input[@type='checkbox' or @type='radio']])"
        )
        out: List[Locator] = []
        for i in range(nodes.count()):
            n = nodes.nth(i)
            try:
                if not n.is_visible():
                    continue
            except Exception:
                pass
            try:
                if n.evaluate("(el)=>el.tagName==='LABEL'"):
                    if n.locator("xpath=ancestor::*[@role='radiogroup'][1]").count():
                        continue
            except Exception:
                pass
            out.append(n)
        return out

    def _sig_node(self, node: Locator, title_fallback: str) -> Tuple[str, str, str]:
        try:
            if node.evaluate("(el)=>el.tagName==='LABEL'"):
                sec = _norm(self._nearest_h6_title(node) or title_fallback)
                label = _norm(self._label_text_labelcontrol(node) or self._fallback_label_from_context(node))
                itype = ""
                try:
                    itype = (node.locator("css=input").first.get_attribute("type") or "").casefold()
                except Exception:
                    itype = ""
                widget = "checkbox" if itype == "checkbox" else "radio"
                return (sec, label, widget)
        except Exception:
            pass

        sec = _norm(self._nearest_h6_title(node) or title_fallback)
        lbl = _norm(self._label_text_formcontrol(node) or self._fallback_label_from_context(node))
        w, _, _ = self._detect_widget_and_options_formcontrol(node)
        return (sec, lbl, _norm(w).casefold())

    def _snapshot_scope(self, scope: Locator, title_fallback: str, nav: str) -> Tuple[List[Dict[str, Any]], Counter]:
        nodes = self._collect_field_nodes_in_scope(scope)
        ordered: List[Dict[str, Any]] = []
        keys: List[str] = []
        for n in nodes:
            sec, lab, wid = self._sig_node(n, title_fallback)
            if not lab or self._is_helper_text(lab):
                continue
            key = _sig3(sec, lab, wid)
            item = {
                "nav": _norm(nav),
                "section": _norm(sec),
                "label": _norm(lab),
                "widget": _norm(wid).casefold(),
                "sig": key,
            }
            ordered.append(item)
            keys.append(key)
        return ordered, Counter(keys)

    def _radio_current_value(self, rg: Locator) -> str:
        labels = rg.locator("xpath=.//label[.//input[@type='radio']]")
        for i in range(labels.count()):
            l = labels.nth(i)
            try:
                inp = l.locator("css=input[type='radio']").first
                if inp.count() and inp.is_checked():
                    t = _norm(l.locator("css=span.MuiFormControlLabel-label").inner_text() or "")
                    return t
            except Exception:
                continue
        return ""

    def _radio_set_by_value(self, rg: Locator, value: str) -> bool:
        value_cf = _cf(value)
        labels = rg.locator("xpath=.//label[.//input[@type='radio']]")
        for i in range(labels.count()):
            l = labels.nth(i)
            try:
                t = _norm(l.locator("css=span.MuiFormControlLabel-label").inner_text() or "")
            except Exception:
                t = ""
            if _cf(t) == value_cf:
                return self._set_radio_option(l)
        return False

    def _radio_values(self, rg: Locator) -> List[str]:
        out: List[str] = []
        labels = rg.locator("xpath=.//label[.//input[@type='radio']]")
        for i in range(labels.count()):
            l = labels.nth(i)
            try:
                t = _norm(l.locator("css=span.MuiFormControlLabel-label").inner_text() or "")
            except Exception:
                t = ""
            if t:
                out.append(t)
        seen: Set[str] = set()
        uniq: List[str] = []
        for t in out:
            k = _cf(t)
            if k in seen:
                continue
            seen.add(k)
            uniq.append(t)
        return uniq

    def _preferred_radio_value(self, values: List[str]) -> str:
        for cand in ("Немає", "Нiмає", "Нет", "Ні", "No"):
            for v in values:
                if _cf(v) == _cf(cand):
                    return v
        return values[0] if values else ""

    def _counter_delta(self, base: Counter, after: Counter) -> Tuple[List[str], List[str]]:
        added: List[str] = []
        removed: List[str] = []
        allk = set(base.keys()) | set(after.keys())
        for k in allk:
            b = base.get(k, 0)
            a = after.get(k, 0)
            if a > b:
                added.extend([k] * (a - b))
            elif b > a:
                removed.extend([k] * (b - a))
        return added, removed

    def _local_probe_scope_for_rg(self, rg: Locator, nav_scope: Locator, nav_title: str) -> Locator:
        sec_title = self._nearest_h6_title(rg)
        if sec_title:
            lit = _xpath_literal(sec_title)
            try:
                box = rg.locator(
                    f"xpath=ancestor::div[contains(@class,'MuiBox-root')][.//h6[normalize-space(.)={lit}]][1]"
                ).first
                if box.count():
                    if box.locator(
                        "xpath=.//div[contains(@class,'MuiFormControl-root') or contains(@class,'MuiTextField-root')]"
                    ).count() >= 2:
                        return box
            except Exception:
                pass
        return nav_scope

    def _find_select_form(self, scope: Locator, nav_title: str, section_cf: str, label_cf: str) -> Optional[Locator]:
        forms = scope.locator(
            "xpath=.//div[contains(@class,'MuiFormControl-root') or contains(@class,'MuiTextField-root')][.//div[contains(@class,'MuiSelect-select') and @role='button']]"
        )
        for i in range(forms.count()):
            f = forms.nth(i)
            try:
                lab = _cf(self._label_text_formcontrol(f) or "")
            except Exception:
                lab = ""
            if not lab or lab != label_cf:
                continue
            try:
                sec = _cf(self._nearest_h6_title(f) or nav_title)
            except Exception:
                sec = _cf(nav_title)
            if section_cf and sec != section_cf:
                continue
            return f
        return None

    def _cache_select_options_for_added(self, local_scope: Locator, nav_title: str, added: List[Dict[str, Any]]) -> None:
        for a in added:
            if _cf(a.get("widget", "")) != "select":
                continue
            sec_cf = _cf(a.get("section", "") or nav_title)
            lab_cf = _cf(a.get("label", ""))
            if not lab_cf:
                continue

            cache_key = _key4(nav_title, sec_cf, lab_cf, "select")
            cached = self._select_options_cache.get(cache_key)
            if cached is not None:
                a.setdefault("options", list(cached.get("options") or []))
                a.setdefault("meta", dict(cached.get("meta") or {}))
                logger.debug("Added-select cache hit: %s (opts=%d)", cache_key, len(a["options"]))
                continue

            form = self._find_select_form(local_scope, nav_title, sec_cf, lab_cf)
            if not form:
                logger.debug("Added-select form not found: nav=%s section=%s label=%s", nav_title, sec_cf, lab_cf)
                continue

            logger.debug("Added-select cache miss: %s", cache_key)
            opts, meta_add = self._collect_select_options_and_meta(form)
            self._select_options_cache[cache_key] = {"options": opts, "meta": meta_add}
            a.setdefault("options", opts)
            a.setdefault("meta", meta_add)
            logger.debug("Added-select cached: %s (opts=%d)", cache_key, len(opts))

    def probe_radios_dynamic(self) -> List[Dict[str, Any]]:
        if not self.enable_radio_probe:
            logger.info("Radio probe disabled")
            return []

        logger.info("Radio probe start")
        self.open_all_blocks_sticky()
        nav_items = self.list_navigation_items()
        results: List[Dict[str, Any]] = []

        for title, occ in nav_items:
            if title in self._NAV_EXCLUDE:
                continue
            scope = self._scope_for_nav_item(title, occ)
            if scope is None:
                continue

            logger.debug("Radio probe nav: %s", title)
            self.page.wait_for_timeout(self.ui_delay_ms)
            self.expand_all_collapsibles(scope, max_rounds=8)
            self.page.wait_for_timeout(self.ui_delay_ms)

            # set all radiogroups to preferred baseline (usually "Немає")
            rgs = scope.locator("xpath=.//*[@role='radiogroup']")
            rg_count = rgs.count()
            logger.debug("Radiogroups in '%s': %d", title, rg_count)
            for i in range(rg_count):
                rg = rgs.nth(i)
                values = self._radio_values(rg)
                if len(values) <= 1:
                    continue
                pref = self._preferred_radio_value(values)
                if pref:
                    self._radio_set_by_value(rg, pref)
                    self.page.wait_for_timeout(self.ui_delay_ms)

            # probe
            rgs = scope.locator("xpath=.//*[@role='radiogroup']")
            rg_count = rgs.count()
            for i in range(rg_count):
                rg = rgs.nth(i)
                values = self._radio_values(rg)
                if len(values) <= 1:
                    continue

                label = self._radiogroup_title_from_rg(rg)
                if not label or self._is_helper_text(label):
                    continue

                host_section = self._nearest_h6_title(rg) or title

                baseline_val = self._radio_current_value(rg)
                pref = self._preferred_radio_value(values)
                if pref and _cf(baseline_val) != _cf(pref):
                    self._radio_set_by_value(rg, pref)
                    self.page.wait_for_timeout(self.ui_delay_ms)
                    baseline_val = self._radio_current_value(rg)

                local_scope = self._local_probe_scope_for_rg(rg, scope, title)
                self.expand_all_collapsibles(local_scope, max_rounds=6)
                self.page.wait_for_timeout(self.ui_delay_ms)

                base_ordered, base_counter = self._snapshot_scope(local_scope, title, title)

                controller_field_key = _key4(title, host_section, label, "radio")
                controller_key = f"{controller_field_key}@@{i}"

                group_info: Dict[str, Any] = {
                    "nav": title,
                    "section": host_section,
                    "label": label,
                    "widget": "radio",
                    "controller_field_key": controller_field_key,
                    "controller_key": controller_key,
                    "controller_ord": i,
                    "baseline_value": baseline_val,
                    "baseline_fields_count": int(sum(base_counter.values())),
                    "options": [],
                }

                by_sig_base: Dict[str, Dict[str, Any]] = {it["sig"]: it for it in base_ordered}

                any_change = False

                for v in values:
                    if _cf(v) == _cf(baseline_val):
                        group_info["options"].append({"value": v, "select_failed": False, "added": [], "removed": []})
                        continue

                    ok = self._radio_set_by_value(rg, v)
                    if not ok:
                        logger.debug("Radio set failed: %s=%s", label, v)
                        group_info["options"].append({"value": v, "select_failed": True, "added": [], "removed": []})
                        continue

                    self.page.wait_for_timeout(self.ui_delay_ms + 450)
                    self.expand_all_collapsibles(local_scope, max_rounds=6)
                    self.page.wait_for_timeout(self.ui_delay_ms)

                    after_ordered, after_counter = self._snapshot_scope(local_scope, title, title)
                    add_sigs, rem_sigs = self._counter_delta(base_counter, after_counter)

                    by_sig_after: Dict[str, Dict[str, Any]] = {it["sig"]: it for it in after_ordered}

                    added = [by_sig_after.get(s, {"sig": s, "nav": title, "section": "", "label": "", "widget": ""}) for s in add_sigs]
                    removed = [by_sig_base.get(s, {"sig": s, "nav": title, "section": "", "label": "", "widget": ""}) for s in rem_sigs]

                    if added or removed:
                        any_change = True

                    group_info["options"].append({"value": v, "select_failed": False, "added": added, "removed": removed})
                    logger.debug("Radio diff: %s=%s added=%d removed=%d", label, v, len(added), len(removed))

                    try:
                        self._cache_select_options_for_added(local_scope, title, added)
                    except Exception as e:
                        logger.debug("Cache select options for added failed: %s", e)

                    # restore baseline to keep page stable
                    if baseline_val:
                        self._radio_set_by_value(rg, baseline_val)
                        self.page.wait_for_timeout(self.ui_delay_ms + 250)

                if baseline_val:
                    self._radio_set_by_value(rg, baseline_val)
                    self.page.wait_for_timeout(self.ui_delay_ms)

                if any_change:
                    results.append(group_info)

        logger.info("Radio probe done: groups=%d", len(results))
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
    debug: bool = False,
) -> str:
    if debug:
        logger.setLevel("DEBUG")
    logger.info(
        "Run collection: headless=%s slow_mo_ms=%s ui_delay_ms=%s enable_radio_probe=%s out=%s",
        headless,
        slow_mo_ms,
        ui_delay_ms,
        enable_radio_probe,
        out_path,
    )

    creds = RieltorCredentials(phone=phone, password=password)

    property_types = [
        # "Квартира",
        "Кімната",
        "Будинок",
        "Комерційна",
        "Ділянка",
        "Паркомісце",
    ]

    out_path_p = Path(out_path)
    if out_path_p.suffix.lower() == ".json":
        out_dir = out_path_p.parent / out_path_p.stem
        combined_path = out_path_p
    else:
        out_dir = out_path_p
        combined_path = out_dir / "schema_dump.json"

    out_dir.mkdir(parents=True, exist_ok=True)

    dump: Dict[str, Any] = {}

    with RieltorSession(creds=creds, headless=headless, slow_mo_ms=slow_mo_ms, debug=debug) as sess:
        logger.info("Login")
        sess.login()
        page = sess.page
        if page is None:
            raise RuntimeError("No page")

        collector = OfferCreateSchemaCollector(
            page,
            ui_delay_ms=ui_delay_ms,
            radio_follow_window=radio_follow_window,
            enable_radio_probe=enable_radio_probe,
            debug=debug,
        )
        collector.open()
        collector.open_all_blocks_sticky()

        for pt in property_types:
            logger.info("=== PROPERTY TYPE: %s ===", pt)
            collector.select_property_type(pt)

            cond = collector.probe_radios_dynamic() if enable_radio_probe else []
            logger.info("Radio probe groups: %d", len(cond))

            schema = collector.collect_schema_dynamic_h6()
            logger.info("Schema collected: nav=%d fields=%d", len(schema.get("navigation") or []), len(schema.get("fields") or []))

            # Add stable field_key + sig3 to every field meta
            for f in schema["fields"]:
                meta = f.get("meta") or {}
                meta.setdefault(
                    "field_key",
                    _key4(f.get("nav", ""), f.get("section", ""), f.get("label", ""), f.get("widget", "")),
                )
                meta.setdefault(
                    "sig",
                    _sig3(f.get("section", ""), f.get("label", ""), f.get("widget", "")),
                )
                f["meta"] = meta

            # For avoiding duplicates when injecting conditional-only fields:
            existing_sigs: Set[str] = set((ff.get("meta") or {}).get("sig") or _sig3(ff.get("section",""), ff.get("label",""), ff.get("widget","")) for ff in schema["fields"])

            # --- Ensure conditional-only fields are present in schema ---
            added_count = 0
            for g in (cond or []):
                for opt in (g.get("options") or []):
                    if opt.get("select_failed"):
                        continue
                    for a in (opt.get("added") or []):
                        nav_a = _norm(g.get("nav") or "")  # prefer controller nav
                        sec_a = _norm(a.get("section") or g.get("section") or nav_a)
                        lab_a = _norm(a.get("label") or "")
                        wid_a = _norm(a.get("widget") or "")
                        if not nav_a or not lab_a or not wid_a:
                            continue

                        sig = _sig3(sec_a, lab_a, wid_a)
                        if sig in existing_sigs:
                            continue

                        fk = _key4(nav_a, sec_a, lab_a, wid_a)
                        schema["fields"].append(
                            {
                                "nav": nav_a,
                                "section": sec_a or nav_a,
                                "label": lab_a,
                                "widget": wid_a,
                                "required": False,
                                "options": list(a.get("options") or []),
                                "meta": {
                                    "field_key": fk,
                                    "sig": sig,
                                    "inferred_from": "radio_probe_added",
                                } | ({"select_meta": a.get("meta")} if a.get("meta") else {}),
                            }
                        )
                        existing_sigs.add(sig)
                        added_count += 1

            if added_count:
                logger.info("Added conditional-only fields into schema: %d", added_count)

            # Build visible_when map by sig3 (independent of nav)
            cond_map: Dict[str, List[Dict[str, Any]]] = {}

            for g in cond:
                controller = {
                    "nav": g.get("nav", ""),
                    "section": g.get("section", ""),
                    "label": g.get("label", ""),
                    "widget": g.get("widget", "radio") or "radio",
                    "field_key": g.get("controller_field_key") or _key4(g.get("nav", ""), g.get("section", ""), g.get("label", ""), "radio"),
                    "key": g.get("controller_key") or (g.get("controller_field_key") or _key4(g.get("nav", ""), g.get("section", ""), g.get("label", ""), "radio")),
                    "ord": g.get("controller_ord"),
                }

                for opt in (g.get("options") or []):
                    if opt.get("select_failed"):
                        continue
                    val = opt.get("value")
                    for a in (opt.get("added") or []):
                        sec_a = a.get("section", "") or g.get("section", "") or ""
                        lab_a = a.get("label", "") or ""
                        wid_a = a.get("widget", "") or ""
                        if not sec_a or not lab_a or not wid_a:
                            continue
                        sig = _sig3(sec_a, lab_a, wid_a)
                        cond_map.setdefault(sig, []).append(
                            {"controller": controller, "value": val, "source": "radio_probe"}
                        )

            merged_rules = 0
            for f in schema["fields"]:
                meta = f.get("meta") or {}
                sig = meta.get("sig") or _sig3(f.get("section",""), f.get("label",""), f.get("widget",""))
                rules = cond_map.get(sig)
                if not rules:
                    continue
                existing = meta.get("visible_when") or []
                seen = set(json.dumps(x, ensure_ascii=False, sort_keys=True) for x in existing)
                for r in rules:
                    js = json.dumps(r, ensure_ascii=False, sort_keys=True)
                    if js in seen:
                        continue
                    existing.append(r)
                    seen.add(js)
                    merged_rules += 1
                meta["visible_when"] = existing
                f["meta"] = meta

            logger.info("Visible_when merged rules: %d", merged_rules)

            payload = {
                "url": page.url,
                "ui_delay_ms": ui_delay_ms,
                "navigation": schema["navigation"],
                "fields": schema["fields"],
            }

            pt_path = out_dir / f"{_slug(pt)}.json"
            pt_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("Saved per-type schema: %s -> %s", pt, pt_path)

            dump[pt] = payload
            page.wait_for_timeout(ui_delay_ms)

    combined_path.parent.mkdir(parents=True, exist_ok=True)
    combined_path.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Saved combined schema dump: %s", combined_path)
    logger.info("Per-type schemas directory: %s", out_dir)
    return str(combined_path)


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
        debug=True,
    )
    print(f"Saved: {out}")
