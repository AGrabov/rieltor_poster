# rieltor_offer_poster_v2.py
from __future__ import annotations

import ast
import os
from dataclasses import is_dataclass
from enum import Enum
from typing import Any, Iterable, Optional, Sequence

from playwright.sync_api import Page, sync_playwright, TimeoutError as PWTimeoutError

from rieltor_dataclasses_01 import Offer, PhotoBlock, Address, OfferType, WithoutPowerSupply, PropertyType, Currency, RoomLayout, Condition
from offer_mapping import offer_mapping, offer_type_mapping, address_mapping, offer_name_mapping
from setup_logger import setup_logger
logger = setup_logger(__name__)


class RequiredFieldError(RuntimeError):
    pass


class RieltorOfferPosterV2:
    """
    Стабильный постинг через:
      h5 "Нове оголошення" -> секции h6 -> label -> input/textarea/checkbox.
    Mapping используется как быстрый путь, но проверка соответствия всегда по label (offer_name_mapping).
    """

    LOGIN_URL = "https://my.rieltor.ua/login"
    CREATE_URL = "https://my.rieltor.ua/offers/create"

    ROOT_H5_TEXT = "Нове оголошення"

    def __init__(
        self,
        phone: str,
        password: str,
        headless: bool = False,
        slow_mo_ms: int = 0,
        default_timeout_ms: int = 30_000,
    ) -> None:
        self.phone = phone
        self.password = password
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.default_timeout_ms = default_timeout_ms

        self._p = None
        self._browser = None
        self._context = None
        self.page: Optional[Page] = None

    # ---------------- lifecycle ----------------
    def __enter__(self) -> "RieltorOfferPosterV2":
        self._p = sync_playwright().start()
        self._browser = self._p.chromium.launch(headless=self.headless, slow_mo=self.slow_mo_ms)
        self._context = self._browser.new_context()
        self.page = self._context.new_page()
        self.page.set_default_timeout(self.default_timeout_ms)
        logger.debug("Started Playwright")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._context:
                self._context.close()
        finally:
            try:
                if self._browser:
                    self._browser.close()
            finally:
                if self._p:
                    self._p.stop()

    # ---------------- public ----------------
    def login(self) -> None:
        assert self.page
        p = self.page
        p.goto(self.LOGIN_URL, wait_until="domcontentloaded")
        p.fill("input[name='phone']", self.phone)
        p.fill("input[name='password']", self.password)
        p.click("button[type='submit']")
        p.wait_for_load_state("networkidle")
        logger.debug("Logged in") if self.page else logger.error("Failed to login")

    def create_offer_draft(self, offer: Offer) -> None:
        assert self.page
        p = self.page
        p.goto(self.CREATE_URL, wait_until="domcontentloaded")
        p.wait_for_load_state("networkidle")

        root = self._new_offer_root()

        # 1) Тип угоди (кнопки)
        self._click_box_button_in_section(root, "Тип угоди", self._deal_text(offer.offer_type))

        # 2) Тип нерухомості (кнопки с иконками, текст в <span>)
        # В твоём Offer это offer.property_type (Enum/str). Ожидаем: квартира/кімната/будинок/комерційна/ділянка/паркомісце
        self._click_box_button_in_section(root, "Тип нерухомості", self._to_text(offer.property_type).lower())

        # 3) Адреса об'єкта (Autocomplete + required)
        self._fill_address(root, offer)

        # 4) Основні параметри
        self._fill_by_label(root, section="Основні параметри", key="price", value=str(offer.price))
        self._fill_select_or_text(root, section="Основні параметри", key="currency", value=self._to_text(offer.currency))

        # чекбоксы/переключатели если есть
        self._set_checkbox_by_label_if_present(root, "Основні параметри", "buyer_commission", bool(offer.buyer_commission))
        self._set_checkbox_by_label_if_present(root, "Основні параметри", "assignment", bool(offer.assignment))

        if offer.commission is not None:
            self._fill_by_label(root, "Основні параметри", "commission", str(offer.commission))
        if offer.commission_unit is not None:
            self._fill_select_or_text(root, "Основні параметри", "commission_unit", self._to_text(offer.commission_unit))

        # 5) Інформація про об'єкт
        self._fill_select_or_text(root, "Інформація про об'єкт", "rooms", str(offer.rooms))
        self._fill_by_label(root, "Інформація про об'єкт", "floor", str(offer.floor))
        self._fill_by_label(root, "Інформація про об'єкт", "floors_total", str(offer.floors_total))
        self._fill_select_or_text(root, "Інформація про об'єкт", "condition", self._to_text(offer.condition))

        self._fill_by_label(root, "Інформація про об'єкт", "total_area", str(offer.total_area))
        self._fill_by_label(root, "Інформація про об'єкт", "living_area", str(offer.living_area))
        self._fill_by_label(root, "Інформація про об'єкт", "kitchen_area", str(offer.kitchen_area))

        if offer.special_conditions:
            self._set_multiselect_or_checklist(root, "Інформація про об'єкт", "special_conditions", offer.special_conditions)

        # без света/доступність: кнопка -> список чекбоксов (по твоим словам)
        if offer.without_power_supply:
            items = self._truthy_fields_as_labels(offer.without_power_supply)
            if items:
                self._open_checklist_and_check(root, "Інформація про об'єкт", "without_power_supply", items)

        if offer.accessibility:
            self._open_checklist_and_check(root, "Інформація про об'єкт", "accessibility", offer.accessibility)

        # 6) Додаткові параметри — кнопка раскрывает поля
        self._click_section_toggle(root, "Додаткові параметри")  # раскрыть
        self._set_checkbox_by_label_if_present(root, "Додаткові параметри", "heating", bool(offer.heating))
        self._set_checkbox_by_label_if_present(root, "Додаткові параметри", "hot_water", bool(offer.hot_water))
        self._set_checkbox_by_label_if_present(root, "Додаткові параметри", "gas", bool(offer.gas))
        self._set_checkbox_by_label_if_present(root, "Додаткові параметри", "internet", bool(offer.internet))

        if offer.nearby:
            self._set_multiselect_or_checklist(root, "Додаткові параметри", "nearby", offer.nearby)

        if offer.building_features:
            self._set_multiselect_or_checklist(root, "Додаткові параметри", "additional_features", offer.building_features)

        # 7) Блоки фото/описаний (каждый со своим загрузчиком)
        self._fill_photo_blocks(root, offer)

        # 8) После заполнения: проверить обязательные поля, которые мы трогали
        self._assert_required_filled(root)

    def save(self) -> None:
        assert self.page
        self.page.locator("button:has-text('Зберегти')").first.click()

    # ---------------- structure helpers ----------------
    def _new_offer_root(self):
        """
        Возвращает контейнер формы "Нове оголошення".
        В HTML: h5 -> div "Нове оголошення" и дальше соседний контейнер с секциями h6【...】.
        """
        assert self.page
        p = self.page
        h5 = p.locator("h5", has_text=self.ROOT_H5_TEXT).first
        h5.wait_for(state="visible")
        # Поднимаемся на общий контейнер блока (обычно ближайший div MuiBox-root)
        root = h5.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][1]").first
        return root

    def _section(self, root, h6_text: str):
        # каждая секция начинается с h6 названием (Тип угоди, Адреса об'єкта, ...):contentReference[oaicite:8]{index=8}
        h6 = root.locator("h6", has_text=h6_text).first
        h6.wait_for(state="visible")
        # секция = ближайший контейнер, где есть этот h6 и контент под ним
        sec = h6.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][1]").first
        return sec

    # ---------------- mapping + label resolution ----------------
    @staticmethod
    def _to_text(v: Any) -> str:
        if isinstance(v, Enum):
            return str(v.value)
        return "" if v is None else str(v)

    def _expected_label(self, key: str) -> Optional[str]:
        return offer_name_mapping.get(key)

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

    def _find_control_fast_with_label_check(self, root, key: str):
        """
        Быстрый путь по mapping + обязательная проверка по label-тексту (offer_name_mapping),
        иначе вернём None и найдём по label.
        """
        assert self.page
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

        # Проверка: есть ли текст label рядом/в предках
        for up in range(1, 6):
            try:
                anc = loc.locator(f"xpath=ancestor::*[{up}]").first
                txt = (anc.inner_text(timeout=800) or "")
                if expected in txt:
                    return loc
            except Exception:
                pass
        return None

    def _find_control_by_label(self, section, label_text: str):
        """
        Ищем label и в его ближайшем контейнере input/textarea или combobox.
        В адресе это особенно стабильно (label "Область" + input required):contentReference[oaicite:9]{index=9}
        """
        # label может содержать * (span asterisk), поэтому ищем по contains(normalize-space(.), label_text)
        label = section.locator("xpath=.//label[contains(normalize-space(.), $t)]", t=label_text).first
        try:
            label.wait_for(state="visible", timeout=2500)
        except Exception:
            return None

        # Обычно input связан через for/id, но иногда нет. Берём ближайший FormControl
        form = label.locator("xpath=ancestor::div[contains(@class,'MuiFormControl-root')][1]").first
        ctrl = form.locator("css=input, textarea, [role='combobox']").first
        try:
            ctrl.wait_for(state="attached", timeout=1500)
            return ctrl
        except Exception:
            return None

    # ---------------- interactions ----------------
    def _click_box_button_in_section(self, root, section_h6: str, text: str) -> None:
        """
        Для "Тип угоди" и "Тип нерухомості": кнопки div MuiBox-root, внутри есть <span>текст</span>:contentReference[oaicite:10]{index=10}
        """
        sec = self._section(root, section_h6)
        # кнопка - контейнер, где есть span с текстом
        btn = sec.locator("xpath=.//div[contains(@class,'MuiBox-root')][.//span[translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZАБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ','abcdefghijklmnopqrstuvwxyzабвгґдеєжзиіїйклмнопрстуфхцчшщьюя')=$t]]",
                        t=text.lower()).first
        btn.click()

    def _fill_address(self, root, offer: Offer) -> None:
        sec = self._section(root, "Адреса об'єкта")
        a = offer.address

        # область/місто/район/вулиця/будинок: Autocomplete (ввод + подсказки)
        self._fill_autocomplete(sec, "region", a.region)
        self._fill_autocomplete(sec, "city", a.city)
        if a.district:
            self._fill_autocomplete(sec, "district", a.district)
        self._fill_autocomplete(sec, "street", a.street)
        self._fill_autocomplete(sec, "house_number", a.house_number)

        if a.subway:
            self._fill_autocomplete_multi(sec, "subway", a.subway)
        if a.guide:
            self._fill_autocomplete_multi(sec, "guide", a.guide)
        if a.condo_complex:
            self._fill_autocomplete(sec, "condo_complex", a.condo_complex)

    def _fill_autocomplete(self, section, key: str, value: str) -> None:
        label = self._expected_label(key) or value  # label обязателен, но пусть будет fallback
        ctrl = self._find_control_fast_with_label_check(section, key)
        if not ctrl:
            ctrl = self._find_control_by_label(section, label)
        if not ctrl:
            logger.warning(f"can't find autocomplete control for {key}: {value}")

        # input внутри Autocomplete
        inp = ctrl
        if inp.evaluate("el => el.tagName.toLowerCase()") != "input":
            cand = ctrl.locator("css=input").first
            if cand.count() > 0:
                inp = cand

        inp.click()
        inp.fill(value)

        # ждём выпадающий список и выбираем первый подходящий
        # (в MUI обычно появляется listbox; если его нет — Enter часто подтверждает)
        try:
            self._wait_suggestions()
            inp.press("ArrowDown")
            inp.press("Enter")
        except Exception:
            try:
                inp.press("Enter")
            except Exception:
                pass

        self._mark_touched(inp)

    def _fill_autocomplete_multi(self, section, key: str, values: Sequence[str]) -> None:
        label = self._expected_label(key)
        ctrl = self._find_control_fast_with_label_check(section, key) or (self._find_control_by_label(section, label) if label else None)
        if not ctrl:
            return

        inp = ctrl.locator("css=input").first if ctrl.locator("css=input").count() else ctrl
        for v in values:
            inp.click()
            inp.fill(v)
            try:
                self._wait_suggestions()
                inp.press("ArrowDown")
                inp.press("Enter")
            except Exception:
                try:
                    inp.press("Enter")
                except Exception:
                    pass
        self._mark_touched(inp)

    def _fill_by_label(self, root, section: str, key: str, value: str) -> None:
        sec = self._section(root, section)
        label = self._expected_label(key) or key
        ctrl = self._find_control_fast_with_label_check(sec, key) or self._find_control_by_label(sec, label)
        if not ctrl:
            return

        tag = ctrl.evaluate("el => el.tagName.toLowerCase()")
        if tag not in ("input", "textarea"):
            inner = ctrl.locator("css=input, textarea").first
            if inner.count() > 0:
                ctrl = inner

        ctrl.click()
        ctrl.fill(value)
        self._mark_touched(ctrl)

    def _fill_select_or_text(self, root, section: str, key: str, value: str) -> None:
        """
        Если это select-кнопка (role=button/listbox) — кликаем и выбираем option.
        Иначе — просто fill как текст.
        """
        sec = self._section(root, section)
        label = self._expected_label(key) or key

        ctrl = self._find_control_fast_with_label_check(sec, key) or self._find_control_by_label(sec, label)
        if not ctrl:
            return

        role = ctrl.get_attribute("role")
        tag = None
        try:
            tag = ctrl.evaluate("el => el.tagName.toLowerCase()")
        except Exception:
            pass

        if role in ("button", "combobox") or tag == "select":
            ctrl.click()
            # варианты чаще всего role=option или li
            opt = self.page.locator(f"[role='option']:has-text('{value}')").first
            if opt.count() == 0:
                opt = self.page.locator(f"li:has-text('{value}')").first
            try:
                opt.click(timeout=2500)
            except Exception:
                # fallback: набрать + Enter
                try:
                    inp = sec.locator("css=input").first
                    inp.fill(value)
                    inp.press("Enter")
                except Exception:
                    pass
            self._mark_touched(ctrl)
            return

        # текстовый ввод
        self._fill_by_label(root, section, key, value)

    def _set_checkbox_by_label_if_present(self, root, section: str, key: str, value: bool) -> None:
        sec = self._section(root, section)
        label = self._expected_label(key)
        if not label:
            return

        # ищем контрол по тексту рядом с чекбоксом/свитчем
        row = sec.locator("xpath=.//*[contains(normalize-space(.), $t)]", t=label).first
        if row.count() == 0:
            return

        cb = row.locator("css=input[type='checkbox']").first
        if cb.count() == 0:
            # иногда свитч без input — тогда кликаем по самому ряду
            if value:
                row.click()
            return

        try:
            checked = cb.is_checked()
            if checked != value:
                if value:
                    cb.check()
                else:
                    cb.uncheck()
        except Exception:
            pass

    def _click_section_toggle(self, root, section_h6: str) -> None:
        """
        "Додаткові параметри" у тебя как кнопка, раскрывающая доп.поля.
        Обычно это h6 + кнопка рядом; кликаем по зоне секции/заголовку.
        """
        sec = self._section(root, section_h6)
        try:
            sec.locator("xpath=.//h6").first.click()
        except Exception:
            try:
                sec.click()
            except Exception:
                pass

    def _open_checklist_and_check(self, root, section: str, key: str, items: Iterable[str]) -> None:
        """
        without_power_supply / accessibility: кнопка -> список чекбоксов (по твоим словам).
        Реализация: кликаем по полю (по label), потом кликаем чекбоксы по тексту.
        """
        sec = self._section(root, section)
        label = self._expected_label(key) or key

        # Кнопка открытия списка — обычно это поле/контрол в FormControl
        opener = self._find_control_fast_with_label_check(sec, key) or self._find_control_by_label(sec, label)
        if opener:
            try:
                opener.click()
            except Exception:
                pass

        for item in items:
            # чекбокс пункт: текст + input[type=checkbox] где-то рядом
            node = self.page.locator("xpath=//*[contains(normalize-space(.), $t)]", t=str(item)).first
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

    def _set_multiselect_or_checklist(self, root, section: str, key: str, values: Sequence[str]) -> None:
        """
        Универсально: пытаемся как Autocomplete multi, если не получилось — как checklist.
        """
        sec = self._section(root, section)
        label = self._expected_label(key)
        if not label:
            return

        ctrl = self._find_control_fast_with_label_check(sec, key) or self._find_control_by_label(sec, label)
        if not ctrl:
            return

        # пробуем как autocomplete (input + Enter)
        inp = ctrl.locator("css=input").first if ctrl.locator("css=input").count() else None
        if inp:
            ok = True
            for v in values:
                try:
                    inp.click()
                    inp.fill(str(v))
                    self._wait_suggestions()
                    inp.press("ArrowDown")
                    inp.press("Enter")
                except Exception:
                    ok = False
                    break
            if ok:
                self._mark_touched(inp)
                return

        # иначе checklist
        self._open_checklist_and_check(root, section, key, values)

    # ---------------- photo blocks ----------------
    def _fill_photo_blocks(self, root, offer: Offer) -> None:
        """
        В каждом блоке есть textarea Опис и отдельный input[type=file] hidden:contentReference[oaicite:11]{index=11}:contentReference[oaicite:12]{index=12}
        Так как названия блоков у тебя есть в offer_name_mapping/offer_mapping, будем кликать таб/заголовок блока по h6.
        """
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

            # описание
            if block.description:
                ta = sec.locator(f"textarea[name='{desc_name}']").first
                if ta.count() == 0:
                    ta = sec.locator("textarea").first
                try:
                    ta.fill(block.description)
                    self._mark_touched(ta)
                except Exception:
                    pass

            # загрузка фото
            if block.photos:
                files = [p for p in block.photos if p and os.path.exists(p)]
                if files:
                    fi = sec.locator("input[type='file']").first
                    try:
                        fi.set_input_files(files)
                    except Exception:
                        pass

    # ---------------- required validation ----------------
    def _mark_touched(self, ctrl) -> None:
        """
        Помечаем, что это поле мы заполняли (атрибут на DOM).
        Потом проверим обязательные именно среди затронутых.
        """
        try:
            ctrl.evaluate("el => el.setAttribute('data-rieltor-touched','1')")
        except Exception:
            pass

    def _is_required_control(self, ctrl) -> bool:
        """
        required если:
          - у label есть asterisk (*) (MuiFormLabel-asterisk):contentReference[oaicite:13]{index=13}
          - или class содержит Mui-required / -required
          - или input имеет required атрибут
        """
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

        # ищем label в предках
        try:
            form = ctrl.locator("xpath=ancestor::div[contains(@class,'MuiFormControl-root')][1]").first
            lbl = form.locator("css=label").first
            if lbl.count():
                lbl_cls = lbl.get_attribute("class") or ""
                if "Mui-required" in lbl_cls:
                    return True
                # наличие span asterisk
                if lbl.locator("css=span.MuiFormLabel-asterisk").count():
                    return True
                # или в тексте label есть *
                if "*" in (lbl.inner_text() or ""):
                    return True
        except Exception:
            pass

        return False

    def _control_value_is_empty(self, ctrl) -> bool:
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

        # input/select/combobox
        try:
            if ctrl.evaluate("el => el.tagName.toLowerCase()") != "input":
                inner = ctrl.locator("css=input, textarea").first
                if inner.count():
                    ctrl = inner
            v = ctrl.input_value()
            return not (v and v.strip())
        except Exception:
            return True

    def _assert_required_filled(self, root) -> None:
        """
        Проверяем только те контролы, которые мы пометили data-rieltor-touched.
        Если required и пустой — кидаем RequiredFieldError.
        """
        touched = root.locator("[data-rieltor-touched='1']")
        n = touched.count()
        errors = []

        for i in range(n):
            ctrl = touched.nth(i)
            if not self._is_required_control(ctrl):
                continue
            if self._control_value_is_empty(ctrl):
                # попробуем достать label текст
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
            raise RequiredFieldError("Не заполнены обязательные поля: " + ", ".join(errors))

    # ---------------- misc ----------------
    def _wait_suggestions(self) -> None:
        """
        Для Autocomplete ждём появления listbox или хотя бы исчезновения "loading".
        """
        assert self.page
        p = self.page
        # listbox может не быть всегда (в статике не видно), поэтому мягко
        try:
            p.wait_for_selector("[role='listbox'], ul[role='listbox']", timeout=2000)
        except PWTimeoutError:
            pass

    @staticmethod
    def _deal_text(offer_type: Any) -> str:
        v = (str(offer_type.value) if isinstance(offer_type, Enum) else str(offer_type)).lower()
        if "прод" in v:
            return "продаж"
        if "орен" in v or "аренд" in v:
            return "оренда"
        return v

    @staticmethod
    def _truthy_fields_as_labels(dc_obj) -> list[str]:
        """
        Для dataclass типа WithoutPowerSupply, где поля True/False.
        Возвращаем имена полей со значением True.
        """
        if not is_dataclass(dc_obj):
            return []
        out = []
        for f in dc_obj.__dataclass_fields__.keys():
            if getattr(dc_obj, f, None) is True:
                out.append(f)
        return out





