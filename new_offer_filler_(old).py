from __future__ import annotations

import ast
import os
from dataclasses import is_dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from playwright.sync_api import Locator, Page, TimeoutError as PWTimeoutError

from offer_mapping import offer_mapping, offer_type_mapping, address_mapping, offer_name_mapping
from rieltor_dataclasses_01 import Offer, PhotoBlock
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


class NewOfferFormFiller:
    """Fill only the 'Нове оголошення' form on /offers/create.

    This class assumes `Page` already exists and user is logged in.
    """

    CREATE_URL = "https://my.rieltor.ua/offers/create"
    ROOT_H5_TEXT = "Нове оголошення"

    # Sometimes offer_name_mapping is missing newer fields
    LABEL_OVERRIDES: Dict[str, str] = {
        "house_number": "Будинок",
    }

    # Internal keys (dataclass) -> human labels in checkbox lists
    WITHOUT_POWER_SUPPLY_ITEM_LABELS: Dict[str, str] = {
        "water": "Вода",
        "gas": "Газ",
        "sewer": "Каналізація",
        "heating": "Опалення",
        "internet": "Інтернет",
        "elevator": "Ліфт",
        "backup_power": "Резервне живлення",
    }

    def __init__(self, page: Page, debug: bool = False) -> None:
        self.page = page
        if debug:
            logger.setLevel("DEBUG")

    # ---------------- public API ----------------
    def open(self) -> None:
        self.page.goto(self.CREATE_URL, wait_until="domcontentloaded")
        logger.info("Opened create-offer page")

    def create_offer_draft(self, offer: Offer) -> None:
        self.open()
        root = self._new_offer_root()
        logger.info("Start filling offer draft")

        # 1) Deal type buttons
        self._click_box_button_in_section(root, "Тип угоди", self._deal_text(offer.offer_type))

        # 2) Property type buttons
        self._click_box_button_in_section(root, "Тип нерухомості", self._to_text(offer.property_type).lower())

        # 3) Address
        self._fill_address(root, offer)

        # 4) Main params
        self._fill_by_label(root, section="Основні параметри", key="price", value=str(offer.price))
        self._fill_select_or_text(root, section="Основні параметри", key="currency", value=self._to_text(offer.currency))

        self._set_checkbox_by_label_if_present(root, "Основні параметри", "buyer_commission", bool(offer.buyer_commission))
        self._set_checkbox_by_label_if_present(root, "Основні параметри", "assignment", bool(offer.assignment))

        if offer.commission is not None:
            self._fill_by_label(root, "Основні параметри", "commission", str(offer.commission))
        if offer.commission_unit is not None:
            self._fill_select_or_text(root, "Основні параметри", "commission_unit", self._to_text(offer.commission_unit))

        # 5) Object info
        self._fill_select_or_text(root, "Інформація про об'єкт", "rooms", str(offer.rooms))
        self._fill_by_label(root, "Інформація про об'єкт", "floor", str(offer.floor))
        self._fill_by_label(root, "Інформація про об'єкт", "floors_total", str(offer.floors_total))
        self._fill_select_or_text(root, "Інформація про об'єкт", "condition", self._to_text(offer.condition))

        self._fill_by_label(root, "Інформація про об'єкт", "total_area", str(offer.total_area))
        self._fill_by_label(root, "Інформація про об'єкт", "living_area", str(offer.living_area))
        self._fill_by_label(root, "Інформація про об'єкт", "kitchen_area", str(offer.kitchen_area))

        if offer.special_conditions:
            self._set_multiselect_or_checklist(root, "Інформація про об'єкт", "special_conditions", offer.special_conditions)

        # without power supply: open list + check by human labels
        if offer.without_power_supply:
            raw_keys = self._truthy_fields_as_keys(offer.without_power_supply)
            human = [self.WITHOUT_POWER_SUPPLY_ITEM_LABELS.get(k, k) for k in raw_keys]
            if human:
                self._open_checklist_and_check(root, "Інформація про об'єкт", "without_power_supply", human)

        if offer.accessibility:
            self._open_checklist_and_check(root, "Інформація про об'єкт", "accessibility", list(offer.accessibility))

        # 6) Additional params (toggle section)
        self._click_section_toggle(root, "Додаткові параметри")
        self._set_checkbox_by_label_if_present(root, "Додаткові параметри", "heating", bool(offer.heating))
        self._set_checkbox_by_label_if_present(root, "Додаткові параметри", "hot_water", bool(offer.hot_water))
        self._set_checkbox_by_label_if_present(root, "Додаткові параметри", "gas", bool(offer.gas))
        self._set_checkbox_by_label_if_present(root, "Додаткові параметри", "internet", bool(offer.internet))

        if offer.nearby:
            self._set_multiselect_or_checklist(root, "Додаткові параметри", "nearby", offer.nearby)

        if offer.building_features:
            self._set_multiselect_or_checklist(root, "Додаткові параметри", "additional_features", offer.building_features)

        # 7) Photo blocks
        self._fill_photo_blocks(root, offer)

        # 8) Required validation (touched fields)
        self._assert_required_filled(root)

        logger.info("Offer draft filled")

    def save(self) -> None:
        logger.info("Click save")
        self.page.locator("button:has-text('Зберегти')").first.click()
        self.page.wait_for_load_state("networkidle")

    def save_and_get_report(self, raise_on_errors: bool = False) -> List[dict]:
        """Save and return structured validation report.

        If `raise_on_errors` is True and report is not empty, raises FormValidationError(report).
        """
        self.save()
        root = self._new_offer_root()
        report = self.collect_validation_report(root)
        if report:
            logger.warning("Validation errors after save: %s", report)
            if raise_on_errors:
                raise FormValidationError(report)
        else:
            logger.info("No validation errors detected")
        return report

    def collect_validation_report(self, root=None) -> List[dict]:
        """Collect MUI validation errors into a structured report."""
        if root is None:
            root = self._new_offer_root()

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

    # ---------------- structure helpers ----------------
    def _new_offer_root(self) -> Locator:
        h5 = self.page.locator("h5", has_text=self.ROOT_H5_TEXT).first
        h5.wait_for(state="visible")
        return h5.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][1]").first

    def _section(self, root: Locator, h6_text: str) -> Locator:
        h6 = root.locator("h6", has_text=h6_text).first
        h6.wait_for(state="visible")
        # ancestor[2] = section container (header+content)
        return h6.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][2]").first

    # ---------------- mapping + label resolution ----------------
    @staticmethod
    def _to_text(v: Any) -> str:
        if isinstance(v, Enum):
            return str(v.value)
        return "" if v is None else str(v)

    def _expected_label(self, key: str) -> Optional[str]:
        return offer_name_mapping.get(key) or self.LABEL_OVERRIDES.get(key)

    def _parse_mapping(self, key: str) -> Optional[dict]:
        raw = offer_mapping.get(key) or address_mapping.get(key) or offer_type_mapping.get(key)
        if raw is None:
            return None
        if isinstance(raw, dict):
            return raw
        return ast.literal_eval(raw)

    @staticmethod
    def _selector_from_attrs(attrs: dict) -> str:
        cls = attrs.get("class")
        _id = attrs.get("id")
        parts = []
        if _id:
            parts.append(f"#{_id}")
        if cls:
            classes = cls.split() if isinstance(cls, str) else list(cls)
            parts.append("".join([f".{c}" for c in classes if c]))
        for k, val in attrs.items():
            if k in ("class", "id") or val is None:
                continue
            parts.append(f'[{k}="{val}"]')
        return "".join(parts) if parts else "*"

    def _find_control_fast_with_label_check(self, key: str) -> Optional[Locator]:
        attrs = self._parse_mapping(key)
        if not attrs:
            return None

        sel = self._selector_from_attrs(attrs)
        loc = self.page.locator(sel).first
        try:
            loc.wait_for(state="attached", timeout=1500)
        except Exception:
            return None

        expected = self._expected_label(key)
        if not expected:
            return loc

        for up in range(1, 6):
            try:
                anc = loc.locator(f"xpath=ancestor::*[{up}]").first
                txt = (anc.inner_text(timeout=800) or "")
                if expected in txt:
                    return loc
            except Exception:
                pass
        return None

    def _find_control_by_label(self, section: Locator, label_text: str) -> Optional[Locator]:
        lit = self._xpath_literal((label_text or "").strip())
        label = section.locator(f"xpath=.//label[contains(normalize-space(.), {lit})]").first
        try:
            label.wait_for(state="visible", timeout=2500)
        except Exception:
            return None

        form = label.locator("xpath=ancestor::div[contains(@class,'MuiFormControl-root')][1]").first
        ctrl = form.locator("css=input, textarea, [role='combobox']").first
        try:
            ctrl.wait_for(state="attached", timeout=1500)
            return ctrl
        except Exception:
            return None

    def _xpath_literal(self, s: str) -> str:
        if "'" not in s:
            return f"'{s}'"
        if '"' not in s:
            return f'"{s}"'
        parts = s.split("'")
        return "concat(" + ", ".join(
            [f"'{p}'" if i == len(parts) - 1 else f"'{p}', \"'\"" for i, p in enumerate(parts)]
        ) + ")"


    def _is_input_filled(self, inp: Locator) -> bool:
        try:
            v = (inp.input_value() or "").strip()
            return bool(v)
        except Exception:
            return False


    # ---------------- interactions ----------------
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

    def _fill_address(self, root: Locator, offer: Offer) -> None:
        sec = self._section(root, "Адреса об'єкта")
        a = offer.address
        if a.street.startswith("вул.") or a.street.startswith("вулиця "):
            a.street = a.street.replace("вул.", "").replace("вулиця ", "")

        logger.info("Fill address: %s, %s, %s", a.region, a.city, a.street)

        # region -> должен появиться city
        self._fill_autocomplete(sec, "region", a.region, next_key="city")

        # city -> должен появиться district (если есть) иначе street
        self._fill_autocomplete(sec, "city", a.city, next_key="district" if a.district else "street")

        # district -> должен появиться street
        if a.district:
            self._fill_autocomplete(sec, "district", a.district, next_key="street")

        # street -> должен появиться house_number
        self._fill_autocomplete(sec, "street", a.street, next_key="house_number")

        self._fill_autocomplete(sec, "house_number", a.house_number)

        if getattr(a, "subway", None):
            self._fill_autocomplete_multi(sec, "subway", list(a.subway))
        if getattr(a, "guide", None):
            self._fill_autocomplete_multi(sec, "guide", list(a.guide))
        if getattr(a, "condo_complex", None):
            self._fill_autocomplete(sec, "condo_complex", a.condo_complex)


    def _fill_autocomplete_or_text(self, section: Locator, key: str, value: str) -> None:
        label = self._expected_label(key) or key
        ctrl = self._find_control_fast_with_label_check(key) or self._find_control_by_label(section, label)
        if not ctrl:
            logger.warning("Control not found for key=%s (label=%s)", key, label)
            return

        tag = ctrl.evaluate("el => el.tagName.toLowerCase()")
        if tag != "input":
            inner = ctrl.locator("css=input").first
            if inner.count():
                ctrl = inner

        ctrl.click()
        ctrl.fill(str(value))
        self._mark_touched(ctrl)


    def _robust_click(self, loc: Locator, what: str = "element", timeout_ms: int = 3000) -> bool:
        """Try multiple strategies to click an element reliably."""
        try:
            loc.wait_for(state="visible", timeout=timeout_ms)
        except Exception:
            logger.warning("Robust click: %s not visible", what)
            return False

        try:
            loc.scroll_into_view_if_needed()
        except Exception:
            pass

        try:
            loc.click(timeout=timeout_ms)
            return True
        except Exception as e:
            logger.debug("Robust click: normal click failed for %s: %s", what, e)

        try:
            loc.click(trial=True, timeout=timeout_ms)
        except Exception as e:
            logger.debug("Robust click: trial click says %s", e)

        try:
            loc.click(force=True, timeout=timeout_ms)
            return True
        except Exception as e:
            logger.debug("Robust click: force click failed for %s: %s", what, e)

        try:
            box = loc.bounding_box()
            if box:
                x = box["x"] + box["width"] / 2
                y = box["y"] + box["height"] / 2
                self.page.mouse.move(x, y)
                self.page.mouse.click(x, y)
                return True
        except Exception as e:
            logger.debug("Robust click: mouse click failed for %s: %s", what, e)

        try:
            loc.dispatch_event("mousedown")
            loc.dispatch_event("mouseup")
            loc.dispatch_event("click")
            return True
        except Exception as e:
            logger.debug("Robust click: dispatch events failed for %s: %s", what, e)

        return False


    def _mouse_click_visible_option_by_text(
        self,
        desired: str,
        timeout_ms: int = 6000,
        *,
        allow_single_option: bool = False,
        anchor_box: dict | None = None,
    ) -> bool:
        desired = (desired or "").strip()
        if not desired:
            return False

        self.page.wait_for_timeout(150)

        res = self.page.evaluate(
            """(params) => {
                const desired = params.desired;
                const timeoutMs = params.timeoutMs;
                const allowSingle = params.allowSingle;
                const anchor = params.anchor;

                const start = Date.now();
                const norm = (s) => (s || '').replace(/\\s+/g,' ').trim().toLowerCase();
                const d = norm(desired);

                const isVisible = (el) => {
                if (!el) return false;
                const cs = getComputedStyle(el);
                if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
                const r = el.getBoundingClientRect();
                if (r.width < 5 || r.height < 5) return false;
                if (r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) return false;
                return true;
                };

                const inAnchorBand = (r) => {
                if (!anchor) return true;
                const bandTop = anchor.y + anchor.height - 6;
                const bandBottom = bandTop + 420;
                const cx = r.left + r.width / 2;
                const ax = anchor.x + anchor.width / 2;
                return r.top >= bandTop && r.top <= bandBottom && Math.abs(cx - ax) <= 520;
                };

                // ВАЖНО: добавили li и div, иначе иногда "visible=0"
                const selectors = [
                '[role="option"]',
                '[data-option-index]',
                '.MuiAutocomplete-option',
                '[role="listbox"] [role="option"]',
                '[role="listbox"] li',
                'li',
                'div'
                ];

                function collect() {
                const out = [];
                const seen = new Set();
                for (const sel of selectors) {
                    for (const el of document.querySelectorAll(sel)) {
                    if (seen.has(el)) continue;
                    seen.add(el);
                    if (!isVisible(el)) continue;

                    const tag = (el.tagName || '').toLowerCase();
                    if (['input','textarea','label','button'].includes(tag)) continue;

                    const r = el.getBoundingClientRect();
                    if (!inAnchorBand(r)) continue;

                    // отсекаем огромные контейнеры
                    if (r.height > 260 && r.width > 600) continue;

                    const txt = (el.innerText || '').trim();
                    if (!txt) continue;

                    out.push({ txt, n: norm(txt), r });
                    }
                }
                // сортируем сверху вниз (обычно список именно так расположен)
                out.sort((a,b) => a.r.top - b.r.top);
                return out;
                }

                function pick(opts) {
                const onlyDigits = (s) => (s || '').replace(/\\D+/g,'');
                const dDigits = onlyDigits(desired);

                // 1) точное/префикс/contains (общий кейс)
                for (const o of opts) {
                    if (o.n === d || o.n.startsWith(d) || o.n.includes(d)) {
                    return { ok:true, x:o.r.left + o.r.width/2, y:o.r.top + Math.min(18, o.r.height/2), text:o.txt, mode:'match', count:opts.length };
                    }
                }

                // 2) Спец-кейс дома: совпадение по цифровому префиксу (17 -> 17к1, 17а)
                if (dDigits) {
                    for (const o of opts) {
                    const oDigits = onlyDigits(o.txt);
                    if (oDigits && oDigits.startsWith(dDigits)) {
                        return { ok:true, x:o.r.left + o.r.width/2, y:o.r.top + Math.min(18, o.r.height/2), text:o.txt, mode:'digits_prefix', count:opts.length };
                    }
                    }
                }

                // 3) если ровно 1 вариант и разрешено — берём его
                if (allowSingle && opts.length === 1) {
                    const o = opts[0];
                    return { ok:true, x:o.r.left + o.r.width/2, y:o.r.top + Math.min(18, o.r.height/2), text:o.txt, mode:'single', count:1 };
                }

                return null;
                }


                return new Promise((resolve) => {
                const tick = () => {
                    const opts = collect();
                    const got = pick(opts);
                    if (got) return resolve(got);
                    if (Date.now() - start > timeoutMs) return resolve({ ok:false, count: opts.length });
                    requestAnimationFrame(tick);
                };
                tick();
                });
            }""",
            {"desired": desired, "timeoutMs": timeout_ms, "allowSingle": allow_single_option, "anchor": anchor_box},
        )

        if not res or not res.get("ok"):
            logger.warning("Autocomplete: could not pick option for '%s' (visible=%s)", desired, (res or {}).get("count"))
            return False

        x, y = float(res["x"]), float(res["y"])
        logger.debug(
            "Autocomplete: mouse click option at (%.1f, %.1f), mode=%s, text='%s'",
            x, y, res.get("mode"), (res.get("text") or "").strip()
        )
        self.page.mouse.move(x, y)
        self.page.mouse.click(x, y)
        return True




    def _wait_dropdown_closed(self, timeout_ms: int = 2500) -> bool:
        """
        Ждём, что выпадающий список автокомплита закроется.
        Не привязываемся к listbox/poppers — просто ждём исчезновения видимых option-элементов.
        """
        try:
            self.page.wait_for_function(
                """() => {
                    const isVisible = (el) => {
                    if (!el) return false;
                    const cs = getComputedStyle(el);
                    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 5 && r.height > 5 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
                    };

                    const candidates = Array.from(document.querySelectorAll('[role="option"], [data-option-index], .MuiAutocomplete-option, [role="listbox"] [role="option"], [role="listbox"] li'));

                    // Если вообще нет видимых опций — считаем, что дропдаун закрыт
                    return !candidates.some(isVisible);
                }""",
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False


    def _wait_next_field_visible(self, section: Locator, next_key: str, timeout_ms: int = 5000) -> bool:
        """
        Ждём появления следующего поля (по label из offer_name_mapping / overrides).
        """
        next_label = self._expected_label(next_key) or next_key
        lit = self._xpath_literal(next_label)
        try:
            # Ищем label с текстом next_label внутри секции адреса
            section.locator(f"xpath=.//label[contains(normalize-space(.), {lit})]").first.wait_for(
                state="visible",
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False




    def _pick_autocomplete_option_and_verify(
        self,
        inp: Locator,
        desired_text: str,
        timeout_ms: int = 9000,
        *,
        section: Locator | None = None,
        next_key: str | None = None,
        allow_single_option: bool = False,
    ) -> bool:

        desired = (desired_text or "").strip()

        # открыть список (если нужно)
        try:
            inp.press("ArrowDown")
        except Exception:
            pass

        # клик по видимой опции (координатный)
        anchor = None
        try:
            anchor = inp.bounding_box()
        except Exception:
            anchor = None

        if not self._mouse_click_visible_option_by_text(
            desired,
            timeout_ms=timeout_ms,
            allow_single_option=allow_single_option,
            anchor_box=anchor,
        ):
            return False



        # 1) дождаться закрытия списка (самый универсальный сигнал “выбор применён”)
        closed = self._wait_dropdown_closed(timeout_ms=2500)

        # 2) если задано следующее поле — ждём его появления
        if section is not None and next_key:
            if self._wait_next_field_visible(section, next_key, timeout_ms=5000):
                logger.debug("Autocomplete confirmed by next field: %s", next_key)
                return True

        # 3) fallback: проверяем значение инпута (Python-side)
        self.page.wait_for_timeout(150)
        try:
            cur = (inp.input_value() or "").strip()
        except Exception:
            cur = ""

        if cur:
            cur_l = cur.lower()
            des_l = desired.lower()
            if cur_l == des_l or cur_l.startswith(des_l) or des_l in cur_l:
                logger.debug("Autocomplete confirmed by input value. closed=%s desired='%s' current='%s'", closed, desired, cur)
                return True

        logger.warning("Autocomplete: not confirmed. closed=%s desired='%s' current='%s'", closed, desired, cur)
        return False



    def _fill_autocomplete(self, section: Locator, key: str, value: str, *, next_key: str | None = None) -> None:
        label = self._expected_label(key) or str(value)
        ctrl = self._find_control_fast_with_label_check(key) or self._find_control_by_label(section, label)
        if not ctrl:
            logger.warning("Autocomplete control not found for key='%s' label='%s'", key, label)
            return

        inp = ctrl.locator("css=input").first if ctrl.locator("css=input").count() else ctrl
        desired = str(value).strip()

        # SKIP: уже заполнено
        if self._is_input_filled(inp):
            cur = (inp.input_value() or "").strip()
            logger.info("Autocomplete skip '%s': already filled '%s'", key, cur)
            return

        logger.info("Autocomplete fill '%s' = '%s'", key, desired)

        def _clear_and_type() -> None:
            try:
                inp.click()
            except Exception:
                pass

            try:
                inp.fill("")
            except Exception:
                try:
                    inp.press("Control+A")
                    inp.press("Backspace")
                except Exception:
                    pass

            try:
                inp.type(desired, delay=25)
            except Exception:
                try:
                    inp.fill(desired)
                except Exception:
                    pass

        def _try_pick() -> bool:
            # house_number: разрешаем “один вариант в списке” даже без полного совпадения
            allow_single = key == "house_number"
            return self._pick_autocomplete_option_and_verify(
                inp,
                desired,
                section=section,
                next_key=next_key,
                allow_single_option=allow_single,
            )

        _clear_and_type()
        if _try_pick():
            self._mark_touched(inp)
            return

        logger.debug("Retry autocomplete selection (mouse) for '%s' = '%s'", key, desired)
        try:
            inp.press("End")
            inp.type(" ")
            self.page.wait_for_timeout(120)
            inp.press("Backspace")
        except Exception:
            pass

        if _try_pick():
            self._mark_touched(inp)
            return

        cur = ""
        try:
            cur = (inp.input_value() or "").strip()
        except Exception:
            pass

        self._mark_touched(inp)
        logger.warning("Autocomplete failed to select '%s' for key='%s' next_key='%s' (current='%s')", desired, key, next_key, cur)



    def _fill_autocomplete_multi(self, section: Locator, key: str, values: Sequence[str]) -> None:
        label = self._expected_label(key) or key
        ctrl = self._find_control_fast_with_label_check(key) or self._find_control_by_label(section, label)
        if not ctrl:
            logger.warning("Autocomplete(multi) control not found for key=%s (label=%s)", key, label)
            return

        inp = ctrl.locator("css=input").first if ctrl.locator("css=input").count() else ctrl

        for v in values:
            logger.debug("Autocomplete multi add %s -> %s", key, v)
            inp.click()
            try:
                inp.fill("")
            except Exception:
                pass
            inp.type(str(v), delay=20)
            # Use the same robust picker
            self._pick_autocomplete_option_and_verify(inp, str(v), timeout_ms=7000)

        self._mark_touched(inp)

    # NOTE: removed the old _pick_autocomplete_option() that used unsupported locator(..., t=...)

    def _fill_by_label(self, root: Locator, section: str, key: str, value: str) -> None:
        sec = self._section(root, section)
        label = self._expected_label(key) or key
        ctrl = self._find_control_fast_with_label_check(key) or self._find_control_by_label(sec, label)
        if not ctrl:
            logger.warning("Control not found for key=%s (label=%s) in section=%s", key, label, section)
            return

        tag = ctrl.evaluate("el => el.tagName.toLowerCase()")
        if tag not in ("input", "textarea"):
            inner = ctrl.locator("css=input, textarea").first
            if inner.count():
                ctrl = inner

        logger.debug("Fill %s/%s=%s", section, key, value)
        ctrl.click()
        ctrl.fill(str(value))
        self._mark_touched(ctrl)

    def _fill_select_or_text(self, root: Locator, section: str, key: str, value: str) -> None:
        sec = self._section(root, section)
        label = self._expected_label(key) or key
        ctrl = self._find_control_fast_with_label_check(key) or self._find_control_by_label(sec, label)
        if not ctrl:
            logger.warning("Select/text control not found for key=%s (label=%s) in section=%s", key, label, section)
            return

        role = ctrl.get_attribute("role")
        try:
            tag = ctrl.evaluate("el => el.tagName.toLowerCase()")
        except Exception:
            tag = None

        if role in ("button", "combobox") or tag == "select":
            logger.debug("Select %s/%s -> %s", section, key, value)
            ctrl.click()
            opt = self.page.locator(f"[role='option']:has-text('{value}')").first
            if opt.count() == 0:
                opt = self.page.locator(f"li:has-text('{value}')").first
            try:
                opt.click(timeout=2500)
            except Exception:
                try:
                    inp = sec.locator("css=input").first
                    inp.fill(value)
                    inp.press("Enter")
                except Exception:
                    pass
            self._mark_touched(ctrl)
            return

        self._fill_by_label(root, section, key, value)

    def _set_checkbox_by_label_if_present(self, root: Locator, section: str, key: str, value: bool) -> None:
        sec = self._section(root, section)
        label = self._expected_label(key)
        if not label:
            return

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

    def _open_checklist_and_check(self, root: Locator, section: str, key: str, items: Sequence[str]) -> None:
        sec = self._section(root, section)
        label = self._expected_label(key) or key

        logger.info("Open checklist %s/%s and check %d items", section, key, len(items))

        opener = self._find_control_fast_with_label_check(key) or self._find_control_by_label(sec, label)
        if opener:
            try:
                opener.click()
            except Exception:
                pass

        for item in items:
            logger.debug("Check item: %s", item)
            lit = self._xpath_literal(str(item))
            node = self.page.locator(f"xpath=//*[contains(normalize-space(.), {lit})]").first
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
        label = self._expected_label(key)
        if not label:
            return

        ctrl = self._find_control_fast_with_label_check(key) or self._find_control_by_label(sec, label)
        if not ctrl:
            logger.warning("Multi control not found for %s/%s", section, key)
            return

        inp = ctrl.locator("css=input").first if ctrl.locator("css=input").count() else None
        if inp:
            ok = True
            for v in values:
                try:
                    inp.click()
                    try:
                        inp.fill("")
                    except Exception:
                        pass
                    inp.type(str(v), delay=20)
                    self._pick_autocomplete_option_and_verify(inp, str(v), timeout_ms=7000)
                except Exception:
                    ok = False
                    break
            if ok:
                self._mark_touched(inp)
                return

        self._open_checklist_and_check(root, section, key, list(values))

    # ---------------- photo blocks ----------------
    def _fill_photo_blocks(self, root: Locator, offer: Offer) -> None:
        blocks = [
            ("Блок 1 з 5: Про квартиру", getattr(offer, "apartment", None), "description1"),
            ("Блок 2 з 5: Деталі інтер’єру", getattr(offer, "interior", None), "description2"),
            ("Блок 3 з 5: Планування", getattr(offer, "layout", None), "description3"),
            ("Блок 4 з 5: Будинок та двір", getattr(offer, "yard", None), "description4"),
            ("Блок 5 з 5: Інфраструктура", getattr(offer, "infrastructure", None), "description5"),
        ]

        for h6_title, block, desc_name in blocks:
            if not isinstance(block, PhotoBlock):
                continue
            if (not block.description) and (not block.photos):
                continue

            sec = self._section(root, h6_title)
            logger.info("Fill photo block: %s", h6_title)

            if block.description:
                ta = sec.locator(f"textarea[name='{desc_name}']").first
                if ta.count() == 0:
                    ta = sec.locator("textarea").first
                try:
                    ta.fill(block.description)
                    self._mark_touched(ta)
                except Exception:
                    logger.exception("Failed to fill description in block %s", h6_title)

            if block.photos:
                files = [p for p in block.photos if p and os.path.exists(p)]
                if files:
                    fi = sec.locator("input[type='file']").first
                    try:
                        fi.set_input_files(files)
                        logger.debug("Uploaded %d photos in block %s", len(files), h6_title)
                    except Exception:
                        logger.exception("Failed to upload photos in block %s", h6_title)

    # ---------------- required validation ----------------
    def _mark_touched(self, ctrl: Locator) -> None:
        try:
            ctrl.evaluate("el => el.setAttribute('data-rieltor-touched','1')")
        except Exception:
            pass

    def _is_required_control(self, ctrl: Locator) -> bool:
        try:
            if ctrl.get_attribute("required") is not None:
                return True
        except Exception:
            pass

        try:
            cls = ctrl.get_attribute("class") or ""
            if "Mui-required" in cls or "-required" in cls:
                return True
        except Exception:
            pass

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

    def _control_value_is_empty(self, ctrl: Locator) -> bool:
        try:
            tag = ctrl.evaluate("el => el.tagName.toLowerCase()")
        except Exception:
            tag = None

        if tag == "textarea":
            try:
                v = ctrl.input_value()
                return not (v and v.strip())
            except Exception:
                return True

        try:
            if ctrl.evaluate("el => el.tagName.toLowerCase()") != "input":
                inner = ctrl.locator("css=input, textarea").first
                if inner.count():
                    ctrl = inner
            v = ctrl.input_value()
            return not (v and v.strip())
        except Exception:
            return True

    def _assert_required_filled(self, root: Locator) -> None:
        touched = root.locator("[data-rieltor-touched='1']")
        n = touched.count()
        errors = []

        for i in range(n):
            ctrl = touched.nth(i)
            if not self._is_required_control(ctrl):
                continue
            if self._control_value_is_empty(ctrl):
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

    # ---------------- misc ----------------
    @staticmethod
    def _deal_text(offer_type: Any) -> str:
        v = (str(offer_type.value) if isinstance(offer_type, Enum) else str(offer_type)).lower()
        if "прод" in v:
            return "продаж"
        if "орен" in v or "аренд" in v:
            return "оренда"
        return v

    @staticmethod
    def _truthy_fields_as_keys(dc_obj) -> List[str]:
        if not is_dataclass(dc_obj):
            return []
        out = []
        for f in dc_obj.__dataclass_fields__.keys():
            if getattr(dc_obj, f, None) is True:
                out.append(f)
        return out
