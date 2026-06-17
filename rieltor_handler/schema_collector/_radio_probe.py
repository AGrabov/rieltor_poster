from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple

from playwright.sync_api import Locator

from setup_logger import setup_logger

from .helpers import _cf, _key4, _norm, _xpath_literal

logger = setup_logger(__name__)


class _RadioProbeMixin:
    # ---------------- RADIO probe ----------------
    def _preferred_radio_value(self, values: list[str]) -> str:
        for cand in ("Немає", "Нiмає", "Нет", "Ні", "No"):
            for v in values:
                if _cf(v) == _cf(cand):
                    return v
        return values[0] if values else ""

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

    def _collect_field_nodes_in_scope(self, scope: Locator) -> list[Locator]:
        nodes = scope.locator(
            "xpath=(.//div[contains(@class,'MuiFormControl-root') or contains(@class,'MuiTextField-root')]"
            " | .//label[contains(@class,'MuiFormControlLabel-root')][.//input[@type='checkbox' or @type='radio']])"
        )
        out: list[Locator] = []
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

    def _sig_node(self, node: Locator, title_fallback: str) -> tuple[str, str, str]:
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

    def _field_key_sig(self, nav: str, sec: str, lab: str, wid: str) -> str:
        return "||".join([_cf(nav), _cf(sec), _cf(lab), _cf(wid)])

    def _sig_node_keyed(self, node: Locator, title_fallback: str, nav: str) -> tuple[str, str, str, str]:
        sec, lab, wid = self._sig_node(node, title_fallback)
        return sec, lab, wid, self._field_key_sig(nav, sec, lab, wid)

    def _snapshot_scope(self, scope: Locator, title_fallback: str, nav: str) -> tuple[list[dict[str, Any]], Counter]:
        nodes = self._collect_field_nodes_in_scope(scope)
        ordered: list[dict[str, Any]] = []
        keys: list[str] = []
        for n in nodes:
            sec, lab, wid, k = self._sig_node_keyed(n, title_fallback, nav)
            if not lab or self._is_helper_text(lab):
                continue
            item = {
                "nav": _norm(nav),
                "section": _norm(sec),
                "label": _norm(lab),
                "widget": _norm(wid).casefold(),
                "key": k,
            }
            ordered.append(item)
            keys.append(k)
        return ordered, Counter(keys)

    def _radio_current_value(self, rg: Locator) -> str:
        labels = rg.locator("xpath=.//label[.//input[@type='radio']]")
        for i in range(labels.count()):
            label = labels.nth(i)
            try:
                inp = label.locator("css=input[type='radio']").first
                if inp.count() and inp.is_checked():
                    t = _norm(label.locator("css=span.MuiFormControlLabel-label").inner_text() or "")
                    return t
            except Exception:
                continue
        return ""

    def _radio_set_by_value(self, rg: Locator, value: str) -> bool:
        value_cf = _cf(value)
        labels = rg.locator("xpath=.//label[.//input[@type='radio']]")
        for i in range(labels.count()):
            label = labels.nth(i)
            try:
                t = _norm(label.locator("css=span.MuiFormControlLabel-label").inner_text() or "")
            except Exception:
                t = ""
            if _cf(t) == value_cf:
                return self._set_radio_option(label)
        return False

    def _radio_values(self, rg: Locator) -> list[str]:
        out: list[str] = []
        labels = rg.locator("xpath=.//label[.//input[@type='radio']]")
        for i in range(labels.count()):
            label = labels.nth(i)
            try:
                t = _norm(label.locator("css=span.MuiFormControlLabel-label").inner_text() or "")
            except Exception:
                t = ""
            if t:
                out.append(t)
        seen: set[str] = set()
        uniq: list[str] = []
        for t in out:
            k = _cf(t)
            if k in seen:
                continue
            seen.add(k)
            uniq.append(t)
        return uniq

    def _counter_delta(self, base: Counter, after: Counter) -> tuple[list[str], list[str]]:
        added: list[str] = []
        removed: list[str] = []
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
                    if (
                        box.locator(
                            "xpath=.//div[contains(@class,'MuiFormControl-root') or contains(@class,'MuiTextField-root')]"
                        ).count()
                        >= 2
                    ):
                        return box
            except Exception:
                pass
        return nav_scope

    def _find_select_form(self, scope: Locator, nav_title: str, section_cf: str, label_cf: str) -> Locator | None:
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

    def _cache_select_options_for_added(
        self, local_scope: Locator, nav_title: str, added: list[dict[str, Any]]
    ) -> None:
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
                a.setdefault("options", cached)
                logger.debug("Кеш added-select: влучання %s (opts=%d)", cache_key, len(cached))
                continue

            form = self._find_select_form(local_scope, nav_title, sec_cf, lab_cf)
            if not form:
                logger.debug(
                    "Форма added-select не знайдена: nav=%s section=%s label=%s",
                    nav_title,
                    sec_cf,
                    lab_cf,
                )
                continue

            logger.debug("Кеш added-select: промах %s", cache_key)
            opts, select_meta = self._collect_select_options(form)
            self._select_options_cache[cache_key] = opts
            a.setdefault("options", opts)
            # Merge select metadata if needed
            if select_meta:
                a.setdefault("meta", {}).update(select_meta)
            logger.debug("Added-select збережено в кеш: %s (opts=%d)", cache_key, len(opts))

    # ---------------- TOGGLE-BUTTON-GROUP probe ----------------
    # The site replaced radio groups with MUI ToggleButtonGroup. Conditional
    # fields that appear when a given option is chosen (e.g. "Опалення"="Є" →
    # "Тип опалення") must be probed the same way radios were, otherwise the
    # schema misses them.
    def _toggle_label_text(self, tg: Locator) -> str:
        form = tg.locator("xpath=ancestor::div[contains(@class,'MuiFormControl-root')][1]").first
        if form.count():
            t = self._label_text_formcontrol(form)
            if t:
                return _norm(t)
        return ""

    def _toggle_button_text(self, btn: Locator) -> str:
        try:
            lbl = btn.locator("css=span.MuiToggleButton-label").first
            if lbl.count():
                return _norm(lbl.inner_text() or "")
            return _norm(btn.inner_text() or "")
        except Exception:
            return ""

    def _toggle_values(self, tg: Locator) -> list[str]:
        out: list[str] = []
        btns = tg.locator("css=button.MuiToggleButton-root, button")
        for i in range(btns.count()):
            t = self._toggle_button_text(btns.nth(i))
            if t:
                out.append(t)
        seen: set[str] = set()
        uniq: list[str] = []
        for t in out:
            k = _cf(t)
            if k in seen:
                continue
            seen.add(k)
            uniq.append(t)
        return uniq

    def _toggle_current_value(self, tg: Locator) -> str:
        btns = tg.locator("css=button.MuiToggleButton-root, button")
        for i in range(btns.count()):
            b = btns.nth(i)
            try:
                pressed = (b.get_attribute("aria-pressed") == "true") or (
                    "Mui-selected" in (b.get_attribute("class") or "")
                )
            except Exception:
                pressed = False
            if pressed:
                return self._toggle_button_text(b)
        return ""

    def _toggle_set_by_value(self, tg: Locator, value: str) -> bool:
        value_cf = _cf(value)
        btns = tg.locator("css=button.MuiToggleButton-root, button")
        for i in range(btns.count()):
            b = btns.nth(i)
            if _cf(self._toggle_button_text(b)) != value_cf:
                continue
            self._click_best_effort(b)
            self.page.wait_for_timeout(self.ui_delay_ms + 250)
            try:
                return (b.get_attribute("aria-pressed") == "true") or (
                    "Mui-selected" in (b.get_attribute("class") or "")
                )
            except Exception:
                return True
        return False

    _NEGATIVE_OPTION_CF = {_cf(x) for x in ("Немає", "Нiмає", "Нет", "Ні", "No")}

    def _is_probeable_toggle(self, values: list[str]) -> bool:
        """Чи варто зондувати цю toggle-групу на умовні поля.

        Зондуємо лише бінарні/малі групи на кшталт Є/Немає, Так/Ні — саме вони
        відкривають умовні поля ('Тип опалення' тощо). Великі мультивибірні групи
        удобств ('У будинку є', 'На ділянці є') та лічильники ('Число кімнат')
        умовних полів не дають, тож пропускаємо їх — інакше зонд марно клікає
        десятки кнопок (і кидає 'Встановлення toggle не вдалося' на мультивиборі).
        """
        has_negative = any(_cf(v) in self._NEGATIVE_OPTION_CF for v in values)
        if has_negative:
            return True
        return len(values) <= 3

    def probe_toggles_dynamic(self) -> list[dict[str, Any]]:
        """Зондувати MUI ToggleButtonGroup так само, як radio: для кожного варіанта
        фіксувати поля, що з'являються/зникають (visible_when)."""
        if not self.enable_radio_probe:
            return []

        logger.info("Початок зондування груп-кнопок (ToggleButtonGroup)")
        self.open_all_blocks_sticky()
        nav_items = self.list_navigation_items()
        results: list[dict[str, Any]] = []

        for title, occ in nav_items:
            if title in self._NAV_EXCLUDE_FIELDS:
                continue
            scope = self._scope_for_nav_item(title, occ)
            if scope is None:
                continue

            logger.debug("Зондування toggle nav: %s", title)
            self.page.wait_for_timeout(self.ui_delay_ms)
            self.expand_all_collapsibles(scope, max_rounds=8)
            self.page.wait_for_timeout(self.ui_delay_ms)

            tgs = scope.locator("css=div.MuiToggleButtonGroup-root")
            tg_count = tgs.count()
            logger.debug("ToggleButtonGroups у '%s': %d", title, tg_count)

            # 1) baseline: set all to preferred (usually 'Немає') to start from a clean state
            for i in range(tg_count):
                tg = tgs.nth(i)
                values = self._toggle_values(tg)
                if len(values) <= 1:
                    continue
                values_cf = {_cf(v) for v in values}
                if _cf("Гараж") in values_cf and _cf("Паркомісце") in values_cf:
                    continue  # parking subtype — handled by select_parking_type
                if not self._is_probeable_toggle(values):
                    continue  # multi-select amenities / counters — no conditional fields
                pref = self._preferred_radio_value(values)
                cur = self._toggle_current_value(tg)
                if pref and _cf(cur) != _cf(pref):
                    self._toggle_set_by_value(tg, pref)
                    self.page.wait_for_timeout(self.ui_delay_ms)

            # 2) probe each group
            tgs = scope.locator("css=div.MuiToggleButtonGroup-root")
            tg_count = tgs.count()

            for i in range(tg_count):
                tg = tgs.nth(i)
                values = self._toggle_values(tg)
                if len(values) <= 1:
                    continue
                values_cf = {_cf(v) for v in values}
                if _cf("Гараж") in values_cf and _cf("Паркомісце") in values_cf:
                    logger.debug("Пропускаємо toggle вибору типу паркінгу")
                    continue
                if not self._is_probeable_toggle(values):
                    logger.debug("Пропускаємо мультивибірну/лічильну toggle-групу (умовних полів немає)")
                    continue

                label = self._toggle_label_text(tg)
                if not label or self._is_helper_text(label):
                    continue

                host_section = self._nearest_h6_title(tg) or title
                baseline_val = self._toggle_current_value(tg)
                pref = self._preferred_radio_value(values)
                if pref and _cf(baseline_val) != _cf(pref):
                    self._toggle_set_by_value(tg, pref)
                    self.page.wait_for_timeout(self.ui_delay_ms)
                    baseline_val = self._toggle_current_value(tg)

                local_scope = self._local_probe_scope_for_rg(tg, scope, title)
                self.expand_all_collapsibles(local_scope, max_rounds=6)
                self.page.wait_for_timeout(self.ui_delay_ms)

                base_ordered, base_counter = self._snapshot_scope(local_scope, title, title)
                by_key_base = {it["key"]: it for it in base_ordered}

                controller_field_key = _key4(title, host_section, label, "radio")
                group_info: dict[str, Any] = {
                    "nav": title,
                    "section": host_section,
                    "label": label,
                    "widget": "radio",
                    "controller_field_key": controller_field_key,
                    "controller_ord": i,
                    "baseline_value": baseline_val,
                    "baseline_fields_count": int(sum(base_counter.values())),
                    "options": [],
                }

                any_change = False
                for v in values:
                    if _cf(v) == _cf(baseline_val):
                        group_info["options"].append(
                            {"value": v, "select_failed": False, "added": [], "removed": []}
                        )
                        continue

                    ok = self._toggle_set_by_value(tg, v)
                    if not ok:
                        logger.debug("Встановлення toggle не вдалося: %s=%s", label, v)
                        group_info["options"].append(
                            {"value": v, "select_failed": True, "added": [], "removed": []}
                        )
                        continue

                    self.page.wait_for_timeout(self.ui_delay_ms + 450)
                    self.expand_all_collapsibles(local_scope, max_rounds=6)
                    self.page.wait_for_timeout(self.ui_delay_ms)

                    after_ordered, after_counter = self._snapshot_scope(local_scope, title, title)
                    by_key_after = {it["key"]: it for it in after_ordered}
                    add_keys, rem_keys = self._counter_delta(base_counter, after_counter)

                    added = [
                        by_key_after.get(k, {"nav": title, "section": "", "label": "", "widget": "", "key": k})
                        for k in add_keys
                    ]
                    removed = [
                        by_key_base.get(k, {"nav": title, "section": "", "label": "", "widget": "", "key": k})
                        for k in rem_keys
                    ]
                    if added or removed:
                        any_change = True

                    try:
                        self._cache_select_options_for_added(local_scope, title, added)
                    except Exception as e:
                        logger.debug("Кешування варіантів select для added (toggle) не вдалося: %s", e)

                    group_info["options"].append(
                        {"value": v, "select_failed": False, "added": added, "removed": removed}
                    )
                    logger.debug("Toggle diff: %s=%s додано=%d видалено=%d", label, v, len(added), len(removed))

                    if baseline_val:
                        self._toggle_set_by_value(tg, baseline_val)
                        self.page.wait_for_timeout(self.ui_delay_ms + 250)

                if baseline_val:
                    self._toggle_set_by_value(tg, baseline_val)
                    self.page.wait_for_timeout(self.ui_delay_ms)

                if any_change:
                    results.append(group_info)

        logger.info("Зондування груп-кнопок завершено: груп=%d", len(results))
        return results

    def probe_radios_dynamic(self) -> list[dict[str, Any]]:
        if not self.enable_radio_probe:
            logger.info("Зондування радіокнопок вимкнено")
            return []

        logger.info("Початок зондування радіокнопок")
        self.open_all_blocks_sticky()
        nav_items = self.list_navigation_items()
        results: list[dict[str, Any]] = []

        for title, occ in nav_items:
            if title in self._NAV_EXCLUDE_FIELDS:
                continue
            scope = self._scope_for_nav_item(title, occ)
            if scope is None:
                continue

            logger.debug("Зондування radio nav: %s", title)
            self.page.wait_for_timeout(self.ui_delay_ms)
            self.expand_all_collapsibles(scope, max_rounds=8)
            self.page.wait_for_timeout(self.ui_delay_ms)

            # 1) baseline: set all radios to preferred (usually 'Немає')
            rgs = scope.locator("xpath=.//*[@role='radiogroup']")
            rg_count = rgs.count()
            logger.debug("Radiogroups у '%s': %d", title, rg_count)

            for i in range(rg_count):
                rg = rgs.nth(i)
                values = self._radio_values(rg)
                if len(values) <= 1:
                    continue
                # Skip parking type selector radio (Гараж/Паркомісце) - handled by select_parking_type
                values_cf = {_cf(v) for v in values}
                if _cf("Гараж") in values_cf and _cf("Паркомісце") in values_cf:
                    continue
                pref = self._preferred_radio_value(values)
                if pref:
                    self._radio_set_by_value(rg, pref)
                    self.page.wait_for_timeout(self.ui_delay_ms)

            # 2) probe each radiogroup
            rgs = scope.locator("xpath=.//*[@role='radiogroup']")
            rg_count = rgs.count()

            for i in range(rg_count):
                rg = rgs.nth(i)
                values = self._radio_values(rg)
                if len(values) <= 1:
                    continue

                # Skip parking type selector radio (Гараж/Паркомісце) - handled by select_parking_type
                values_cf = {_cf(v) for v in values}
                if _cf("Гараж") in values_cf and _cf("Паркомісце") in values_cf:
                    logger.debug("Пропускаємо radiogroup вибору типу паркінгу")
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
                by_key_base: dict[str, dict[str, Any]] = {it["key"]: it for it in base_ordered}

                controller_field_key = _key4(title, host_section, label, "radio")

                group_info: dict[str, Any] = {
                    "nav": title,
                    "section": host_section,
                    "label": label,
                    "widget": "radio",
                    "controller_field_key": controller_field_key,
                    "controller_ord": i,
                    "baseline_value": baseline_val,
                    "baseline_fields_count": int(sum(base_counter.values())),
                    "options": [],
                }

                any_change = False

                for v in values:
                    if _cf(v) == _cf(baseline_val):
                        group_info["options"].append(
                            {
                                "value": v,
                                "select_failed": False,
                                "added": [],
                                "removed": [],
                            }
                        )
                        continue

                    ok = self._radio_set_by_value(rg, v)
                    if not ok:
                        logger.debug("Встановлення radio не вдалося: %s=%s", label, v)
                        group_info["options"].append(
                            {
                                "value": v,
                                "select_failed": True,
                                "added": [],
                                "removed": [],
                            }
                        )
                        continue

                    self.page.wait_for_timeout(self.ui_delay_ms + 450)
                    self.expand_all_collapsibles(local_scope, max_rounds=6)
                    self.page.wait_for_timeout(self.ui_delay_ms)

                    after_ordered, after_counter = self._snapshot_scope(local_scope, title, title)
                    by_key_after: dict[str, dict[str, Any]] = {it["key"]: it for it in after_ordered}

                    add_keys, rem_keys = self._counter_delta(base_counter, after_counter)

                    added = [
                        by_key_after.get(
                            k,
                            {
                                "nav": title,
                                "section": "",
                                "label": "",
                                "widget": "",
                                "key": k,
                            },
                        )
                        for k in add_keys
                    ]
                    removed = [
                        by_key_base.get(
                            k,
                            {
                                "nav": title,
                                "section": "",
                                "label": "",
                                "widget": "",
                                "key": k,
                            },
                        )
                        for k in rem_keys
                    ]

                    if added or removed:
                        any_change = True

                    try:
                        self._cache_select_options_for_added(local_scope, title, added)
                    except Exception as e:
                        logger.debug("Кешування варіантів select для added не вдалося: %s", e)

                    group_info["options"].append(
                        {
                            "value": v,
                            "select_failed": False,
                            "added": added,
                            "removed": removed,
                        }
                    )
                    logger.debug(
                        "Radio diff: %s=%s додано=%d видалено=%d",
                        label,
                        v,
                        len(added),
                        len(removed),
                    )

                    if baseline_val:
                        self._radio_set_by_value(rg, baseline_val)
                        self.page.wait_for_timeout(self.ui_delay_ms + 250)

                if baseline_val:
                    self._radio_set_by_value(rg, baseline_val)
                    self.page.wait_for_timeout(self.ui_delay_ms)

                if any_change:
                    results.append(group_info)

        logger.info("Зондування радіокнопок завершено: груп=%d", len(results))
        return results
