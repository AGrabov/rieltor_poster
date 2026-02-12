# schema_collector/_address_seed.py
from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional, Set

from playwright.sync_api import Locator

from .helpers import _cf, _norm
from setup_logger import setup_logger

logger = setup_logger(__name__)


class _AddressSeedMixin:
    """Address seed fill for schema discovery (city -> street -> house number)."""

    # ---------------- debugging helpers ----------------
    def _save_html_context(self, label: str) -> None:
        """Save current page HTML for debugging."""
        try:
            debug_dir = Path("debug_html")
            debug_dir.mkdir(exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = debug_dir / f"{timestamp}_{label}.html"
            html_content = self.page.content()
            filename.write_text(html_content, encoding="utf-8")
            logger.info("Saved HTML context: %s", filename)
        except Exception as e:
            logger.debug("Failed to save HTML context: %s", e)

    # ---------------- find forms ----------------
    def _find_form_by_label_contains(self, scope: Locator, needles: List[str]) -> Optional[Locator]:
        needles_cf = [_cf(x) for x in needles if _norm(x)]
        forms = scope.locator(
            "xpath=.//div[contains(@class,'MuiFormControl-root') or contains(@class,'MuiTextField-root')]"
        )
        for i in range(forms.count()):
            f = forms.nth(i)
            try:
                if not f.is_visible():
                    continue
            except Exception:
                pass

            lab = _cf(self._label_text_formcontrol(f) or "")
            if not lab:
                continue
            if any(n and n in lab for n in needles_cf):
                return f
        return None

    def _list_autocomplete_forms(self, scope: Locator) -> List[Locator]:
        forms = scope.locator(
            "xpath=.//div[contains(@class,'MuiFormControl-root') or contains(@class,'MuiTextField-root')]"
            "[.//div[contains(@class,'MuiAutocomplete-root')] or .//input[contains(@class,'MuiAutocomplete-input')]]"
        )
        out: List[Locator] = []
        for i in range(forms.count()):
            f = forms.nth(i)
            try:
                if not f.is_visible():
                    continue
            except Exception:
                pass
            out.append(f)
        return out

    # ---------------- autocomplete (GLOBAL dropdown / tooltip popper) ----------------
    def _wait_autocomplete_options(self, anchor: Locator, timeout_s: float = 6.0, *, debug_label: str = "") -> int:
        """Wait until any visible autocomplete option appears anywhere in DOM (portal)."""
        deadline = time.time() + float(timeout_s)
        last = 0
        first_check = True
        while time.time() < deadline:
            try:
                result = self.page.evaluate(
                    """
                    (input) => {
                      const isVisible = (el) => {
                        if (!el) return false;
                        const cs = window.getComputedStyle(el);
                        if (!cs) return false;
                        if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
                        const r = el.getBoundingClientRect();
                        return !!r && r.width > 5 && r.height > 5 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
                      };

                      const norm = (s) => (s || '').replace(/\\s+/g,' ').trim();

                      // Expanded selectors to catch more autocomplete patterns
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
                        // Generic listbox
                        '[role="listbox"] [role="option"]',
                        '[role="listbox"] li',
                        '[role="listbox"] > *',
                        // Tooltip variants
                        '[role="tooltip"] [role="option"]',
                        '[role="tooltip"] li[data-option-index]',
                        '[role="tooltip"] li.MuiAutocomplete-option',
                        '[role="tooltip"] li',
                        // Paper/Popper containers
                        '.MuiPaper-root [role="option"]',
                        '.MuiPaper-root li.MuiAutocomplete-option',
                        '.MuiPaper-root li[data-option-index]',
                        '.MuiPopper-root [role="option"]',
                        '.MuiPopper-root li',
                        // Direct autocomplete list items
                        'li.MuiAutocomplete-option',
                        'li[data-option-index]',
                        // Menu items
                        '[role="menu"] [role="option"]',
                        '[role="menu"] li',
                      ];

                      const nodes = [];
                      const foundSelectors = [];
                      for (const sel of selectors) {
                        const found = document.querySelectorAll(sel);
                        if (found.length > 0) {
                          foundSelectors.push({sel, count: found.length});
                          found.forEach(n => nodes.push(n));
                        }
                      }

                      const uniq = Array.from(new Set(nodes));
                      let visible = 0;
                      const visibleTexts = [];
                      for (const el of uniq) {
                        if (isVisible(el)) {
                          visible += 1;
                          const txt = norm(el.innerText || el.textContent || '');
                          if (txt) visibleTexts.push(txt);
                        }
                      }

                      return {
                        count: visible,
                        foundSelectors: foundSelectors,
                        visibleTexts: visibleTexts.slice(0, 5),  // First 5 for debugging
                        totalNodes: uniq.length
                      };
                    }
                    """,
                    anchor,
                )
                cnt = int(result.get("count", 0))
                last = cnt

                # Log detailed info on first check
                if first_check and debug_label:
                    first_check = False
                    found_selectors = result.get("foundSelectors", [])
                    visible_texts = result.get("visibleTexts", [])
                    total_nodes = result.get("totalNodes", 0)

                    logger.debug(
                        "%s: Found %d visible options (total nodes: %d, selectors matched: %d)",
                        debug_label, cnt, total_nodes, len(found_selectors)
                    )
                    if found_selectors:
                        for sel_info in found_selectors[:3]:  # Log first 3 matching selectors
                            logger.debug("  Selector '%s': %d nodes", sel_info["sel"], sel_info["count"])
                    if visible_texts:
                        logger.debug("  Sample options: %s", visible_texts[:3])

                if cnt > 0:
                    return cnt
            except Exception as e:
                if first_check:
                    logger.debug("Error checking autocomplete options: %s", e)
                    first_check = False
            self.page.wait_for_timeout(120)
        return last

    def _click_autocomplete_option_contains(self, anchor: Locator, needle: str, *, allow_first_fallback: bool = True, debug_label: str = "") -> tuple[bool, str]:
        """Click visible option that contains needle (case-insensitive). Returns (success, clicked_text)."""
        needle_cf = (needle or "").strip().casefold()

        try:
            result = self.page.evaluate(
                """
                ({input, needle, allowFirst}) => {
                  const isVisible = (el) => {
                    if (!el) return false;
                    const cs = window.getComputedStyle(el);
                    if (!cs) return false;
                    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
                    const r = el.getBoundingClientRect();
                    return !!r && r.width > 5 && r.height > 5 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
                  };

                  const norm = (s) => (s || '').replace(/\\s+/g,' ').trim();
                  const ncf = (needle || '').toLowerCase();

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
                    .map(el => ({ el, r: el.getBoundingClientRect(), t: norm(el.innerText || el.textContent || '') }))
                    .filter(x => x.t.length > 0);

                  if (!uniq.length) return {success: false, reason: 'no_visible_options', count: 0};

                  // Smart matching: prioritize exact > starts-with > contains
                  let pick = [];
                  let matchType = 'none';
                  if (ncf) {
                    // 1. Try exact match first (most precise)
                    const exactMatches = uniq.filter(x => x.t.toLowerCase() === ncf);
                    if (exactMatches.length > 0) {
                      pick = exactMatches;
                      matchType = 'exact';
                    } else {
                      // 2. Try starts-with match (second priority)
                      const startsWithMatches = uniq.filter(x => x.t.toLowerCase().startsWith(ncf));
                      if (startsWithMatches.length > 0) {
                        pick = startsWithMatches;
                        matchType = 'starts_with';
                      } else {
                        // 3. Fall back to contains match (lowest priority)
                        const containsMatches = uniq.filter(x => x.t.toLowerCase().includes(ncf));
                        if (containsMatches.length > 0) {
                          pick = containsMatches;
                          matchType = 'contains';
                        }
                      }
                    }
                  }

                  // If no matches and allowFirst is true, pick from all options
                  if (pick.length === 0 && allowFirst) {
                    pick = uniq;
                    matchType = 'fallback_first';
                  }

                  if (!pick.length) return {success: false, reason: 'no_matches', count: uniq.length, needle: ncf};

                  // Prefer topmost item (dropdown) to be stable
                  pick.sort((a,b) => (a.r.top - b.r.top) || (a.r.left - b.r.left));
                  const target = pick[0];

                  // Mark the target element for Playwright to click
                  const markerId = 'playwright-autocomplete-target-' + Date.now();
                  target.el.setAttribute('data-playwright-target', markerId);

                  return {
                    success: true,
                    clicked_text: target.t,
                    total_options: uniq.length,
                    match_type: matchType,
                    marker_id: markerId,
                    matched_needle: (matchType !== 'fallback_first' && matchType !== 'none')
                  };
                }
                """,
                {"input": anchor, "needle": needle_cf, "allowFirst": bool(allow_first_fallback)},
            )

            success = result.get("success", False)
            if not success:
                if debug_label:
                    reason = result.get("reason", "unknown")
                    logger.debug("%s: Selection failed - reason: %s", debug_label, reason)
                return (False, "")

            clicked_text = result.get("clicked_text", "")
            match_type = result.get("match_type", "unknown")
            marker_id = result.get("marker_id", "")

            if debug_label:
                logger.debug(
                    "%s: Found option '%s' (total: %d, match_type: %s)",
                    debug_label,
                    clicked_text,
                    result.get("total_options", 0),
                    match_type
                )

            # Click the marked element using Playwright (more reliable than JS events)
            if marker_id:
                try:
                    target_locator = self.page.locator(f"[data-playwright-target='{marker_id}']").first
                    if target_locator.count() > 0:
                        target_locator.click(timeout=2000)
                        self.page.wait_for_timeout(self.ui_delay_ms)
                        # Clean up the marker
                        try:
                            self.page.evaluate(f"document.querySelector('[data-playwright-target=\"{marker_id}\"]')?.removeAttribute('data-playwright-target')")
                        except Exception:
                            pass
                        if debug_label:
                            logger.debug("%s: Clicked using Playwright", debug_label)
                        return (True, clicked_text)
                    else:
                        if debug_label:
                            logger.debug("%s: Marked element not found", debug_label)
                        return (False, "")
                except Exception as e:
                    if debug_label:
                        logger.debug("%s: Playwright click failed: %s", debug_label, e)
                    return (False, "")

            return (False, "")
        except Exception as e:
            if debug_label:
                logger.debug("%s: Exception clicking autocomplete: %s", debug_label, e)
            return False

    def _autocomplete_pick(self, form: Locator, desired_text: str, *, query: str | None = None, save_html: bool = False) -> bool:
        """Type query and click option containing desired_text (dropdown rendered via portal/tooltip)."""
        inp = form.locator("css=input").first
        if not inp.count():
            logger.debug("Autocomplete input not found")
            return False

        desired = (desired_text or "").strip()
        q = (query or desired_text or "").strip()
        if not q:
            logger.debug("Empty query for autocomplete")
            return False

        debug_label = f"autocomplete(q='{q}',desired='{desired}')"

        try:
            inp.scroll_into_view_if_needed(timeout=1500)
        except Exception as e:
            logger.debug("Failed to scroll autocomplete input into view: %s", e)

        # keep focus on input, otherwise tooltip disappears
        try:
            inp.click(timeout=2000)
        except Exception:
            try:
                inp.click(force=True, timeout=2000)
            except Exception as e:
                logger.debug("Failed to click autocomplete input: %s", e)
                return False

        self.page.wait_for_timeout(self.ui_delay_ms)

        if save_html:
            self._save_html_context(f"before_type_{q.replace(' ', '_')}")

        # clear and type (type is more reliable than fill for triggering fetch)
        try:
            inp.fill("")
        except Exception:
            try:
                inp.press("Control+A")
                inp.press("Backspace")
            except Exception:
                pass

        # Try typing with delay first (more reliable for triggering autocomplete)
        try:
            inp.type(q, delay=35)
        except Exception as e:
            logger.debug("Type failed, trying fill: %s", e)
            try:
                inp.fill(q)
            except Exception as e2:
                logger.debug("Both type and fill failed: %s", e2)
                return False

        # open dropdown if needed
        try:
            inp.press("ArrowDown")
        except Exception:
            pass

        self.page.wait_for_timeout(self.ui_delay_ms + 200)

        if save_html:
            self._save_html_context(f"after_type_{q.replace(' ', '_')}_before_wait")

        visible_cnt = self._wait_autocomplete_options(inp, timeout_s=6.0, debug_label=debug_label)
        logger.debug("Autocomplete options visible=%d for query='%s' (desired='%s')", visible_cnt, q, desired)

        if visible_cnt <= 0:
            logger.debug("No autocomplete options appeared for query='%s'", q)

            if save_html:
                self._save_html_context(f"no_options_{q.replace(' ', '_')}")

            # Try one more time with just pressing ArrowDown
            try:
                inp.press("ArrowDown")
                self.page.wait_for_timeout(self.ui_delay_ms + 300)
                visible_cnt = self._wait_autocomplete_options(inp, timeout_s=2.0, debug_label=f"{debug_label}_retry")
                if visible_cnt <= 0:
                    return False
                logger.debug("Second attempt found %d options", visible_cnt)
            except Exception:
                return False

        if save_html:
            self._save_html_context(f"options_visible_{q.replace(' ', '_')}")

        # try click matching desired (contains), then fallback to first
        click_ok, clicked_text = self._click_autocomplete_option_contains(inp, desired, allow_first_fallback=True, debug_label=debug_label)
        if not click_ok:
            logger.debug("Failed to click autocomplete option (desired='%s')", desired)
            if save_html:
                self._save_html_context(f"click_failed_{q.replace(' ', '_')}")
            return False

        self.page.wait_for_timeout(self.ui_delay_ms + 250)

        # Verify selection was made by checking if input has a value
        final_value = ""
        try:
            final_value = inp.input_value() or ""
            if final_value.strip():
                logger.debug("Autocomplete selection successful: input='%s' (clicked='%s')", final_value, clicked_text)
                # Verify the selection matches what we wanted
                if desired and desired.lower() not in final_value.lower():
                    logger.warning("Autocomplete mismatch! Wanted '%s', got '%s' (clicked '%s')", desired, final_value, clicked_text)
                    if save_html:
                        self._save_html_context(f"mismatch_{q.replace(' ', '_')}")
            else:
                logger.warning("Autocomplete input still empty after clicking '%s'", clicked_text)
                if save_html:
                    self._save_html_context(f"empty_after_click_{q.replace(' ', '_')}")
        except Exception as e:
            logger.debug("Failed to verify autocomplete value: %s", e)

        # close leftovers (not Enter!)
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass
        self.page.wait_for_timeout(self.ui_delay_ms)
        return True

    # ---------------- select pick first ----------------
    def _select_pick_first(self, form: Locator) -> bool:
        btn = form.locator("css=div.MuiSelect-select[role='button']").first
        if not btn.count():
            return False

        menu_id = None
        try:
            menu_id = btn.get_attribute("aria-controls")
        except Exception:
            menu_id = None

        lb = self._open_listbox(btn, menu_id)
        if not lb:
            return False

        # Check if this is a multiselect (contains checkboxes) - skip if so
        try:
            has_checkboxes = lb.locator("input[type='checkbox']").count() > 0
            if has_checkboxes:
                logger.debug("Skipping multiselect dropdown (contains checkboxes)")
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
                self.page.wait_for_timeout(self.ui_delay_ms)
                return False
        except Exception:
            pass

        opts = lb.locator("[role='option']")
        for i in range(min(opts.count(), 50)):
            o = opts.nth(i)
            try:
                txt = _norm(o.inner_text() or "")
            except Exception:
                txt = ""
            if not txt:
                continue
            if self._click_best_effort(o):
                logger.debug("Select pick first: '%s'", txt)
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
                self.page.wait_for_timeout(self.ui_delay_ms)
                return True

        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass
        self.page.wait_for_timeout(self.ui_delay_ms)
        return False

    # ---------------- main seed fill ----------------
    def seed_fill_address(self, city: str = "Київ") -> None:
        """Fill minimal address so dependent fields appear. Tries residential complex first (auto-fills street/house)."""
        logger.info("Seed fill address: city=%s", city)

        scope = self._find_nav_scope("Адреса об'єкта")
        if scope is None:
            logger.warning("Address section not found")
            return

        self.expand_all_collapsibles(scope, max_rounds=10)
        self.page.wait_for_timeout(self.ui_delay_ms + 200)

        # Collect autocomplete forms in order (usually: city, street, house, ...)
        autos = self._list_autocomplete_forms(scope)

        # CITY form
        city_form = self._find_form_by_label_contains(scope, ["Місто", "Населений", "Населений пункт", "Город"])
        if not city_form and autos:
            city_form = autos[0]

        if city_form:
            ok = False
            # first try exact desired; then shorter queries to force options
            for q in [city, city[:2], city[:1]]:
                if self._autocomplete_pick(city_form, city, query=q, save_html=False):
                    ok = True
                    break
            if not ok:
                logger.warning("City autocomplete failed (desired=%s)", city)
            self.page.wait_for_timeout(self.ui_delay_ms + 600)
        else:
            logger.warning("City form not found")

        # refresh scope (after city selection UI may rerender)
        scope = self._find_nav_scope("Адреса об'єкта") or scope
        self.expand_all_collapsibles(scope, max_rounds=6)
        self.page.wait_for_timeout(self.ui_delay_ms + 200)

        autos = self._list_autocomplete_forms(scope)

        # Debug: Log all autocomplete field labels to find the complex field
        logger.debug("Found %d autocomplete forms after city selection", len(autos))
        for idx, auto_form in enumerate(autos):
            try:
                lbl = self._label_text_formcontrol(auto_form)
                logger.debug("  Autocomplete #%d: label='%s'", idx, lbl)
            except Exception:
                pass

        # Try RESIDENTIAL COMPLEX/NEW BUILDING first - auto-fills street, house, subway, etc.
        complex_form = self._find_form_by_label_contains(scope, [
            "Новобудова",  # New building (primary field on this site)
            "Житловий комплекс", "ЖК", "Жилой комплекс", "Котеджне містечко"
        ])

        # If not found by label, try second autocomplete (often complex is between city and street)
        if not complex_form and len(autos) >= 2:
            logger.debug("Complex not found by label, trying second autocomplete form")
            complex_form = autos[1]

        if complex_form:
            logger.debug("Found residential complex field, trying to fill")
            ok = False
            # Try triggering autocomplete with generic query first to see any options
            for query in ["а", " ", ""]:
                if self._autocomplete_pick(complex_form, "", query=query if query else "а", save_html=False):
                    ok = True
                    logger.info("Residential complex filled (generic query)")
                    break

            # If generic didn't work, try specific complex names
            if not ok:
                for complex_name in ["Comfort", "Paradise", "Riverside"]:
                    for q in [complex_name, complex_name[:3] if len(complex_name) > 3 else complex_name]:
                        if self._autocomplete_pick(complex_form, complex_name, query=q, save_html=False):
                            ok = True
                            logger.info("Residential complex filled: %s", complex_name)
                            break
                    if ok:
                        break

            if ok:
                # Complex selected - street, house, etc. should auto-fill
                self.page.wait_for_timeout(self.ui_delay_ms + 1000)
                logger.info("Seed fill address done (via residential complex)")
                return
            else:
                logger.debug("Residential complex field found but no options selected, falling back to street")

        # Fallback: STREET form (if no complex or complex failed)
        street_form = self._find_form_by_label_contains(scope, ["Вулиця", "Вул", "Улица", "Street"])
        if not street_form:
            # often second autocomplete after city
            if len(autos) >= 2:
                street_form = autos[1]
            elif autos:
                street_form = autos[-1]

        if street_form:
            ok = False
            for desired, q in [("Хрещатик", "Хрещатик"), ("Хрещатик", "Хр"), ("Хрещатик", "а")]:
                if self._autocomplete_pick(street_form, desired, query=q, save_html=False):
                    ok = True
                    break
            if not ok:
                logger.warning("Street autocomplete failed")
            self.page.wait_for_timeout(self.ui_delay_ms + 700)
        else:
            logger.debug("Street form not found (may be auto-filled by complex or not required)")

        # refresh again: house field may appear only after street
        scope = self._find_nav_scope("Адреса об'єкта") or scope
        self.expand_all_collapsibles(scope, max_rounds=6)
        self.page.wait_for_timeout(self.ui_delay_ms + 200)

        # HOUSE NUMBER
        deadline = time.time() + 14.0
        house_form: Optional[Locator] = None
        while time.time() < deadline:
            house_form = self._find_form_by_label_contains(scope, ["Номер будинку", "Будинок", "House"])
            if house_form:
                break
            self.page.wait_for_timeout(250)

        if not house_form:
            logger.debug("House number form not found (may be auto-filled by complex or not required)")
            logger.info("Seed fill address done")
            return

        # Check if house number already filled (by complex)
        try:
            inp = house_form.locator("css=input").first
            if inp.count() > 0:
                cur_val = inp.input_value() or ""
                if cur_val.strip():
                    logger.debug("House number already filled: '%s'", cur_val)
                    logger.info("Seed fill address done")
                    return
        except Exception:
            pass

        if house_form.locator("css=div.MuiSelect-select[role='button']").count():
            self._select_pick_first(house_form)
        else:
            ok = False
            for q in ["1", "10", "2"]:
                if self._autocomplete_pick(house_form, q, query=q, save_html=False):
                    ok = True
                    break
            if not ok:
                logger.warning("House autocomplete failed")

        self.page.wait_for_timeout(self.ui_delay_ms + 500)
        logger.info("Seed fill address done")
