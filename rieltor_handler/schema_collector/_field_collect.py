from __future__ import annotations

from typing import Any, Dict, List, Tuple

from setup_logger import setup_logger
logger = setup_logger(__name__)

from playwright.sync_api import Locator

from .helpers import (FieldInfo, _norm, _sig3, _key4)


class _FieldCollectMixin:
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
        """Collect standalone checkboxes and radios (not inside radiogroups)."""
        out: List[FieldInfo] = []

        # Find all MuiFormControlLabel-root labels with checkbox/radio inputs
        labels = scope.locator(
            "xpath=.//label[contains(@class,'MuiFormControlLabel-root')][.//input[@type='checkbox' or @type='radio']]"
        )
        label_count = labels.count()
        logger.debug("Found %d MuiFormControlLabel-root labels in %s", label_count, nav_title)

        for i in range(label_count):
            lab = labels.nth(i)
            try:
                if not lab.is_visible():
                    logger.debug("Label %d not visible, skipping", i)
                    continue
            except Exception:
                pass

            # Skip if inside a radiogroup (those are collected separately)
            if lab.locator("xpath=ancestor::*[@role='radiogroup'][1]").count():
                logger.debug("Label %d is inside radiogroup, skipping", i)
                continue

            txt = self._label_text_labelcontrol(lab)
            if not txt:
                logger.debug("Label %d has no text, skipping", i)
                continue

            inp = lab.locator("css=input").first
            itype = ""
            checked = False
            aria_label = ""
            try:
                itype = (inp.get_attribute("type") or "").casefold()
                checked = bool(inp.is_checked())
                aria_label = _norm(inp.get_attribute("aria-label") or "")
            except Exception:
                itype = "checkbox"  # Default to checkbox if can't determine

            widget = "checkbox" if itype == "checkbox" else "radio"
            meta = {"standalone": True, "field_index": i}
            if checked:
                meta["checked"] = True
            if aria_label:
                meta["aria_label"] = aria_label

            logger.debug("Found standalone %s: '%s' (section=%s)", widget, txt, self._nearest_h6_title(lab) or nav_title)

            out.append(
                FieldInfo(
                    nav=nav_title,
                    section=self._nearest_h6_title(lab) or nav_title,
                    label=txt,
                    widget=widget,
                    required=False,
                    options=[],
                    meta=meta,
                )
            )

        # Also try to find standalone checkboxes in MuiCheckbox-root spans (alternative MUI structure)
        checkboxes = scope.locator(
            "xpath=.//label[contains(@class,'MuiFormControlLabel-root')][.//span[contains(@class,'MuiCheckbox-root')]]"
        )
        checkbox_count = checkboxes.count()
        logger.debug("Found %d MuiCheckbox-root labels in %s", checkbox_count, nav_title)

        already_found = {f.label.casefold() for f in out}

        for i in range(checkbox_count):
            lab = checkboxes.nth(i)
            try:
                if not lab.is_visible():
                    continue
            except Exception:
                pass

            # Skip if inside a radiogroup
            if lab.locator("xpath=ancestor::*[@role='radiogroup'][1]").count():
                continue

            txt = self._label_text_labelcontrol(lab)
            if not txt or txt.casefold() in already_found:
                continue

            # Check if it's actually a checkbox (not radio)
            checkbox_span = lab.locator("xpath=.//span[contains(@class,'MuiCheckbox-root')]").first
            if not checkbox_span.count():
                continue

            checked = False
            try:
                inp = lab.locator("css=input").first
                if inp.count():
                    checked = bool(inp.is_checked())
            except Exception:
                pass

            meta = {"standalone": True, "field_index": len(out)}
            if checked:
                meta["checked"] = True

            logger.debug("Found MuiCheckbox standalone: '%s' (section=%s)", txt, self._nearest_h6_title(lab) or nav_title)

            out.append(
                FieldInfo(
                    nav=nav_title,
                    section=self._nearest_h6_title(lab) or nav_title,
                    label=txt,
                    widget="checkbox",
                    required=False,
                    options=[],
                    meta=meta,
                )
            )

        logger.debug("Total standalone controls in %s: %d", nav_title, len(out))
        return out

    def _collect_fields_in_scope(self, scope: Locator, nav_title: str) -> List[FieldInfo]:
        out: List[FieldInfo] = []

        logger.debug("Collect fields in scope: %s", nav_title)
        forms = self._collect_forms_in_scope(scope)
        logger.debug("Found forms: %d", len(forms))

        for idx, form in enumerate(forms):
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
            placeholder = ""
            aria_label = ""
            current_value = ""

            if not label:
                try:
                    inp = form.locator("css=input, textarea").first
                    if inp.count():
                        aria_label = _norm(inp.get_attribute("aria-label") or "")
                        placeholder = _norm(inp.get_attribute("placeholder") or "")
                        label = aria_label or placeholder
                except Exception:
                    pass

            # Collect additional metadata for field identification
            try:
                inp = form.locator("css=input, textarea").first
                if inp.count():
                    if not placeholder:
                        placeholder = _norm(inp.get_attribute("placeholder") or "")
                    if not aria_label:
                        aria_label = _norm(inp.get_attribute("aria-label") or "")
                    try:
                        current_value = _norm(inp.input_value() or "")
                    except Exception:
                        pass

                    # HTML attributes for stable targeting
                    try:
                        name_attr = inp.get_attribute("name") or ""
                        if name_attr:
                            meta["name"] = name_attr
                    except Exception:
                        pass

                    try:
                        id_attr = inp.get_attribute("id") or ""
                        if id_attr:
                            meta["id"] = id_attr
                    except Exception:
                        pass

                    # Validation constraints
                    try:
                        pattern = inp.get_attribute("pattern") or ""
                        if pattern:
                            meta["pattern"] = pattern
                    except Exception:
                        pass

                    try:
                        minlength = inp.get_attribute("minlength") or ""
                        if minlength:
                            meta["minlength"] = int(minlength)
                    except Exception:
                        pass

                    try:
                        maxlength = inp.get_attribute("maxlength") or ""
                        if maxlength:
                            meta["maxlength"] = int(maxlength)
                    except Exception:
                        pass

                    try:
                        min_val = inp.get_attribute("min") or ""
                        if min_val:
                            meta["min"] = min_val
                    except Exception:
                        pass

                    try:
                        max_val = inp.get_attribute("max") or ""
                        if max_val:
                            meta["max"] = max_val
                    except Exception:
                        pass

                    try:
                        step = inp.get_attribute("step") or ""
                        if step:
                            meta["step"] = step
                    except Exception:
                        pass

                    # Field state
                    try:
                        meta["disabled"] = inp.is_disabled()
                    except Exception:
                        pass

                    try:
                        readonly = inp.get_attribute("readonly")
                        if readonly is not None:
                            meta["readonly"] = True
                    except Exception:
                        pass
            except Exception:
                pass

            # Helper text (MUI form helper text)
            try:
                helper = form.locator("css=p.MuiFormHelperText-root").first
                if helper.count():
                    helper_text = _norm(helper.inner_text() or "")
                    if helper_text:
                        meta["helper_text"] = helper_text
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

            # Add identifying metadata
            if placeholder:
                meta["placeholder"] = placeholder
            if aria_label:
                meta["aria_label"] = aria_label
            if current_value:
                meta["current_value"] = current_value
            meta["field_index"] = idx

            # Lazy select options collection with caching
            if widget == "select":
                cache_key = _key4(nav_title, section, label, "select")
                cached = self._select_options_cache.get(cache_key)
                if cached is not None:
                    options = cached
                    logger.debug("Select cache hit: %s", cache_key)
                else:
                    logger.debug("Select cache miss: %s", cache_key)
                    options, select_meta = self._collect_select_options(form)
                    self._select_options_cache[cache_key] = options
                    # Merge select metadata (is_multiselect, etc.)
                    for k, v in select_meta.items():
                        meta[k] = v

            # Lazy autocomplete options collection with caching
            if widget in ("text_autocomplete", "autocomplete_multi"):
                cache_key = _key4(nav_title, section, label, widget)
                cached = self._select_options_cache.get(cache_key)
                if cached is not None:
                    options = cached
                    logger.debug("Autocomplete cache hit: %s", cache_key)
                else:
                    logger.debug("Autocomplete cache miss: %s", cache_key)
                    try:
                        options = self._collect_autocomplete_options(form)
                        self._select_options_cache[cache_key] = options
                    except Exception as e:
                        logger.warning("Failed to collect autocomplete options for %s: %s", label, e)
                        options = []

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
        """Collect schema by traversing current page.

        Dedupe strategy:
          1) strict key4 (nav, section, label, widget)
          2) cross-nav key sig3 (section, label, widget) with meta.navs
        """
        self.open_all_blocks_sticky()

        nav_items = self.list_navigation_items()
        all_fields: List[FieldInfo] = []

        logger.info("Collect schema: nav_items=%d", len(nav_items))

        for title, occ in nav_items:
            if title in self._NAV_EXCLUDE_FIELDS:
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

        # 1) strict dedupe by key4
        by_key4: Dict[Tuple[str, str, str, str], FieldInfo] = {}
        order4: List[Tuple[str, str, str, str]] = []
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

        # 2) cross-nav merge by sig3
        by_sig: Dict[str, FieldInfo] = {}
        order_sig: List[str] = []
        merged_cross_nav = 0

        for f in uniq4:
            sig = _sig3(f.section, f.label, f.widget)
            ex = by_sig.get(sig)
            if ex is None:
                f.meta = dict(f.meta or {})
                f.meta["navs"] = [f.nav]
                by_sig[sig] = f
                order_sig.append(sig)
                continue

            merged_cross_nav += 1
            ex.required = bool(ex.required or f.required)
            ex.options = _merge_opts(ex.options or [], f.options or [])
            for mk, mv in (f.meta or {}).items():
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
            "navigation": [t for (t, _) in nav_items if t not in self._NAV_EXCLUDE_FROM_LIST],
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
