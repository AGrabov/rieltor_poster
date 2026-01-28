from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from setup_logger import setup_logger
logger = setup_logger(__name__)

from playwright.sync_api import Locator

from .helpers import (_xpath_literal, _norm, _cf)


class _WidgetMixin:
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

    def _collect_select_options(self, form: Locator) -> Tuple[List[str], Dict[str, Any]]:
        """Open listbox for a select in this form and return option texts and metadata."""
        select_btn = form.locator("css=div.MuiSelect-select[role='button']").first
        if not select_btn.count():
            return [], {}

        menu_id = None
        try:
            menu_id = select_btn.get_attribute("aria-controls")
        except Exception:
            menu_id = None

        lb = self._open_listbox(select_btn, menu_id)
        if not lb:
            logger.debug("Listbox open failed")
            return [], {}

        # Detect multiselect by checking for checkboxes
        select_meta: Dict[str, Any] = {}
        try:
            has_checkboxes = lb.locator("input[type='checkbox']").count() > 0
            if has_checkboxes:
                select_meta["is_multiselect"] = True
        except Exception:
            pass

        opts = self._list_listbox_options(lb)
        logger.debug("Collected select options: %d (multiselect=%s)", len(opts), select_meta.get("is_multiselect", False))
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass
        self.page.wait_for_timeout(self.ui_delay_ms)
        return opts, select_meta

    def _collect_autocomplete_options(self, form: Locator, query: str = "а") -> List[str]:
        """Trigger autocomplete dropdown with a query and collect visible options."""
        inp = form.locator("css=input").first
        if not inp.count():
            logger.debug("No input found in autocomplete form")
            return []

        # Save current value
        original_value = ""
        try:
            original_value = inp.input_value() or ""
        except Exception:
            pass

        try:
            # Focus input
            inp.click(timeout=2000)
        except Exception:
            try:
                inp.click(force=True, timeout=2000)
            except Exception:
                logger.debug("Failed to click autocomplete input")
                return []

        self.page.wait_for_timeout(self.ui_delay_ms)

        # Clear and type query
        try:
            inp.fill("")
            inp.type(query, delay=35)
        except Exception:
            logger.debug("Failed to type into autocomplete")
            return []

        # Open dropdown
        try:
            inp.press("ArrowDown")
        except Exception:
            pass

        self.page.wait_for_timeout(self.ui_delay_ms + 200)

        # Wait for options and collect them
        options: List[str] = []
        try:
            # Wait briefly for options to appear
            visible_cnt = 0
            try:
                # Use enhanced wait method from _address_seed mixin (available via inheritance)
                visible_cnt = self._wait_autocomplete_options(inp, timeout_s=3.0, debug_label=f"collect_options(q='{query}')")
            except AttributeError:
                # Fallback: simple wait if method not available
                self.page.wait_for_timeout(1000)

            if visible_cnt > 0 or True:  # Always try to collect
                # Collect option texts using JavaScript (expanded selectors)
                options = list(
                    self.page.evaluate(
                        """
                        (input) => {
                          const isVisible = (el) => {
                            if (!el) return false;
                            const cs = window.getComputedStyle(el);
                            if (!cs) return false;
                            if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
                            const r = el.getBoundingClientRect();
                            return !!r && r.width > 5 && r.height > 5;
                          };

                          const norm = (s) => (s || '').replace(/\\s+/g,' ').trim();

                          // Expanded selectors matching _wait_autocomplete_options
                          const selectors = [
                            // Specific pattern found in this site (MUI List with div items)
                            '#autocomplete-popper div.MuiListItem-button',
                            '#autocomplete-popper div.MuiListItem-root[role="button"]',
                            '[role="tooltip"] div.MuiListItem-button',
                            '[role="tooltip"] div.MuiListItem-root[role="button"]',
                            'ul.MuiList-root div.MuiListItem-button',
                            'ul.MuiList-root div.MuiListItem-root[role="button"]',
                            '.MuiPaper-root div.MuiListItem-button',
                            'div.MuiListItem-button',
                            'div.MuiListItem-root[role="button"]',
                            // MUI Autocomplete popper variants
                            '#autocomplete-popper [role="option"]',
                            '#autocomplete-popper li[data-option-index]',
                            '#autocomplete-popper li.MuiAutocomplete-option',
                            '.MuiAutocomplete-popper [role="option"]',
                            '.MuiAutocomplete-popper li[data-option-index]',
                            '.MuiAutocomplete-popper li.MuiAutocomplete-option',
                            '.MuiAutocomplete-popper li',
                            '[role="listbox"] [role="option"]',
                            '[role="listbox"] li',
                            '[role="listbox"] > *',
                            '[role="tooltip"] [role="option"]',
                            '[role="tooltip"] li[data-option-index]',
                            '[role="tooltip"] li.MuiAutocomplete-option',
                            '[role="tooltip"] li',
                            '.MuiPaper-root [role="option"]',
                            '.MuiPaper-root li.MuiAutocomplete-option',
                            '.MuiPaper-root li[data-option-index]',
                            '.MuiPopper-root [role="option"]',
                            '.MuiPopper-root li',
                            'li.MuiAutocomplete-option',
                            'li[data-option-index]',
                            '[role="menu"] [role="option"]',
                            '[role="menu"] li',
                          ];

                          const nodes = [];
                          for (const sel of selectors) {
                            document.querySelectorAll(sel).forEach(n => nodes.push(n));
                          }

                          const uniq = Array.from(new Set(nodes))
                            .filter(isVisible)
                            .map(el => norm(el.innerText || el.textContent || ''))
                            .filter(t => t.length > 0);

                          return Array.from(new Set(uniq));
                        }
                        """,
                        inp,
                    )
                )
                logger.debug("Collected autocomplete options: %d", len(options))

        except Exception as e:
            logger.debug("Failed to collect autocomplete options: %s", e)

        # Cleanup: restore original value or clear
        try:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(self.ui_delay_ms)
            if original_value:
                inp.fill(original_value)
            else:
                inp.fill("")
        except Exception:
            pass

        self.page.wait_for_timeout(self.ui_delay_ms)

        return options

    def _detect_widget_and_options_formcontrol(self, form: Locator) -> Tuple[str, List[str], Dict[str, Any]]:
        meta: Dict[str, Any] = {}

        rg = form.locator("css=[role='radiogroup']").first
        if rg.count():
            return "radio", self._radio_options(rg), meta

        if form.locator("css=input[type='checkbox']").count():
            return "checkbox", [], meta

        # NOTE: do NOT open listbox here (expensive). Options are collected lazily with caching.
        if form.locator("css=div.MuiSelect-select[role='button']").count():
            # Detect multiselect by checking for multiple attribute
            select_btn = form.locator("css=div.MuiSelect-select[role='button']").first
            try:
                aria_multiselectable = select_btn.get_attribute("aria-multiselectable")
                if aria_multiselectable == "true":
                    meta["is_multiselect"] = True
            except Exception:
                pass
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
