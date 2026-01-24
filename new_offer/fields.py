from __future__ import annotations

from typing import Sequence

from playwright.sync_api import Locator, Page

from setup_logger import setup_logger

logger = setup_logger(__name__)


class FieldsMixin:
    # -------- buttons / toggles --------
    def _click_box_button_in_section(self, root: Locator, section_h6: str, text: str) -> None:
        sec = self._section(root, section_h6)
        target = (text or "").strip().lower()
        logger.info("Select button in '%s': %s", section_h6, target)

        lit = self._xpath_literal(target)
        xp = (
            ".//div[contains(@class,'MuiBox-root') and .//span and "
            "contains(translate(normalize-space(.//span),"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZАБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ',"
            "'abcdefghijklmnopqrstuvwxyzабвгґдеєжзиіїйклмнопрстуфхцчшщьюя'),"
            f"{lit})]"
        )

        btn = sec.locator(f"xpath={xp}").first
        if btn.count() == 0:
            logger.warning("Button not found in section '%s' for text '%s'", section_h6, target)
            try:
                texts = [t.strip() for t in sec.locator("css=span").all_inner_texts() if t.strip()]
                logger.debug("Available button texts in '%s': %s", section_h6, texts)
            except Exception:
                pass
            return

        btn.click()


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
        form = self._find_formcontrol_by_label(sec, label)
        if form and self._try_fill_radio_group(form, section, key, desired):
            logger.debug("Fill radio-group %s/%s = %s", section, key, desired)
            return

        # 1) locate control
        ctrl = self._find_control_by_label(sec, label)
        if not ctrl:
            logger.warning("Select/text control not found for key=%s (label=%s) in section=%s", key, label, section)
            return

        form = ctrl.locator("xpath=ancestor::div[contains(@class,'MuiFormControl-root')][1]").first
        select_btn = form.locator("css=div.MuiSelect-select[role='button']").first

        # ---------- MUI Select ----------
        if select_btn.count():
            if not desired:
                logger.info("Select skip %s/%s: empty value", section, key)
                return

            try:
                cur = (select_btn.inner_text() or "").strip()
            except Exception:
                cur = ""

            if cur == desired:
                logger.info("Select skip %s/%s: already '%s'", section, key, cur)
                self._mark_touched(form)  # IMPORTANT: mark formcontrol, not only select_btn
                return

            logger.info("Select %s/%s -> %s", section, label, desired)
            select_btn.click()

            try:
                self.page.wait_for_selector("xpath=//div[@id='menu-']//ul", timeout=4000)
            except Exception:
                logger.warning("Select listbox not opened for %s/%s", section, key)
                return

            # ul = self.page.locator("xpath=//div[@id='menu-']//ul").first


            opt = self.page.locator("[role='listbox'] [role='option']").filter(has_text=desired).first
            if opt.count() == 0:
                logger.warning("Option '%s' not found for %s/%s", desired, section, key)
                texts = self._list_radio_options(opt)
                logger.debug("Radio available options for %s/%s: %s", section, key, texts)
                self.page.keyboard.press("Escape")
                return

            opt.click()
            self._mark_touched(form)  # IMPORTANT
            self.page.keyboard.press("Escape")
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

        opener = self._find_control_by_label(sec, label)
        if opener:
            try:
                opener.click()
            except Exception:
                pass

        try:
            self.page.wait_for_selector("[role='listbox']", timeout=4000)
        except Exception:
            logger.warning("Select listbox not opened for %s/%s", section, key)
            return

        ul = self.page.locator("xpath=//div[@id='menu-']//ul").first

        for item in items:
            logger.debug("Check item: %s", str(item))
            lit = self._xpath_literal(str(item))
            node = ul.locator(f"xpath=//*[contains(normalize-space(.), {lit})]").first
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