def main():
    pass

if __name__ == '__main__':
    from dotenv import load_dotenv
    import os

    load_dotenv()

    new_offer = Offer(
        offer_type=OfferType.SALE,
        address=Address(
            region="Київська область",
            city="Київ",
            district="Шевченківський",
            street="вул. Дегтярівська",
            house_number="17",
            condo_complex="ЖК Creator City",
        ),
        price=182000,
        currency=Currency.USD,
        property_type=PropertyType.APARTMENT,
        room_layout=RoomLayout.STUDIO,
        rooms=1,
        floor=3,
        floors_total=25,
        condition=Condition.RENOVATED,
        total_area=45,
        living_area=17,
        kitchen_area=15,
        without_power_supply=WithoutPowerSupply(
            gas=False,
            water=False,
            sewer=False,
        ),
        apartment=PhotoBlock(
            description="""Пропонується ексклюзивна однокімнатна квартира з авторським ремонтом та повним меблюванням у сучасному житловому комплексі Бізнес-класу Creator City - символі нового рівня комфорту та стилю в серці Шевченківського району. Квартира створена для тих, хто цінує простір, естетику та технологічність. Тут продумана кожна деталь - від планування до інженерних рішень. За адресою Дегтярівська вул., 17.
            - Авторський ремонт 2026 року
            - В квартирі ніхто не проживав
            - Загальна площа 45 м.кв.
            - Безпечний 3 поверх / 25 (з видом у двір)
            - Чудовий інвестиційний варіант

            Повністю укомплектована меблями та всією необхідною технікою для життя без зайвих турбот: вбудований холодильник, індукційна плита, духова шафа, мікрохвильова піч, посудомийна машина, пральна та сушильна машини, телевізор, витяжка, бойлер. Додатково встановлені система очищення води та центральне кондиціонування, що забезпечує комфорт у будь-яку пору року.

            ЖК Creator City є концепція «місто в місті» - вся необхідна для життя інфраструктура знаходиться на території комплексу. Для безпеки майбутніх мешканців в громадських місцях встановлять камери відеоспостереження, внутрішні двори огородять парканом, а увійти в під'їзд та ліфт можна буде тільки з картою-пропуском. Для дітей різного віку розмістять кілька ігрових комплексів, для спортсменів — вуличні тренажери і футбольне поле, а родзинкою комплексу стане власний ландшафтний парк площею 2 га з водоймою. Щоб комфортному відпочинку не заважали автомобілі, забудовник передбачив підземний дворівневий паркінг з ліфтом.

            Локація - ще одна сильна сторона. Поруч зелений парк імені Івана Багряного, Київський зоопарк, метро Лук’янівська та Шулявська, КПІ, інноваційний простір Unit City, житлові комплекси Crystal Park Tower та інші знакові об’єкти району. Тут зручно жити, працювати й відпочивати.

            Це не просто нерухомість - це готовий простір для життя, куди можна заїхати з валізою і відразу відчути себе вдома.
            Запрошую на перегляд, щоб ви змогли відчути цю атмосферу особисто.""",
            photos=['offers/pics/photo_2025-12-09_02-43-25.jpg', 'offers/pics/photo_2025-12-09_02-44-14.jpg'],
        )
    )

    publisher = RieltorOfferPosterV2(phone=os.getenv("PHONE"), password=os.getenv("PASSWORD"), headless=False)
    publisher.create_offer_draft(new_offer)

    main()