from __future__ import annotations

from typing import Sequence

from playwright.sync_api import Locator, Page

from setup_logger import setup_logger

logger = setup_logger(__name__)


class FieldsMixin:
    # -------- internal helpers --------
    def _active_listbox(self, *, prefer_menu_id: str | None = None) -> Locator | None:
        """Return the currently opened MUI listbox.

        MUI Popover/Select renders the listbox outside the form.
        We try to scope it via aria-controls menu id (best), otherwise pick the last visible listbox.
        """
        try:
            if prefer_menu_id:
                # Use XPath to avoid CSS escaping issues for dynamic ids.
                lit = self._xpath_literal(prefer_menu_id)
                lb = self.page.locator(f"xpath=//div[@id={lit}]//*[@role='listbox']").first
                if lb.count():
                    lb.wait_for(state="visible", timeout=2500)
                    return lb
        except Exception:
            pass

        try:
            lb = self.page.locator("css=[role='listbox']:visible").last
            if lb.count():
                lb.wait_for(state="visible", timeout=2500)
                return lb
        except Exception:
            pass

        try:
            lb = self.page.locator("[role='listbox']").last
            if lb.count():
                return lb
        except Exception:
            pass
        return None

    @staticmethod
    def _norm_text(s: str) -> str:
        return " ".join((s or "").replace("\xa0", " ").split()).strip()

    def _find_option_in_listbox(self, listbox: Locator, desired: str) -> Locator | None:
        desired_n = self._norm_text(desired)
        if not desired_n:
            return None

        # 1) exact match (normalized)
        opts = listbox.locator("[role='option']")
        for i in range(opts.count()):
            o = opts.nth(i)
            try:
                t = self._norm_text(o.inner_text())
            except Exception:
                continue
            if t == desired_n:
                return o

        # 2) contains match
        o = opts.filter(has_text=desired_n).first
        return o if o.count() else None

    # -------- buttons / toggles --------
    def _click_box_button_in_section(self, root: Locator, section_h6: str, text: str) -> None:
        sec = self._section(root, section_h6)
        target = (text or "").strip().casefold()
        logger.info("Select button in '%s': %s", section_h6, target)

        if not target:
            return

        cards = sec.locator("xpath=.//div[contains(@class,'MuiBox-root')][.//img[@alt] and .//span]")

        chosen = None
        for i in range(cards.count()):
            c = cards.nth(i)

            alt = ""
            try:
                alt = (c.locator("css=img[alt]").first.get_attribute("alt") or "").strip().casefold()
            except Exception:
                pass

            span_txt = ""
            try:
                span_txt = " ".join([t.strip() for t in c.locator("css=span").all_inner_texts() if t.strip()]).casefold()
            except Exception:
                pass

            if (alt and target in alt) or (span_txt and target in span_txt):
                chosen = c
                break

        if not chosen:
            logger.warning("Button not found in section '%s' for text '%s'", section_h6, target)
            return

        try:
            cls = (chosen.get_attribute("class") or "")
            if "-selected" in cls:
                logger.info("Box already selected in '%s' for '%s' (skip)", section_h6, target)
                return
        except Exception:
            pass

        try:
            chosen.scroll_into_view_if_needed()
        except Exception:
            pass

        try:
            chosen.click()
        except Exception:
            chosen.click(force=True)



    def _click_section_toggle(self, root: Locator, section_h6: str) -> None:
        sec = self._section(root, section_h6)
        logger.info("Toggle section: %s", section_h6)
        try:
            sec.locator("xpath=.//h6").first.click()
        except Exception:
            try:
                sec.click()
            except Exception:
                pass


    # -------- inputs/selects --------
    def _fill_by_label(self, root: Locator, section: str, key: str, value: str) -> None:
        sec = self._section(root, section)
        label = self._expected_label(key) or key
        desired = ("" if value is None else str(value)).strip()
        if not desired:
            return

        ctrl = self._find_control_by_label(sec, label)
        if not ctrl:
            logger.warning("Control not found for key=%s (label=%s) in section=%s", key, label, section)
            return

        try:
            tag = ctrl.evaluate("el => el.tagName.toLowerCase()")
        except Exception:
            tag = None

        if tag not in ("input", "textarea"):
            inner = ctrl.locator("css=input, textarea").first
            if inner.count():
                ctrl = inner

        # SKIP только если уже стоит то же самое
        try:
            cur = (ctrl.input_value() or "").strip()
        except Exception:
            cur = ""

        if cur == desired:
            logger.info("Fill skip %s/%s: already '%s'", section, key, cur)
            self._mark_touched(ctrl)
            return

        logger.info("Fill %s/%s = %s (was='%s')", section, key, desired, cur)

        try:
            ctrl.click()
        except Exception:
            pass

        try:
            ctrl.fill(desired)
        except Exception:
            try:
                ctrl.press("Control+A")
                ctrl.press("Backspace")
                ctrl.type(desired, delay=20)
            except Exception:
                logger.exception("Failed to fill %s/%s", section, key)
                return

        # verify (best-effort)
        try:
            after = (ctrl.input_value() or "").strip()
            if after != desired:
                logger.warning("Fill not confirmed %s/%s: desired='%s' current='%s'", section, key, desired, after)
        except Exception:
            pass

        self._mark_touched(ctrl)


    def _fill_select_or_text(self, root: Locator, section: str, key: str, value: str) -> None:
        sec = self._section(root, section)
        label = self._expected_label(key) or key
        desired = ("" if value is None else str(value)).strip()

        # 0) radio-group?
        form_rg = self._find_formcontrol_by_label(sec, label)
        if form_rg and self._try_fill_radio_group(form_rg, section, key, desired):
            logger.debug("Fill radio-group %s/%s = %s", section, key, desired)
            return

        # 1) locate control by label
        ctrl = self._find_control_by_label(sec, label)

        # fallback: sometimes label is empty (rare for selects, but happens)
        if not ctrl:
            # if there is exactly one MUI select in section, use it
            one_select = sec.locator("css=div.MuiSelect-select[role='button']").first
            if one_select.count():
                ctrl = one_select
            else:
                logger.warning("Select/text control not found for key=%s (label=%s) in section=%s", key, label, section)
                return

        # unwrap to formcontrol/select button if possible
        try:
            form = ctrl.locator("xpath=ancestor::div[contains(@class,'MuiFormControl-root')][1]").first
        except Exception:
            form = sec.locator("css=div.MuiFormControl-root").first

        select_btn = None

        # ctrl itself could be the select button
        try:
            role = ctrl.get_attribute("role")
            cls = (ctrl.get_attribute("class") or "")
            if role == "button" and "MuiSelect-select" in cls:
                select_btn = ctrl
        except Exception:
            pass

        if not select_btn:
            select_btn = form.locator("css=div.MuiSelect-select[role='button']").first

        # ---------- MUI Select ----------
        if select_btn and select_btn.count():
            if not desired:
                logger.info("Select skip %s/%s: empty value", section, key)
                return

            try:
                cur = (select_btn.inner_text() or "").strip()
            except Exception:
                cur = ""

            if cur == desired:
                logger.info("Select skip %s/%s: already '%s'", section, key, cur)
                try:
                    self._mark_touched(form)
                except Exception:
                    pass
                return

            logger.info("Select %s/%s -> %s", section, label, desired)

            # Capture menu id to reliably choose the correct listbox
            menu_id = None
            try:
                menu_id = select_btn.get_attribute("aria-controls")
            except Exception:
                menu_id = None

            # Open menu (robust)
            try:
                select_btn.scroll_into_view_if_needed()
            except Exception:
                pass

            opened_lb = None
            for _ in range(2):
                # primary click
                try:
                    select_btn.click(timeout=1500)
                except Exception:
                    try:
                        select_btn.click(force=True, timeout=1500)
                    except Exception:
                        pass

                opened_lb = self._active_listbox(prefer_menu_id=menu_id)
                if opened_lb:
                    break

                # fallback: click arrow icon
                try:
                    form.locator("css=svg.MuiSelect-icon").first.click(timeout=1200)
                except Exception:
                    pass

                opened_lb = self._active_listbox(prefer_menu_id=menu_id)
                if opened_lb:
                    break

                # fallback: click input root
                try:
                    form.locator("css=.MuiInputBase-root").first.click(timeout=1200)
                except Exception:
                    pass

                opened_lb = self._active_listbox(prefer_menu_id=menu_id)
                if opened_lb:
                    break

                # keyboard fallback
                try:
                    select_btn.press("Enter")
                except Exception:
                    try:
                        select_btn.press("Space")
                    except Exception:
                        pass

                opened_lb = self._active_listbox(prefer_menu_id=menu_id)
                if opened_lb:
                    break

            lb = opened_lb or self._active_listbox(prefer_menu_id=menu_id)
            if not lb:
                logger.warning("Select listbox not opened for %s/%s", section, key)
                return

            opt = self._find_option_in_listbox(lb, desired)
            if not opt:
                logger.warning("Option '%s' not found for %s/%s", desired, section, key)
                try:
                    texts = self._list_listbox_options(lb)
                    logger.debug("Select available options for %s/%s: %s", section, key, texts)
                except Exception:
                    pass
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
                return

            try:
                opt.click()
            finally:
                try:
                    self._mark_touched(form)
                except Exception:
                    pass
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
            return

        # ---------- Plain input / textarea ----------
        self._fill_by_label(root, section, key, desired)



    def _list_radio_options(self, form: Locator) -> list[str]:
        """Вернуть список всех доступных текстов радиокнопок внутри формы."""
        options = []
        try:
            # Ищем все label'ы внутри формы
            labels = form.locator("xpath=.//label").all()
            for lbl in labels:
                try:
                    text = lbl.inner_text().strip()
                    if text:
                        options.append(text)
                except Exception:
                    continue
        except Exception:
            pass
        logger.info("Radio options: %s", options)
        return options

    def _try_fill_radio_group(self, form: Locator, section: str, key: str, value: str) -> bool:
        desired = (value or "").strip()
        if not desired:
            return False

        try:
            radios = form.locator("css=input[type='radio']")
            if radios.count() == 0:
                return False
        except Exception:
            return False

        lit = self._xpath_literal(desired)

        # SKIP — уже выбран
        try:
            already = form.locator(
                f"xpath=.//*[(.//input[@type='radio' and @checked]) and contains(normalize-space(.), {lit})]"
            ).count() > 0
        except Exception:
            already = False

        if already:
            logger.info("Radio skip %s/%s: already '%s'", section, key, desired)
            self._mark_touched(form)
            return True

        logger.info("Radio %s/%s -> %s", section, key, desired)

        opt = form.locator(f"xpath=.//label[contains(normalize-space(.), {lit})]").first
        if opt.count() == 0:
            opt = form.locator(f"xpath=.//*[contains(normalize-space(.), {lit})]").first

        if opt.count() == 0:
            try:
                texts = [t.strip() for t in form.all_inner_texts() if t.strip()]
                logger.debug("Radio available texts for %s/%s: %s", section, key, texts)
            except Exception:
                pass
            logger.warning("Radio option not found for %s/%s desired='%s'", section, key, desired)
            self._mark_touched(form)
            return True

        opt.click()
        self._mark_touched(form)
        return True

    # -------- checkbox rows --------
    def _set_checkbox_by_label_if_present(self, root: Locator, section: str, key: str, value: bool) -> None:
        sec = self._section(root, section)
        label = self._expected_label(key) or key

        lit = self._xpath_literal(label)
        row = sec.locator(f"xpath=.//*[contains(normalize-space(.), {lit})]").first
        if row.count() == 0:
            logger.debug("Checkbox row not found: %s/%s", section, key)
            return

        cb = row.locator("css=input[type='checkbox']").first
        if cb.count() == 0:
            if value:
                logger.debug("Toggle row click (no checkbox input) for %s/%s", section, key)
                row.click()
            return

        try:
            checked = cb.is_checked()
            if checked != value:
                logger.debug("Set checkbox %s/%s -> %s", section, key, value)
                cb.check() if value else cb.uncheck()
        except Exception:
            pass

    # -------- checklists / multiselect --------
    def _select_checklist_by_option_label(self, option_label: str) -> Locator | None:
        desired = (option_label or "").strip()
        if not desired:
            return None

        listboxes = self.page.locator("[role='listbox']")
        count = listboxes.count()

        for i in range(count):
            lb = listboxes.nth(i)
            options = self._list_listbox_options(lb)
            logger.debug("Listbox options: %s -> %s", i, options)
            if desired in options:
                logger.info("Select listbox option: %s", desired)
                return lb

        logger.warning("Listbox option not found: %s", desired)
        return self.page.locator("[role='listbox']").first


    def _list_listbox_options(self, listbox: Locator) -> list[str]:
        options = []
        try:
            items = listbox.locator("[role='option']")
            count = items.count()
            for i in range(count):
                try:
                    text = items.nth(i).inner_text().strip()
                    if text:
                        options.append(text)
                except Exception:
                    continue
        except Exception:
            pass
        return options

    def _open_checklist_and_check(self, root: Locator, section: str, key: str, items: Sequence[str]) -> None:
        sec = self._section(root, section)
        label = self._expected_label(key) or key

        logger.info("Open checklist %s/%s and check %d items", section, key, len(items))

        opener = None

        # 1) стандартный путь — по label (работает если label реально есть)
        try:
            opener = self._find_control_by_label(sec, label)
            if opener and opener.count() == 0:
                opener = None
        except Exception:
            opener = None

        # 2) fallback для кейса, когда label пустой (как в "В квартирі є")
        #    берем первый MUI Select button внутри секции
        if not opener:
            opener = sec.locator(
                "css=div.MuiSelect-select[role='button'], [role='button'][aria-haspopup='listbox']"
            ).first
            if opener.count() == 0:
                opener = None

        if not opener:
            logger.warning("Checklist opener not found for %s/%s", section, key)
            return

        # click to open listbox
        try:
            opener.scroll_into_view_if_needed()
        except Exception:
            pass

        try:
            opener.click()
        except Exception:
            try:
                opener.click(force=True)
            except Exception:
                pass

        # scope listbox to opener (если aria-controls нет — берём последний видимый listbox)
        menu_id = None
        try:
            menu_id = opener.get_attribute("aria-controls")
        except Exception:
            menu_id = None

        lb = self._active_listbox(prefer_menu_id=menu_id)
        if not lb:
            logger.warning("Select listbox not opened for %s/%s", section, key)
            return

        for item in items:
            node = self._find_option_in_listbox(lb, str(item))
            if not node:
                logger.warning("Checklist option not found: %s/%s -> %s", section, key, str(item))
                continue

            cb = node.locator("css=input[type='checkbox']").first
            if cb.count():
                try:
                    cb.check()
                except Exception:
                    try:
                        node.click()
                    except Exception:
                        pass
            else:
                try:
                    node.click()
                except Exception:
                    pass

        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass


    def _set_multiselect_or_checklist(self, root: Locator, section: str, key: str, values: Sequence[str]) -> None:
        sec = self._section(root, section)
        label = self._expected_label(key) or key

        ctrl = self._find_control_by_label(sec, label)
        if not ctrl:
            logger.warning("Multi control not found for %s/%s", section, key)
            return

        inp = ctrl.locator("css=input").first if ctrl.locator("css=input").count() else None
        if inp:
            # основной путь — autocomplete multi
            self._fill_autocomplete_multi(sec, key, values)
            return

        # fallback
        self._open_checklist_and_check(root, section, key, list(values))
