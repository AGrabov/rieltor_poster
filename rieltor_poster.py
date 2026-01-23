# rieltor_poster.py
from __future__ import annotations

import ast
import os
from dataclasses import is_dataclass
from enum import Enum
from typing import Any, Iterable, Optional

from playwright.sync_api import Page, sync_playwright, TimeoutError as PWTimeoutError

from rieltor_dataclasses_01 import Offer, PhotoBlock
from offer_mapping import offer_mapping, offer_type_mapping, address_mapping, offer_name_mapping


class RieltorOfferPoster:
    """
    Постинг Offer в https://my.rieltor.ua/offers/create (Playwright sync).

    Ключевая идея:
      1) пытаемся найти контрол по offer_mapping (jss-class и т.п.)
      2) проверяем, что рядом/в контейнере есть текст offer_name_mapping[field_key]
      3) если проверка не прошла — ищем контрол по label-тексту (offer_name_mapping) и заполняем его
    """

    LOGIN_URL = "https://my.rieltor.ua/login"
    CREATE_URL = "https://my.rieltor.ua/offers/create"

    def __init__(
        self,
        email: str,
        password: str,
        headless: bool = False,
        slow_mo_ms: int = 0,
        default_timeout_ms: int = 30_000,
    ) -> None:
        self.email = email
        self.password = password
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.default_timeout_ms = default_timeout_ms

        self._p = None
        self._browser = None
        self._context = None
        self.page: Optional[Page] = None

    # ---------- lifecycle ----------
    def start(self) -> "RieltorOfferPoster":
        self._p = sync_playwright().start()
        self._browser = self._p.chromium.launch(headless=self.headless, slow_mo=self.slow_mo_ms)
        self._context = self._browser.new_context()
        self.page = self._context.new_page()
        self.page.set_default_timeout(self.default_timeout_ms)
        return self

    def close(self) -> None:
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

    def __enter__(self) -> "RieltorOfferPoster":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ---------- public API ----------
    def login(self) -> None:
        assert self.page
        page = self.page
        page.goto(self.LOGIN_URL, wait_until="domcontentloaded")
        page.fill("input[name='email']", self.email)
        page.fill("input[name='password']", self.password)
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")

    def create_offer_draft(self, offer: Offer) -> None:
        """
        Заполняет форму. Ничего не “публикует”, только ввод данных.
        """
        assert self.page
        page = self.page

        page.goto(self.CREATE_URL, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")

        # 1) Тип сделки (rent/sale) — отдельными кнопками
        self._set_offer_type(offer)

        # 2) Тип недвижимости
        self._fill_field("property_type", self._to_text(offer.property_type))

        # 3) Адрес (вложенная dataclass)
        self._open_section_if_needed("address")
        self._fill_address(offer)

        # 4) Основные параметры
        self._open_section_if_needed("main_params")
        self._fill_field("price", str(offer.price))
        self._fill_field("currency", self._to_text(offer.currency))

        self._fill_checkbox_like("assignment", bool(offer.assignment))
        self._fill_checkbox_like("buyer_commission", bool(offer.buyer_commission))

        if offer.commission is not None:
            self._fill_field("commission", str(offer.commission))
        if offer.commission_unit is not None:
            self._fill_field("commission_unit", self._to_text(offer.commission_unit))

        # 5) Информация об объекте
        self._open_section_if_needed("information")
        self._fill_field("room_layout", self._to_text(offer.room_layout))
        self._fill_field("rooms", str(offer.rooms))
        self._fill_field("floor", str(offer.floor))
        self._fill_field("floors_total", str(offer.floors_total))
        self._fill_field("condition", self._to_text(offer.condition))

        if offer.building_type:
            self._fill_field("building_type", offer.building_type)
        if offer.construction_technology:
            self._fill_field("construction_technology", offer.construction_technology)

        if offer.special_conditions:
            self._fill_multi("special_conditions", offer.special_conditions)

        if offer.construction_stage:
            self._fill_field("construction_stage", offer.construction_stage)

        self._fill_field("total_area", str(offer.total_area))
        self._fill_field("living_area", str(offer.living_area))
        self._fill_field("kitchen_area", str(offer.kitchen_area))

        if offer.year_built is not None:
            self._fill_field("year_built", str(offer.year_built))

        if offer.renewal_program is not None:
            self._fill_checkbox_like("renewal_program", bool(offer.renewal_program))

        # without_power_supply — вложенный набор булевых. На сайте, похоже, мультиселект/чипсы
        # поэтому конвертим True-поля в список названий и пихаем как multi.
        wps = offer.without_power_supply
        if wps and is_dataclass(wps):
            items = []
            for k in ("water", "gas", "heating", "internet", "elevator", "backup_power"):
                if getattr(wps, k, None) is True:
                    items.append(k)
            if items:
                self._fill_multi("without_power_supply", items)

        if offer.accessibility:
            self._fill_multi("accessibility", offer.accessibility)

        # 6) Доп. параметры — часто спрятаны за табом/кнопкой
        self._click_tab_like("additional_params")
        if offer.heating is not None:
            self._fill_checkbox_like("heating", bool(offer.heating))
        if offer.hot_water is not None:
            self._fill_checkbox_like("hot_water", bool(offer.hot_water))
        if offer.gas is not None:
            self._fill_checkbox_like("gas", bool(offer.gas))
        if offer.internet is not None:
            self._fill_checkbox_like("internet", bool(offer.internet))

        if offer.nearby:
            self._fill_multi("nearby", offer.nearby)

        if offer.apartment_type:
            self._fill_field("apartment_type", offer.apartment_type)
        if offer.ceiling_height is not None:
            self._fill_field("ceiling_height", str(offer.ceiling_height))
        if offer.windows_view:
            self._fill_field("windows_view", offer.windows_view)

        # replaned — bool, но в mapping лежит как поле; на сайте может быть чекбокс/селект
        self._fill_checkbox_like("replaned", bool(offer.replaned))

        if offer.bathroom:
            self._fill_field("bathroom", offer.bathroom)
        if offer.plumbing is not None:
            self._fill_checkbox_like("plumbing", bool(offer.plumbing))
        if offer.entrance_door:
            self._fill_field("entrance_door", offer.entrance_door)
        if offer.floor_covering:
            self._fill_field("floor_covering", offer.floor_covering)
        if offer.balconies is not None:
            self._fill_field("balconies", str(offer.balconies))
        if offer.windows_type:
            self._fill_field("windows_type", offer.windows_type)
        if offer.windows_condition:
            self._fill_field("windows_condition", offer.windows_condition)

        if offer.building_features:
            self._fill_multi("additional_features", offer.building_features)

        # 7) Блоки фото/описаний
        self._fill_photo_blocks(offer)

        # 8) Эксклюзив/личные заметки
        self._fill_checkbox_like("exlusive", bool(getattr(offer, "exlusive", False)))
        if offer.personal_notes:
            self._fill_field("personal_notes", offer.personal_notes)

    def save(self) -> None:
        """
        Нажимает “Зберегти” (если есть).
        """
        assert self.page
        page = self.page
        btn = page.locator("button:has-text('Зберегти')").first
        btn.click()

    # ---------- internals: mapping/locators ----------
    def _parse_mapping(self, key: str) -> dict:
        raw = offer_mapping.get(key) or address_mapping.get(key) or offer_type_mapping.get(key)
        if raw is None:
            raise KeyError(f"Mapping not found for key: {key}")
        if isinstance(raw, dict):
            return raw
        return ast.literal_eval(raw)

    @staticmethod
    def _to_text(v: Any) -> str:
        if isinstance(v, Enum):
            return str(v.value)
        return str(v)

    def _selector_from_attrs(self, attrs: dict) -> str:
        """
        Грубый CSS-конструктор:
          - class: 'a b' -> .a.b
          - class: ['a','b'] -> .a.b
          - id: '#id'
        Если кроме class/id есть attrs (type, tabindex) — добавим как [attr="..."].
        """
        cls = attrs.get("class")
        _id = attrs.get("id")

        parts = []
        if _id:
            parts.append(f"#{_id}")

        if cls:
            if isinstance(cls, str):
                classes = cls.split()
            else:
                classes = list(cls)
            parts.append("".join([f".{c}" for c in classes if c]))

        # остальные атрибуты
        for k, val in attrs.items():
            if k in ("class", "id"):
                continue
            if val is None:
                continue
            parts.append(f'[{k}="{val}"]')

        return "".join(parts) if parts else "*"

    def _expected_label(self, key: str) -> Optional[str]:
        return offer_name_mapping.get(key)

    def _verify_label_near(self, control, expected_label: str) -> bool:
        """
        Проверяем “где-то рядом” (в контейнере/предках) наличие текста expected_label.
        Это эвристика под MUI формы.
        """
        # 1) ищем текст в ближайшем “field container”
        # берем 4 уровня предков — обычно хватает
        for up in range(1, 5):
            try:
                ancestor = control.locator(f"xpath=ancestor::*[{up}]").first
                txt = (ancestor.inner_text(timeout=1500) or "").strip()
                if expected_label in txt:
                    return True
            except PWTimeoutError:
                pass
            except Exception:
                pass

        # 2) fallback: ближайший label
        try:
            lbl = control.locator("xpath=ancestor::*[self::label or self::div][1]").first
            txt = (lbl.inner_text(timeout=1500) or "").strip()
            return expected_label in txt
        except Exception:
            return False

    def _find_by_mapping_with_check(self, key: str):
        """
        1) по mapping -> locator
        2) если есть offer_name_mapping[key], проверяем, что лейбл рядом
        3) если не совпало — возвращаем None, чтобы сработал поиск по label
        """
        assert self.page
        attrs = self._parse_mapping(key)
        sel = self._selector_from_attrs(attrs)

        loc = self.page.locator(sel).first
        try:
            loc.wait_for(state="attached", timeout=2000)
        except Exception:
            return None

        expected = self._expected_label(key)
        if expected:
            if not self._verify_label_near(loc, expected):
                return None
        return loc

    def _find_by_label(self, key: str):
        """
        Пытаемся найти контрол по тексту label (offer_name_mapping[key]).
        Для MUI часто работает: найти элемент с текстом, затем в этом блоке input/textarea/[role=combobox]
        """
        assert self.page
        label = self._expected_label(key)
        if not label:
            return None

        # 1) точное/частичное совпадение текста
        candidates = self.page.locator(f"text={label}")
        if candidates.count() == 0:
            # иногда лейбл с двоеточием/переносами
            candidates = self.page.locator(f"xpath=//*[contains(normalize-space(.), {self._xpath_str(label)})]")

        if candidates.count() == 0:
            return None

        anchor = candidates.first

        # 2) пробуем найти input/textarea/select/combobox в ближайшем контейнере
        container = anchor.locator("xpath=ancestor::*[self::div or self::label][1]").first
        control = container.locator("css=input, textarea, select, [role='combobox']").first
        try:
            control.wait_for(state="attached", timeout=2000)
            return control
        except Exception:
            pass

        # 3) чуть шире: в предке уровнем выше
        container2 = anchor.locator("xpath=ancestor::div[1]").first
        control2 = container2.locator("css=input, textarea, select, [role='combobox']").first
        try:
            control2.wait_for(state="attached", timeout=2000)
            return control2
        except Exception:
            return None

    @staticmethod
    def _xpath_str(s: str) -> str:
        # безопасная строка для xpath literal
        if "'" not in s:
            return f"'{s}'"
        if '"' not in s:
            return f'"{s}"'
        parts = s.split("'")
        return "concat(" + ", ".join([f"'{p}'" if i == len(parts) - 1 else f"'{p}', \"'\"" for i, p in enumerate(parts)]) + ")"

    # ---------- internals: actions ----------
    def _open_section_if_needed(self, key: str) -> None:
        """
        Некоторые секции/аккордеоны могут быть свернуты.
        Если элемент по mapping кликабелен — попробуем кликнуть.
        """
        loc = self._find_by_mapping_with_check(key)
        if not loc:
            return
        try:
            loc.click(timeout=1000)
        except Exception:
            pass

    def _click_tab_like(self, key: str) -> None:
        loc = self._find_by_mapping_with_check(key) or self._find_by_label(key)
        if not loc:
            return
        try:
            loc.click()
        except Exception:
            pass

    def _set_offer_type(self, offer: Offer) -> None:
        """
        offer.offer_type: Enum OfferType со значениями "Продаж"/"Оренда"
        а offer_type_mapping у тебя: 'sale'/'rent' -> class
        """
        assert self.page
        v = self._to_text(offer.offer_type).lower()
        if "прод" in v:
            key = "sale"
        elif "орен" in v or "аренд" in v:
            key = "rent"
        else:
            # fallback: попробуем просто клик по label "Тип угоди"
            self._fill_field("offer_type", self._to_text(offer.offer_type))
            return

        attrs = ast.literal_eval(offer_type_mapping[key])
        sel = self._selector_from_attrs(attrs)
        btn = self.page.locator(sel).first
        btn.click()

    def _fill_address(self, offer: Offer) -> None:
        a = offer.address
        self._fill_field("region", a.region)
        self._fill_field("city", a.city)
        self._fill_field("district", a.district)
        self._fill_field("street", a.street)

        if a.subway:
            self._fill_multi("subway", a.subway)
        if a.guide:
            self._fill_multi("guide", a.guide)
        if a.condo_complex:
            self._fill_field("condo_complex", a.condo_complex)

    def _fill_field(self, key: str, value: str) -> None:
        """
        Универсальный ввод:
          - если select/combobox -> выбираем option по тексту
          - иначе fill() в input/textarea
        """
        assert self.page

        control = self._find_by_mapping_with_check(key) or self._find_by_label(key)
        if not control:
            # можно залогировать, но не падаем
            return

        # если это MUI Select/Autocomplete: часто role=combobox
        try:
            role = control.get_attribute("role")
        except Exception:
            role = None

        tag = None
        try:
            tag = control.evaluate("el => el.tagName.toLowerCase()")
        except Exception:
            pass

        if role == "combobox" or tag == "select":
            self._select_by_text(control, value)
            return

        # иногда кликаешь по div-контейнеру, а реальный input внутри
        if tag not in ("input", "textarea"):
            inner = control.locator("css=input, textarea, [role='combobox']").first
            if inner.count() > 0:
                control = inner

        try:
            control.click()
        except Exception:
            pass

        try:
            control.fill(str(value))
        except Exception:
            # fallback: ctrl+a + type
            try:
                control.press("Control+A")
                control.type(str(value))
            except Exception:
                pass

    def _fill_multi(self, key: str, values: Iterable[str]) -> None:
        """
        Мультиселект/чипсы: кликаем, по очереди вводим значение + Enter.
        """
        control = self._find_by_mapping_with_check(key) or self._find_by_label(key)
        if not control:
            return

        try:
            control.click()
        except Exception:
            pass

        # ищем реальный input внутри
        inp = control
        try:
            tag = inp.evaluate("el => el.tagName.toLowerCase()")
            if tag != "input":
                cand = control.locator("css=input").first
                if cand.count() > 0:
                    inp = cand
        except Exception:
            pass

        for v in values:
            try:
                inp.fill(str(v))
                inp.press("Enter")
            except Exception:
                try:
                    inp.type(str(v))
                    inp.press("Enter")
                except Exception:
                    pass

    def _fill_checkbox_like(self, key: str, value: bool) -> None:
        """
        Чекбоксы/свитчи:
          - если нашли input[type=checkbox] — приводим в нужное состояние
          - иначе просто click если надо включить (и пробуем понять текущее через aria-checked)
        """
        control = self._find_by_mapping_with_check(key) or self._find_by_label(key)
        if not control:
            return

        # checkbox внутри?
        cb = control
        if cb.get_attribute("type") != "checkbox":
            inner = control.locator("css=input[type='checkbox']").first
            if inner.count() > 0:
                cb = inner

        try:
            # Playwright умеет check/uncheck
            if cb.get_attribute("type") == "checkbox":
                if value:
                    cb.check()
                else:
                    cb.uncheck()
                return
        except Exception:
            pass

        # fallback по aria-checked
        try:
            aria = control.get_attribute("aria-checked")
            current = (aria == "true")
        except Exception:
            current = None

        if current is None:
            # просто кликаем если нужно True
            if value:
                try:
                    control.click()
                except Exception:
                    pass
            return

        if current != value:
            try:
                control.click()
            except Exception:
                pass

    def _select_by_text(self, control, text: str) -> None:
        """
        Для MUI-select: click -> появляется listbox -> click option by text.
        """
        assert self.page
        try:
            control.click()
        except Exception:
            pass

        # На MUI варианты часто: role="option" или li
        option = self.page.locator(f"[role='option']:has-text('{text}')").first
        if option.count() == 0:
            option = self.page.locator(f"li:has-text('{text}')").first

        try:
            option.click(timeout=3000)
        except Exception:
            # fallback: набрать и Enter
            try:
                control.type(text)
                control.press("Enter")
            except Exception:
                pass

    def _fill_photo_blocks(self, offer: Offer) -> None:
        """
        У тебя 5 блоков PhotoBlock: apartment/interior/layout/yard/infrastructure.
        В mapping есть ключи *_block (кнопки/табы). Внутренние поля фото/описания
        на форме не промаплены — поэтому делаем мягко:
          - переходим на блок
          - если есть textarea рядом (описание) — заполняем description
          - если находим input[type=file] — грузим photos
        """
        blocks = [
            ("apartment_block", getattr(offer, "apartment", None)),
            ("interior_block", getattr(offer, "interior", None)),
            ("layout_block", getattr(offer, "layout", None)),
            ("yard_block", getattr(offer, "yard", None)),
            ("infrastructure_block", getattr(offer, "infrastructure", None)),
        ]

        for tab_key, block in blocks:
            if not isinstance(block, PhotoBlock):
                continue
            if (not block.description) and (not block.photos):
                continue

            self._click_tab_like(tab_key)

            # description: первая textarea в пределах “активного” блока
            if block.description:
                try:
                    ta = self.page.locator("textarea").first
                    if ta.count() > 0:
                        ta.fill(block.description)
                except Exception:
                    pass

            # photos: любой file input (лучше искать ближе к блоку, но без DOM — так)
            if block.photos:
                files = [p for p in block.photos if p and os.path.exists(p)]
                if files:
                    try:
                        fi = self.page.locator("input[type='file']").first
                        if fi.count() > 0:
                            fi.set_input_files(files)
                    except Exception:
                        pass
