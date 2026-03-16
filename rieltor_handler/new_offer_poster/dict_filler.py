from __future__ import annotations

import time
from typing import Any, Dict, List

from playwright.sync_api import Locator, Page

from schemas import load_offer_schema
from setup_logger import setup_logger

from .address import AddressMixin
from .autocomplete import AutocompleteMixin
from .fields import FieldsMixin
from .mappings import MappingMixin
from .misc import deal_text
from .photos import _LABEL_DESCRIPTION, _LABEL_VIDEO_URL, PhotosMixin
from .structure import StructureMixin
from .validation import FormValidationError, ValidationMixin

logger = setup_logger(__name__)

# Photo block keys (offer_data keys that contain photo/description dicts)
_PHOTO_BLOCK_KEYS = frozenset({"apartment", "interior", "layout", "yard", "infrastructure"})

# Section names used across all property type schemas for photos/description
_PHOTO_SECTION_NAMES = ("Опис, фотографії, відеотур", "Фото, відео")

# Keys in offer_data that are handled specially (not schema fields)
_SPECIAL_KEYS = frozenset(
    {
        "offer_type",
        "property_type",
        "article",
        "advertising",
        "photo_download_link",
        "personal_notes",
        "address",
        "public_link",
        "responsible_person",
    }
)


class DictOfferFormFiller(
    StructureMixin,
    MappingMixin,
    AutocompleteMixin,
    FieldsMixin,
    AddressMixin,
    PhotosMixin,
    ValidationMixin,
):
    """Заповнювач форми оголошення на основі словника — версія зі схемою.

    Заповнює лише форму 'Нове оголошення' на /offers/create за допомогою словника даних.
    Метадані полів беруться з JSON-схем у schemas/schema_dump/.
    Передбачається, що сторінка вже відкрита і користувач авторизований.

    Ключі offer_data — українські підписи зі схеми
    (напр. "Число кімнат", "Поверх"), крім декількох спеціальних ключів
    ("offer_type", "property_type", "address", ключі фото-блоків тощо).
    """

    CREATE_URL = "https://my.rieltor.ua/offers/create"
    ROOT_H5_TEXT = "Нове оголошення"
    MANAGEMENT_URL_GLOB = "**/offers/management**"

    def __init__(
        self,
        page: Page,
        property_type: str = "Квартира",
        deal_type: str = "Продаж",
        debug: bool = False,
    ) -> None:
        self.page = page
        self.property_type = property_type
        self.deal_type = deal_type
        self.last_saved_offer_id: str | int | None = None

        if debug:
            logger.setLevel("DEBUG")

        # Load schema and build lookups
        self._schema = load_offer_schema(deal_type, property_type)

        # Map photo block keys → the actual photo/description section in the schema.
        # All schemas use a single section (e.g. "Опис, фотографії, відеотур"),
        # NOT multiple "Блок N з 5:" sections.
        self._photo_block_sections: dict[str, str] = {}
        photo_section = next(
            (n for n in self._schema["navigation"] if n in _PHOTO_SECTION_NAMES),
            None,
        )
        if photo_section:
            for key in _PHOTO_BLOCK_KEYS:
                self._photo_block_sections[key] = photo_section

        logger.debug(
            "Схему завантажено: %d полів, фото-блоки: %s",
            len(self._schema["fields"]),
            self._photo_block_sections,
        )

    # ---------- description enrichment ----------
    def _enrich_offer_data_from_description(self, offer_data: dict) -> None:
        """Re-analyze description text to fill fields missed during CRM collection.

        Runs DescriptionAnalyzer against the schema loaded for this property/deal type.
        Only adds fields that are not already present in offer_data.
        """
        from crm_data_parser.description_analyzer import DescriptionAnalyzer

        desc = (offer_data.get("apartment") or {}).get("description") or ""
        if not desc:
            return

        analyzer = DescriptionAnalyzer(self._schema["fields"])
        extra = analyzer.analyze(desc, offer_data)
        merged = [k for k, v in extra.items() if k not in offer_data and v is not None]
        for k in merged:
            offer_data[k] = extra[k]
        if merged:
            logger.info("Аналіз опису при постингу: додано %d полів: %s", len(merged), merged)

    # ---------- public API ----------
    def open(self) -> None:
        """Відкриває сторінку створення оголошення."""
        self.page.goto(self.CREATE_URL, wait_until="domcontentloaded")
        # Wait for MUI to fully render (h6 sections + card buttons)
        try:
            self.page.locator("h6", has_text="Тип угоди").first.wait_for(state="visible", timeout=15_000)
            self.page.wait_for_timeout(1500)
        except Exception:
            pass
        logger.info("Сторінку створення оголошення відкрито")

    def create_offer_draft(self, offer_data: dict) -> None:
        """Заповнює форму зі словника даних — версія зі схемою.

        Args:
            offer_data: Словник з полями оголошення. Ключі — українські підписи
                зі схеми (напр. "Число кімнат", "Ціна") та спеціальні ключі
                ("offer_type", "property_type", "address", фото-блоки).
        """
        # Re-run description analysis to fill any fields missed during CRM collection
        self._enrich_offer_data_from_description(offer_data)

        self.open()
        root = self._new_offer_root()
        self.last_saved_offer_id = offer_data.get("article")
        logger.info("Початок заповнення чернетки оголошення (на основі словника та схеми)")

        state = {
            "address_filled": False,
            "additional_opened": False,
            "photos_filled": False,
        }

        # ── Phase 1: offer_type → property_type → address (strict order) ──
        if not self._is_empty_value(offer_data.get("offer_type")):
            self._click_box_button_in_section(root, "Тип угоди", deal_text(offer_data["offer_type"]))

        if not self._is_empty_value(offer_data.get("property_type")):
            self._click_box_button_in_section(
                root,
                "Тип нерухомості",
                self._to_text(offer_data["property_type"]).lower(),
            )

        address_data = offer_data.get("address")
        if isinstance(address_data, dict) and not self._is_empty_value(address_data):
            self._fill_address_from_dict(root, address_data)
            state["address_filled"] = True

        # ── Phase 2: all remaining fields ──
        for key, value in offer_data.items():
            if self._is_empty_value(value):
                continue

            # Skip already-handled phase-1 keys
            if key in ("offer_type", "property_type", "address"):
                continue

            # ── Special: photo blocks ──
            if key in self._photo_block_sections:
                if not state["photos_filled"]:
                    self._fill_photos_from_dict(root, offer_data)
                    state["photos_filled"] = True
                continue

            # ── Special: personal_notes ──
            if key == "personal_notes":
                self._fill_personal_notes(root, self._to_text(value))
                continue

            # ── Skip non-schema special keys ──
            if key in _SPECIAL_KEYS:
                continue

            # ── Schema field lookup by label ──
            label_lower = key.lower().strip()
            field_info = self._schema["label_to_field"].get(label_lower)
            if not field_info:
                logger.debug("Ключ '%s' відсутній у схемі, пропуск", key)
                continue

            section = self._schema["label_to_section"].get(label_lower, "")
            widget = self._schema["label_to_widget"].get(label_lower, field_info.get("widget", "text"))

            # Open "Додаткові параметри" if needed (lazy, once)
            if not state["additional_opened"] and "Додаткові параметри" in self._schema["navigation"]:
                if self._is_additional_param(field_info):
                    self._click_section_toggle(root, "Додаткові параметри")
                    state["additional_opened"] = True

            self._fill_field_from_dict(root, section, key, value, widget)

        # Map error handling (only if address was filled)
        if state["address_filled"] and self._map_error_visible():
            self._handle_map_error(root, offer_data.get("address", {}))

        # If commission field is not in data, set it to "Немає" to avoid
        # the site's default "Є" which requires filling child fields
        _COMMISSION_LABEL = "Комісія з покупця/орендатора"
        if _COMMISSION_LABEL not in offer_data:
            commission_field = self._schema["label_to_field"].get(_COMMISSION_LABEL.lower().strip())
            if commission_field:
                self._set_commission_no(root, _COMMISSION_LABEL)

        # Required validation
        self._assert_required_filled(root)

        # Let the form settle before save (async validations, map pin, etc.)
        try:
            self.page.wait_for_timeout(2000)
        except Exception:
            time.sleep(2)

        logger.info("Чернетку оголошення заповнено (на основі словника та схеми)")

    def _is_additional_param(self, field_info: dict) -> bool:
        """Перевіряє, чи належить поле до згортної секції 'Додаткові параметри'.

        Це поля в 'Інформація про об'єкт', що з'являються після перемикача
        у формі (зазвичай field_index >= 16: Опалення, Гаряча вода тощо).
        """
        if field_info.get("section") != "Інформація про об'єкт":
            return False
        idx = field_info.get("meta", {}).get("field_index")
        if idx is None:
            return True  # conditional fields (no index) are usually additional
        return idx >= 16

    def _is_empty_value(self, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str) and not value.strip():
            return True
        if isinstance(value, (list, tuple, set)) and len(value) == 0:
            return True
        if isinstance(value, dict) and len(value) == 0:
            return True
        return False

    def _fill_field_from_dict(
        self,
        root: Locator,
        section: str,
        key: str,
        value: Any,
        widget: str | None,
    ) -> None:
        """Заповнює одне поле за допомогою обробника, специфічного для типу віджету."""
        if widget == "box_select":
            self._click_box_button_in_section(root, section, self._to_text(value).lower())
            return

        if widget == "text_autocomplete":
            sec = self._section(root, section)
            self._fill_autocomplete(sec, key, self._to_text(value), next_key=None)
            return

        if widget == "autocomplete_multi":
            sec = self._section(root, section)
            items = value if isinstance(value, (list, tuple)) else [value]
            self._fill_autocomplete_multi(sec, key, [self._to_text(v) for v in items])
            return

        if widget == "checkbox":
            self._set_checkbox_by_label_if_present(root, section, key, bool(value))
            return

        if widget == "radio":
            if isinstance(value, bool):
                desired = "Так" if value else "Ні"
            else:
                desired = self._to_text(value)
            self._fill_select_or_text(root, section, key, desired)
            return

        if widget == "select":
            self._fill_select_or_text(root, section, key, self._to_text(value))
            return

        if widget in ("text", "multiline_text", "datetime"):
            self._fill_by_label(root, section, key, self._to_text(value))
            return

        if widget == "file":
            self._upload_file_in_section(root, section, key, value)
            return

        if widget == "checklist":
            items = self._checklist_items(key, value)
            if items:
                self._open_checklist_and_check(root, section, key, items)
            return

        # Unknown widget → try generic fill
        self._fill_by_label(root, section, key, self._to_text(value))

    # ── Address ──

    def _fill_address_from_dict(self, root: Locator, address_data: dict) -> None:
        """Заповнює секцію адреси зі словника з українськими ключами-підписами.

        Args:
            address_data: Словник на зразок {"Місто": "Київ", "Новобудова": "ЖК Панорама",
                           "Район": "...", "Будинок": "1", "Метро": [...]}
        """
        sec = self._section(root, "Адреса об'єкта")

        if not address_data:
            logger.warning("Дані адреси порожні, пропуск")
            return

        # Helper to get value by Ukrainian label (case-insensitive)
        def _get(label: str) -> str | None:
            for k, v in address_data.items():
                if k.lower().strip() == label.lower().strip():
                    return v
            return None

        city = _get("Місто")
        condo = _get("Новобудова")
        district = _get("Район")
        street = _get("Вулиця")
        house = _get("Будинок")
        region = _get("Область")
        subway = _get("Метро")
        guide = _get("Орієнтир")

        # Normalize street and condo
        if street:
            s = str(street).strip()
            if s.startswith("вул.") or s.startswith("вулиця "):
                s = s.replace("вул.", "").replace("вулиця ", "").strip()
            street = s

        if condo:
            cc = str(condo).strip()
            if cc.startswith("ЖК "):
                cc = cc.replace("ЖК ", "").strip()
            condo = cc

        logger.info(
            "Заповнення адреси: місто=%s, ЖК=%s, район=%s, вулиця=%s, будинок=%s",
            city,
            condo,
            district,
            street,
            house,
        )

        # 1) CITY
        if city:
            next_key = "Новобудова" if condo else "Район"
            self._fill_autocomplete(sec, "Місто", city, next_key=next_key)

        # 2) CONDO COMPLEX (triggers autofill of district/street/house)
        condo_used = False
        if condo:
            self._fill_autocomplete(sec, "Новобудова", condo)
            condo_used = True
            try:
                self.page.wait_for_timeout(2000)
            except Exception:
                time.sleep(1.5)

        # 3) Early map error check — reselect house right after ЖК autofill
        if condo_used and house and self._map_error_visible():
            self._force_reselect_house_number(sec, house, house_label="Будинок")

        # 4) Fill remaining unfilled address fields
        if region:
            self._fill_autocomplete(sec, "Область", region)

        district_ctrl = self._find_control_by_label(sec, "Район")
        if district_ctrl and not self._control_has_value(district_ctrl):
            if district:
                self._fill_autocomplete(sec, "Район", district, next_key="Вулиця")

        street_ctrl = self._find_control_by_label(sec, "Вулиця")
        if street_ctrl and not self._control_has_value(street_ctrl):
            if street:
                self._fill_autocomplete(sec, "Вулиця", street, next_key="Будинок")

        house_ctrl = self._find_control_by_label(sec, "Будинок")
        if house_ctrl and not self._control_has_value(house_ctrl):
            if house:
                self._fill_autocomplete(sec, "Будинок", house)

        # 5) Final map error check — reselect house if still broken
        if house and self._map_error_visible():
            self._force_reselect_house_number(sec, house, house_label="Будинок")

        # 6) Multi-select fields
        if subway:
            items = subway if isinstance(subway, (list, tuple)) else [subway]
            self._fill_autocomplete_multi(sec, "Метро", [str(v) for v in items])
        if guide:
            items = guide if isinstance(guide, (list, tuple)) else [guide]
            self._fill_autocomplete_multi(sec, "Орієнтир", [str(v) for v in items])

    def _handle_map_error(self, root: Locator, address_data: dict) -> None:
        """Обробляє помилку піна карти — спроба прив'язати пін повторним вибором номера будинку."""
        logger.warning("Видно помилку піна карти — спроба прив'язки через повторний вибір номера будинку")

        if not address_data:
            return

        def _get(label: str) -> str | None:
            for k, v in address_data.items():
                if k.lower().strip() == label.lower().strip():
                    return v
            return None

        try:
            sec_addr = self._section(root, "Адреса об'єкта")
            house = _get("Будинок")
            if house:
                self._force_reselect_house_number(sec_addr, str(house), house_label="Будинок")
        except Exception:
            pass

        if self._map_error_visible():
            logger.error("Помилка піна карти досі видна — повторне заповнення адреси")
            self._fill_address_from_dict(root, address_data)

    # ── Personal notes ──

    def _fill_personal_notes(self, root: Locator, text: str) -> None:
        """Заповнює textarea особистих нотаток.

        Textarea нотаток не має <label>, тому знаходимо її через секцію h6
        і вже всередині неї шукаємо textarea.
        """
        text = (text or "").strip()
        if len(text) > 250:
            text = text[:250]
            logger.warning("personal_notes обрізано до 250 символів")
        if not text:
            return

        try:
            sec = self._section(root, "Особисті нотатки")
        except Exception:
            logger.warning("Не вдалось знайти секцію 'Особисті нотатки'")
            return

        textarea = sec.locator("css=textarea:not([aria-hidden='true'])").first
        if textarea.count() == 0:
            textarea = sec.locator("css=textarea").first

        if textarea.count() == 0:
            logger.warning("Textarea нотаток не знайдено в секції 'Особисті нотатки'")
            return

        try:
            cur = (textarea.input_value() or "").strip()
        except Exception:
            cur = ""

        if cur == text:
            logger.info("Нотатки пропущено: вже заповнено")
            return

        logger.info("Заповнення особистих нотаток (%d симв.)", len(text))
        try:
            textarea.click()
            textarea.fill(text)
        except Exception:
            try:
                textarea.press("Control+A")
                textarea.press("Backspace")
                textarea.type(text, delay=10)
            except Exception:
                logger.exception("Не вдалось заповнити особисті нотатки")
                return

        self._mark_touched(textarea)

    # ── Commission radio ──

    def _set_commission_no(self, root: Locator, label: str) -> None:
        """Встановлює радіокнопку комісії у 'Немає' прямим кліком по label.

        Структура DOM: текст label знаходиться у <p>, що є сусідом
        MuiFormControl-root, обидва загорнуті у MuiBox-root:
            MuiBox-root
              ├── <p>Комісія з покупця/орендатора</p>
              └── MuiBox-root
                    └── MuiFormControl-root
                          └── radiogroup
        """
        try:
            sec = self._section(root, "Основні параметри")
        except Exception:
            logger.warning("Не вдалось знайти секцію 'Основні параметри' для радіокнопки комісії")
            return

        lit = self._xpath_literal(label)
        # Find the outer MuiBox-root that contains both the <p> label and a radiogroup
        wrapper = sec.locator(
            f"xpath=.//div[contains(@class,'MuiBox-root')]"
            f"[.//p[contains(normalize-space(.), {lit})]]"
            f"[.//div[@role='radiogroup']]"
        ).first

        if wrapper.count() == 0:
            logger.warning("Обгортку радіокнопки комісії не знайдено для '%s'", label)
            return

        nemaye = wrapper.locator("xpath=.//label[contains(normalize-space(.), 'Немає')]").first
        if nemaye.count():
            nemaye.click()
            logger.info("Встановлено '%s' у 'Немає' (дані комісії не надано)", label)
        else:
            logger.warning("Опція 'Немає' не знайдена у радіокнопці комісії для '%s'", label)

    # ── Photos ──

    def _fill_photos_from_dict(self, root: Locator, offer_data: dict) -> None:
        """Заповнює фото-блоки, що мають дані.

        Ключі фото-блоків ("apartment", "interior" тощо) відповідають
        заголовкам секцій зі схеми навігації ("Блок 1 з 5: Про квартиру" тощо).
        """
        for key, section_title in self._photo_block_sections.items():
            photo_block = offer_data.get(key)
            if not photo_block or not isinstance(photo_block, dict):
                continue

            desc = str(photo_block.get("description", "")).strip()
            video = str(photo_block.get("video_url", "")).strip()
            photos = photo_block.get("photos", [])

            if not (desc or video or photos):
                continue

            sec = self._section(root, section_title)
            self._ensure_photo_block_open(sec)

            if desc:
                self._fill_text_in_photo_section(sec, _LABEL_DESCRIPTION, desc)

            # video_url exists only in the first block
            if key == "apartment" and video:
                self._fill_text_in_photo_section(sec, _LABEL_VIDEO_URL, video)
            elif video and key != "apartment":
                logger.debug(
                    "PhotoBlock '%s': video_url задано, але UI підтримує його лише у першому блоці — пропуск",
                    key,
                )

            if photos:
                self._upload_photos_in_photo_section(sec, list(photos))

    # ── Checklists ──

    def _checklist_items(self, key: str, value: Any) -> list[str]:
        """Перетворює значення чекліста на UI-підписи.

        У новому форматі зі схемою значення чеклістів у offer_data вже є
        українськими UI-підписами (з опцій полів схеми).
        """
        if isinstance(value, (list, tuple, set)):
            return [str(v) for v in value if str(v).strip()]
        return []

    # ── Save / Publish ──

    def _submit_and_get_report(
        self,
        *,
        publish_immediately: bool,
        raise_on_errors: bool = False,
    ) -> list[dict]:
        """Спільна логіка відправки: збереження чернетки або публікація."""
        action = "publish" if publish_immediately else "save"
        btn_text = "Опублікувати" if publish_immediately else "Зберегти чернетку"
        logger.info("Натискання кнопки %s", action)

        btn = self.page.locator(f"button:has-text('{btn_text}')").first
        btn.wait_for(state="visible", timeout=15_000)

        if publish_immediately:
            try:
                if btn.is_disabled():
                    logger.warning("Кнопка публікації неактивна")
                    root = self._new_offer_root()
                    report = self.collect_validation_report(root)
                    if report and raise_on_errors:
                        raise FormValidationError(report)
                    return report
            except Exception:
                pass

        # Collect pre-existing errors (map error, etc.) to exclude from post-click check
        pre_errors = set()
        try:
            root_pre = self._new_offer_root()
            for e in self.collect_validation_report(root_pre):
                pre_errors.add((e.get("section", ""), e.get("field", ""), e.get("message", "")))
        except Exception:
            pass

        # Scroll into view and click
        try:
            btn.scroll_into_view_if_needed()
        except Exception:
            pass

        try:
            btn.click()
        except Exception:
            try:
                btn.click(force=True)
            except Exception:
                logger.warning("Не вдалось натиснути кнопку '%s'", btn_text)

        # Quick check: if NEW validation errors appear, no API call was made
        try:
            self.page.wait_for_timeout(1500)
        except Exception:
            time.sleep(1.5)

        root_check = self._new_offer_root()
        all_errors = self.collect_validation_report(root_check)
        new_errors = [
            e for e in all_errors if (e.get("section", ""), e.get("field", ""), e.get("message", "")) not in pre_errors
        ]
        if new_errors:
            logger.warning("Помилки валідації на стороні клієнта після %s:", action)
            for err in new_errors:
                logger.error(
                    "  Помилка валідації: [%s] %s — %s",
                    err.get("section", ""),
                    err.get("field", ""),
                    err.get("message", ""),
                )
            if raise_on_errors:
                raise FormValidationError(new_errors)
            return new_errors

        # No new client-side errors — wait for redirect

        if publish_immediately:
            try:
                dlg = self.page.locator("css=[role='dialog']").first
                if dlg.count():
                    confirm = dlg.locator("button:has-text('Опублікувати')").first
                    if confirm.count():
                        try:
                            confirm.click()
                        except Exception:
                            try:
                                confirm.click(force=True)
                            except Exception:
                                pass
            except Exception:
                pass

        try:
            self.page.wait_for_url(self.MANAGEMENT_URL_GLOB, timeout=60_000)
        except Exception:
            pass

        if "/offers/management" in (self.page.url or ""):
            logger.info("Перенаправлено до управління оголошеннями: %s", self.page.url)
            return []

        # Still on the form — check for server-side validation errors
        try:
            self.page.wait_for_timeout(900)
        except Exception:
            pass

        root = self._new_offer_root()
        report = self.collect_validation_report(root)
        if report:
            logger.warning("Помилки валідації після %s:", action)
            for err in report:
                logger.error(
                    "  [%s] %s — %s",
                    err.get("section", ""),
                    err.get("field", ""),
                    err.get("message", ""),
                )
            if raise_on_errors:
                raise FormValidationError(report)
        else:
            logger.warning(
                "%s завершено без перенаправлення та без видимих помилок валідації",
                action,
            )

        return report

    # ── Public API (thin wrappers) ──

    def save(self) -> None:
        self._submit_and_get_report(publish_immediately=False, raise_on_errors=True)

    def save_and_get_report(self, publish_immediately: bool = False) -> list[dict]:
        return self._submit_and_get_report(publish_immediately=publish_immediately, raise_on_errors=False)

    def publish(self) -> None:
        self._submit_and_get_report(publish_immediately=True, raise_on_errors=True)

    def publish_and_get_report(self) -> list[dict]:
        return self._submit_and_get_report(publish_immediately=True, raise_on_errors=False)

    # ── Helpers ──

    def _upload_file_in_section(self, root: Locator, section: str, key: str, value: Any) -> None:
        """Завантажує файл(и) у секції."""
        files: list[str] = []
        if isinstance(value, str):
            if value.strip():
                files = [value.strip()]
        elif isinstance(value, (list, tuple, set)):
            files = [str(x).strip() for x in value if str(x).strip()]

        if not files:
            return

        sec = self._section(root, section)
        label = key  # key IS the label
        lit = self._xpath_literal(label)
        form = sec.locator(f"xpath=.//*[contains(normalize-space(.), {lit})]").first
        inp = (
            form.locator("css=input[type='file']").first
            if form.count()
            else sec.locator("css=input[type='file']").first
        )

        if inp.count() == 0:
            logger.warning("Поле завантаження файлу не знайдено для %s/%s", section, key)
            return

        try:
            logger.info("Завантаження %s/%s: %d файл(ів)", section, key, len(files))
            inp.set_input_files(files)
            self._mark_touched(inp)
        except Exception:
            logger.exception("Помилка завантаження файлів для %s/%s", section, key)
