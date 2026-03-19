from __future__ import annotations

import time
from typing import Any

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
from ..rieltor_session import RieltorErrorPageException

logger = setup_logger(__name__)

_STREET_PREFIXES = (
    "вулиця ", "вул. ", "вул.",
    "проспект ", "просп. ", "просп.", "пр-т ",
    "бульвар ", "бульв. ", "бульв.",
    "площа ", "пл. ", "пл.",
    "провулок ", "пров. ", "пров.", "пер. ", "пер.",
    "шосе ",
    "набережна ",
    "узвіз ",
)


def _strip_street_prefix(value: str) -> str:
    """Видалити тип вулиці з початку рядка (вул., просп., бульв. тощо)."""
    s = value.strip()
    s_lower = s.lower()
    for prefix in _STREET_PREFIXES:
        if s_lower.startswith(prefix.lower()):
            return s[len(prefix):].strip()
    return s


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

_ПРИЗНАЧЕННЯ_DEFAULT_BY_PROPERTY: dict[str, str] = {
    "Комерційна": "Приміщення вільного призначення",
    "Ділянка": "Під забудову",
}

# CRM-specific values that don't match Rieltor options → map to closest equivalent
_SELECT_VALUE_MAP: dict[str, str] = {
    "котедж": "Будинок",
    "під чистову": "Без ремонту",
    "чорновий ремонт": "Без ремонту",
    "чорновий": "Без ремонту",
}


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
        self._last_offer_data: dict | None = None

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
    def _apply_required_defaults(self, offer_data: dict) -> None:
        """Set safe default values for required fields absent from offer_data.

        Only applies when the field exists in the current schema AND is not
        already set (by CRM parser or description analysis).
        Order matters: "Призначення" is resolved first so "Вид будівлі" can
        use it to pick the right default.
        """
        applied = []

        # 1) "Тип будови"
        fi = self._schema["label_to_field"].get("тип будови")
        if fi and fi["label"] not in offer_data:
            offer_data[fi["label"]] = "Готова будівля"
            applied.append(f"{fi['label']}='Готова будівля'")

        # 2) "Призначення" — must be resolved before "Вид будівлі"
        fi_pryz = self._schema["label_to_field"].get("призначення")
        if fi_pryz and fi_pryz["label"] not in offer_data:
            default = _ПРИЗНАЧЕННЯ_DEFAULT_BY_PROPERTY.get(self.property_type)
            if default:
                offer_data[fi_pryz["label"]] = default
                applied.append(f"{fi_pryz['label']}={default!r}")

        # 3) "Вид будівлі" — office → "Офісний центр", other commercial → "Окремо стояча будівля"
        fi = self._schema["label_to_field"].get("вид будівлі")
        if fi and fi["label"] not in offer_data:
            призначення_val = ""
            if fi_pryz:
                призначення_val = str(offer_data.get(fi_pryz["label"], "")).lower()
            vyd_default = (
                "Офісний центр"
                if "офіс" in призначення_val
                else "Окремо стояча будівля"
            )
            offer_data[fi["label"]] = vyd_default
            applied.append(f"{fi['label']}={vyd_default!r}")

        # 4) "Площа ділянки, соток" — required for Будинок; use "1" when not in CRM/description
        for _plot_lbl in ("Площа ділянки, соток", "Загальна площа, соток"):
            fi = self._schema["label_to_field"].get(_plot_lbl.lower())
            if fi and fi["label"] not in offer_data:
                offer_data[fi["label"]] = "1"
                applied.append(f"{fi['label']}='1' (placeholder — уточніть у CRM)")
                break

        # 5) "Тип будинку" — default to "Будинок" for Будинок property type when absent.
        #    The site's list includes "Будинок" as a valid option (confirmed in schema).
        fi = self._schema["label_to_field"].get("тип будинку")
        if fi and fi["label"] not in offer_data:
            if str(self.property_type).lower() in ("будинок", "таунхаус", "котедж"):
                options = fi.get("options", [])
                default_house_type = "Будинок" if not options else next(
                    (o for o in options if "будинок" in o.lower()), options[0] if options else "Будинок"
                )
                offer_data[fi["label"]] = default_house_type
                applied.append(f"{fi['label']}='{default_house_type}' (default)")

        # 6) "Поверховість" — default to 2 when absent; cap at 50 (site limit)
        #    For house/cottage types the site additionally validates ≤ 6.
        _HOUSE_MAX_FLOORS = 6
        _HOUSE_FLOOR_TYPES = {"будинок", "котедж", "таунхаус", "дача"}
        fi = self._schema["label_to_field"].get("поверховість")
        if fi:
            lbl = fi["label"]
            if lbl not in offer_data:
                offer_data[lbl] = 2
                applied.append(f"{lbl}=2 (default)")
            else:
                try:
                    floors = int(offer_data[lbl])
                    if floors > 50:
                        offer_data[lbl] = 2
                        applied.append(f"{lbl}: {floors}→2 (перевищував ліміт 50)")
                    elif (
                        str(self.property_type).lower() in _HOUSE_FLOOR_TYPES
                        and floors > _HOUSE_MAX_FLOORS
                    ):
                        offer_data[lbl] = _HOUSE_MAX_FLOORS
                        applied.append(
                            f"{lbl}: {floors}→{_HOUSE_MAX_FLOORS} (ліміт для {self.property_type})"
                        )
                except (TypeError, ValueError):
                    pass

        # 7) Площа кухні / Житлова площа — estimate from total area for residential types.
        # Mirrors the same logic in html_parser._fill_missing_with_defaults so that
        # objects collected before the parser fix also get the estimates at post time.
        _residential = {"квартира", "будинок", "таунхаус", "котедж"}
        if str(self.property_type).lower() in _residential:
            fi_kitchen = self._schema["label_to_field"].get("площа кухні, м²")
            fi_total = self._schema["label_to_field"].get("загальна площа, м²")
            fi_living = self._schema["label_to_field"].get("житлова площа, м²")
            if fi_kitchen and fi_total:
                kitchen_lbl = fi_kitchen["label"]
                total_lbl = fi_total["label"]
                if kitchen_lbl not in offer_data and total_lbl in offer_data:
                    try:
                        total_f = float(offer_data[total_lbl])
                        if total_f <= 40:
                            kitchen_est = 10
                        elif total_f <= 60:
                            kitchen_est = 12
                        elif total_f <= 80:
                            kitchen_est = 15
                        elif total_f <= 100:
                            kitchen_est = 20
                        elif total_f <= 130:
                            kitchen_est = 25
                        else:
                            kitchen_est = 30
                        # Cap kitchen so total >= living + kitchen (avoid "сума площ" validation error)
                        if fi_living:
                            _living_lbl = fi_living["label"]
                            if _living_lbl in offer_data:
                                try:
                                    _living_f = float(offer_data[_living_lbl])
                                    _max_kitchen = total_f - _living_f - 1
                                    if kitchen_est > _max_kitchen:
                                        kitchen_est = max(1, int(_max_kitchen))
                                        applied.append(
                                            f"{kitchen_lbl}: обрізано до {kitchen_est} "
                                            f"(загальна={total_f}, житлова={_living_f})"
                                        )
                                except (ValueError, TypeError):
                                    pass
                        if kitchen_est > 0:
                            offer_data[kitchen_lbl] = str(kitchen_est)
                            applied.append(f"{kitchen_lbl}='{kitchen_est}' (оцінено із {total_lbl}={total_f})")
                    except (ValueError, TypeError):
                        pass
            if fi_living and fi_kitchen and fi_total:
                living_lbl = fi_living["label"]
                kitchen_lbl = fi_kitchen["label"]
                total_lbl = fi_total["label"]
                if living_lbl not in offer_data and total_lbl in offer_data and kitchen_lbl in offer_data:
                    try:
                        living = round(float(offer_data[total_lbl]) - 1.4 * float(offer_data[kitchen_lbl]), 1)
                        if living > 0:
                            offer_data[living_lbl] = str(living)
                            applied.append(f"{living_lbl}='{living}' (обчислено)")
                    except (ValueError, TypeError):
                        pass

        # Estimate "Число кімнат" from area when absent for Квартира/Будинок
        fi_rooms = self._schema["label_to_field"].get("число кімнат")
        if fi_rooms and fi_rooms["label"] not in offer_data:
            _pt = self.property_type.lower()
            if _pt in ("квартира", "будинок", "таунхаус", "котедж"):
                fi_total = self._schema["label_to_field"].get("загальна площа, м²")
                fi_living = self._schema["label_to_field"].get("житлова площа, м²")
                _area_lbl = None
                _area_val = None
                for _fi in (fi_total, fi_living):
                    if _fi and _fi["label"] in offer_data:
                        try:
                            _area_val = float(offer_data[_fi["label"]])
                            _area_lbl = _fi["label"]
                            break
                        except (ValueError, TypeError):
                            pass
                if _area_val is not None:
                    if _area_val <= 35:
                        rooms_default = "1 кімната"
                    elif _area_val <= 55:
                        rooms_default = "2 кімнати"
                    elif _area_val <= 80:
                        rooms_default = "3 кімнати"
                    elif _area_val <= 100:
                        rooms_default = "4 кімнати"
                    elif _area_val <= 150:
                        rooms_default = "5 кімнат"
                    else:
                        rooms_default = "6 кімнат і більше"
                    # Align with schema options if available
                    options = fi_rooms.get("options", [])
                    if options and rooms_default not in options:
                        num = rooms_default.split()[0]
                        for opt in options:
                            if num in opt:
                                rooms_default = opt
                                break
                    offer_data[fi_rooms["label"]] = rooms_default
                    applied.append(
                        f"{fi_rooms['label']}='{rooms_default}' (оцінено з {_area_lbl}={_area_val})"
                    )
                else:
                    logger.warning(
                        "Відсутнє поле '%s' для %s — форма може не пройти валідацію",
                        fi_rooms["label"],
                        self.property_type,
                    )

        if applied:
            logger.info("Значення за замовчуванням: %s", applied)

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
    def _is_error_page(self) -> bool:
        """Перевіряє, чи поточна сторінка є сторінкою помилки ('Щось пішло не так')."""
        try:
            url = self.page.url or ""
            if "/error" in url or "/404" in url:
                return True
            # The error page always shows a "На головну" button — most reliable indicator.
            if self.page.locator("button:has-text('На головну')").count() > 0:
                return True
            # Also catch the 404 image alt attribute.
            if self.page.locator('[alt="404 bot"]').count() > 0:
                return True
            # Fallback: scan raw page text (handles React multi-node text rendering).
            try:
                content = self.page.content()
                if "Щось пішло не так" in content or "404 bot" in content:
                    return True
            except Exception:
                pass
            return False
        except Exception:
            return False

    def _raise_if_error_page(self) -> None:
        if self._is_error_page():
            url = self.page.url or "unknown"
            logger.error("Сайт повернув сторінку помилки: %s", url)
            try:
                btn = self.page.locator("button:has-text('На головну')").first
                if btn.count() > 0:
                    btn.click()
                    self.page.wait_for_load_state("domcontentloaded", timeout=10_000)
                    logger.info("Натиснуто 'На головну' після сторінки помилки")
            except Exception:
                pass
            raise RieltorErrorPageException(
                f"Сайт повернув сторінку помилки за адресою {url}. "
                "Сторінка недоступна або сесія закінчилась."
            )

    def open(self) -> None:
        """Відкриває сторінку створення оголошення."""
        # Strategy: navigate → clear storage → reload so React starts with empty storage.
        # Auth is stored in cookies on rieltor.ua, so clearing localStorage/sessionStorage is safe.
        self.page.goto(self.CREATE_URL, wait_until="domcontentloaded")
        try:
            self.page.evaluate("() => { window.localStorage.clear(); window.sessionStorage.clear(); }")
        except Exception:
            pass
        # Reload so the React app initialises with empty storage (no draft restore).
        self.page.goto(self.CREATE_URL, wait_until="domcontentloaded")
        self._raise_if_error_page()
        # Wait for MUI to fully render (h6 sections + card buttons)
        try:
            self.page.locator("h6", has_text="Тип угоди").first.wait_for(state="visible", timeout=15_000)
            self.page.wait_for_timeout(1500)
        except Exception:
            pass
        self._raise_if_error_page()
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
        # Apply safe defaults for required fields still missing after analysis
        self._apply_required_defaults(offer_data)
        # Store for use in error recovery at save/publish time
        self._last_offer_data = offer_data

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

        # Check after address fill — server errors sometimes surface here.
        self._raise_if_error_page()

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
                    self._raise_if_error_page()
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

            # Open "Додаткові параметри" if needed (lazy, once).
            # Triggered for: fields in "Інформація про об'єкт" with index ≥ 16
            # AND for fields whose schema section is literally "Додаткові параметри".
            if not state["additional_opened"] and "Додаткові параметри" in self._schema["navigation"]:
                if self._is_additional_param(field_info) or section == "Додаткові параметри":
                    self._click_section_toggle(root, "Додаткові параметри")
                    state["additional_opened"] = True

            self._fill_field_from_dict(root, section, key, value, widget)

        # ── Fill "Опис" as a regular schema field when it lives outside the photo section.
        # For Квартира/Будинок it's inside "Опис, фотографії, відеотур" (handled by photos).
        # For Комерційна/Кімната/Ділянка/Паркомісце it's in "Інформація про об'єкт" and
        # must be filled here — _fill_photos_from_dict can't reach it.
        _desc_field = self._schema["label_to_field"].get("опис")
        if _desc_field:
            _desc_section = self._schema["label_to_section"].get("опис", "")
            if _desc_section not in _PHOTO_SECTION_NAMES:
                _desc_text = ((offer_data.get("apartment") or {}).get("description") or "").strip()
                if _desc_text:
                    logger.debug("Опис як звичайне поле (секція '%s')", _desc_section)
                    self._fill_field_from_dict(root, _desc_section, "Опис", _desc_text, "multiline_text")

        # Map error handling (only if address was filled)
        if state["address_filled"] and self._map_error_visible():
            self._handle_map_error(root, offer_data.get("address", {}))

        # If commission field is not in data, set it to "Немає" to avoid
        # the site's default "Є" which requires filling child fields
        _COMMISSION_LABEL = "Комісія з покупця/орендатора"
        if _COMMISSION_LABEL not in offer_data:
            commission_field = self._schema["label_to_field"].get(_COMMISSION_LABEL.lower().strip())
            if commission_field:
                self._set_commission_radio(root, _COMMISSION_LABEL, desired="Немає")

        # Required validation — log issues but continue so save is still attempted.
        # A RequiredFieldError here often means a field like "Вулиця" was typed but
        # not confirmed via dropdown; the server-side save report will capture it too.
        try:
            self._assert_required_filled(root)
        except Exception as e:
            logger.warning("Перевірка обов'язкових полів: %s (спроба зберегти все одно)", e)

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
        # "Котеджне містечко" — autocomplete (dropdown shows "КМ <назва>" items),
        # but the schema records it as widget="text". Force autocomplete handling.
        if key.lower().strip() == "котеджне містечко":
            sec = self._section(root, section)
            desired = self._to_text(value)
            self._fill_autocomplete(sec, key, desired)
            return

        # Спеціальний випадок: "Комісія з покупця/орендатора" — радіокнопка (Є/Немає),
        # а не MUI Select. Схема може неправильно визначити widget="select" через сусідній
        # dropdown валюти, тому перехоплюємо до загальної диспетчеризації.
        _COMMISSION_KEY_LOWER = "комісія з покупця/орендатора"
        if key.lower().strip() == _COMMISSION_KEY_LOWER:
            desired_radio = self._to_text(value).strip() or "Немає"
            self._set_commission_radio(root, key, desired_radio)
            return

        if widget == "box_select":
            if isinstance(value, list):
                value = value[0] if value else ""
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
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
            return

        if widget == "checkbox":
            self._set_checkbox_by_label_if_present(root, section, key, bool(value))
            return

        if widget == "radio":
            if isinstance(value, bool):
                desired = "Так" if value else "Ні"
            else:
                desired = self._to_text(value)
            # Normalize CRM-specific values (e.g. "під чистову" → "Без ремонту")
            desired = _SELECT_VALUE_MAP.get(desired.lower().strip(), desired)
            self._fill_select_or_text(root, section, key, desired)
            return

        if widget == "select":
            if isinstance(value, bool):
                value = "Є" if value else "Немає"
            elif isinstance(value, list):
                if len(value) > 1:
                    # Multi-value: open listbox once and select all items at once
                    items = [
                        _SELECT_VALUE_MAP.get(self._to_text(v).lower().strip(), self._to_text(v))
                        for v in value
                    ]
                    self._open_checklist_and_check(root, section, key, items)
                    return
                value = value[0] if value else ""
            # Normalize known CRM-specific values to Rieltor equivalents
            value_str = self._to_text(value)
            value_str = _SELECT_VALUE_MAP.get(value_str.lower().strip(), value_str)
            self._fill_select_or_text(root, section, key, value_str)
            return

        if widget in ("text", "multiline_text", "datetime"):
            self._fill_by_label(root, section, key, self._to_text(value))
            return

        if widget == "file":
            self._upload_file_in_section(root, section, key, value)
            return

        if widget == "checklist":
            items = self._checklist_items(value)
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
        cadastral = _get("Кадастровий номер")

        if not house:
            logger.warning(
                "Адреса: номер будинку відсутній або порожній — "
                "поле 'Будинок' не буде заповнено, можлива помилка валідації"
            )

        # Normalize street prefix (вул., просп., бульв. тощо)
        if street:
            street = _strip_street_prefix(str(street))

        # Normalize house: strip leading "Будинок " so only number remains ("Будинок 6" → "6")
        if house:
            h = str(house).strip()
            if h.lower().startswith("будинок "):
                h = h[len("будинок "):].strip()
            house = h

        if condo:
            cc = str(condo).strip()
            # "Так"/"Yes" означає лише прапорець новобудови, але не вказує назву ЖК —
            # на сайті поле "Новобудова" є autocomplete з конкретними ЖК, тому
            # значення без назви трактуємо як порожнє.
            if cc.lower() in ("так", "yes", "true", "1"):
                condo = None
            else:
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
            self._fill_autocomplete(sec, "Місто", city)

        if condo:
            # 2a) CONDO COMPLEX → triggers autofill of district/street/house
            self._fill_autocomplete(sec, "Новобудова", condo)
            try:
                self.page.wait_for_timeout(3500)
            except Exception:
                time.sleep(3)

            # Early map error check after ЖК autofill
            if house and self._map_error_visible():
                self._force_reselect_house_number(sec, house, house_label="Будинок")

            # Fill any fields not auto-filled by ЖК selection
            if region:
                self._fill_autocomplete(sec, "Область", region)

            district_ctrl = self._find_control_by_label(sec, "Район")
            if district_ctrl and not self._control_has_value(district_ctrl):
                if district:
                    self._fill_autocomplete(sec, "Район", district)

            street_ctrl = self._find_control_by_label(sec, "Вулиця")
            if street_ctrl and not self._control_has_value(street_ctrl):
                if street:
                    self._fill_autocomplete(sec, "Вулиця", street)

            house_ctrl = self._find_control_by_label(sec, "Будинок")
            if house_ctrl and not self._control_has_value(house_ctrl):
                if house:
                    self._fill_autocomplete(sec, "Будинок", house)

            if house and self._map_error_visible():
                self._force_reselect_house_number(sec, house, house_label="Будинок")
        else:
            # 2b) No ЖК: fill street → house → wait → check district auto-fill
            if region:
                self._fill_autocomplete(sec, "Область", region)
            if street:
                self._fill_autocomplete(sec, "Вулиця", street)
            if house:
                self._fill_autocomplete(sec, "Будинок", house)

            # Wait for district to auto-fill from geo-lookup
            try:
                self.page.wait_for_timeout(1500)
            except Exception:
                time.sleep(1.5)

            # Fill district only if not auto-filled
            district_ctrl = self._find_control_by_label(sec, "Район")
            if district_ctrl and not self._control_has_value(district_ctrl):
                if district:
                    self._fill_autocomplete(sec, "Район", district)

            if house and self._map_error_visible():
                self._force_reselect_house_number(sec, house, house_label="Будинок")

        # Check for error page before spending time on slow multi-selects
        self._raise_if_error_page()

        # 6) Multi-select fields
        if subway:
            items = subway if isinstance(subway, (list, tuple)) else [subway]
            self._fill_autocomplete_multi(sec, "Метро", [str(v) for v in items])
        if guide:
            items = guide if isinstance(guide, (list, tuple)) else [guide]
            self._fill_autocomplete_multi(sec, "Орієнтир", [str(v) for v in items])

        # Check for error page that may have appeared during multi-select fills
        self._raise_if_error_page()

        # 7) Cadastral number — plain text input, name="cadastralNumber"
        if cadastral:
            import re as _re
            cadnum_str = str(cadastral).strip()
            _CADNUM_RE = _re.compile(r"^\d{10}:\d{2}:\d{3}:\d{4}$")
            if not _CADNUM_RE.match(cadnum_str):
                logger.warning(
                    "Кадастровий номер '%s' не відповідає формату XXXXXXXXXX:XX:XXX:XXXX — пропуск",
                    cadnum_str,
                )
                cadnum_str = None
        if cadastral and cadnum_str:
            try:
                _cad_selectors = [
                    "css=input[name='cadastralNumber']",
                    "css=input[id*='cadastral' i]",
                    "css=input[placeholder*='кадастр' i]",
                    "css=input[aria-label*='кадастр' i]",
                ]
                inp = None
                for _sel in _cad_selectors:
                    _candidate = sec.locator(_sel).first
                    if _candidate.count():
                        inp = _candidate
                        break
                if inp is None:
                    for _sel in _cad_selectors:
                        _candidate = root.locator(_sel).first
                        if _candidate.count():
                            inp = _candidate
                            logger.debug("Кадастровий номер знайдено в root (не в sec): %s", _sel)
                            break
                if inp is not None:
                    inp.click()
                    inp.fill(cadnum_str)
                    logger.info("Кадастровий номер заповнено: %s", cadnum_str)
                else:
                    logger.warning("Поле 'cadastralNumber' не знайдено в адресній секції")
            except Exception:
                logger.exception("Помилка заповнення кадастрового номера '%s'", cadnum_str)

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

    def _set_commission_radio(self, root: Locator, label: str, desired: str = "Немає") -> None:
        """Встановлює радіокнопку комісії у вказане значення ('Є' або 'Немає').

        Структура DOM: текст label знаходиться у <p>, що є сусідом
        MuiFormControl-root, обидва загорнуті у MuiBox-root:
            MuiBox-root
              ├── <p>Комісія з покупця/орендатора</p>
              └── MuiBox-root
                    └── MuiFormControl-root
                          └── radiogroup
        """
        lit = self._xpath_literal(label)
        # Search "Основні параметри" (Комерційна) then "Цінові параметри" (Квартира)
        wrapper = None
        for section_name in ("Основні параметри", "Цінові параметри"):
            try:
                sec = self._section(root, section_name)
            except Exception:
                continue
            candidate = sec.locator(
                f"xpath=.//div[contains(@class,'MuiBox-root')]"
                f"[.//p[contains(normalize-space(.), {lit})]]"
                f"[.//div[@role='radiogroup']]"
            ).first
            if candidate.count() > 0:
                wrapper = candidate
                break

        if wrapper is None or wrapper.count() == 0:
            logger.warning("Обгортку радіокнопки комісії не знайдено для '%s'", label)
            return

        opt_lit = self._xpath_literal(desired)
        option_btn = wrapper.locator(f"xpath=.//label[contains(normalize-space(.), {opt_lit})]").first
        if option_btn.count():
            option_btn.click()
            logger.info("Встановлено '%s' у '%s'", label, desired)
        else:
            logger.warning("Опція '%s' не знайдена у радіокнопці комісії для '%s'", desired, label)

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

    def _checklist_items(self, value: Any) -> list[str]:
        """Перетворює значення чекліста на UI-підписи.

        У новому форматі зі схемою значення чеклістів у offer_data вже є
        українськими UI-підписами (з опцій полів схеми).
        """
        if isinstance(value, (list, tuple, set)):
            return [str(v) for v in value if str(v).strip()]
        return []

    # ── Save / Publish ──

    def _attempt_error_recovery(self, root, errors: list[dict]) -> bool:
        """Спроба виправити відомі помилки валідації перед повторним збереженням.

        Повертає True, якщо хоча б одну помилку вдалось виправити (варто повторити save).
        Наслідує той самий паттерн, що й відновлення «wrong city» у _submit_and_get_report.
        """
        fixed_any = False
        offer_data = self._last_offer_data or {}

        for err in errors:
            field = (err.get("field") or "").replace("*", "").strip()
            msg = (err.get("message") or "").lower()
            sec = err.get("section", "")

            # --- Відновлення 1: Поверх > Поверховість ---
            if "не може бути більше" in msg and "поверховість" in msg:
                try:
                    sec_obj = self._section(root, "Інформація про об'єкт")
                    poverkh_lbl = self._expected_label("Поверх") or "Поверх"
                    floor_ctrl = self._find_control_by_label(sec_obj, poverkh_lbl)
                    floor_val = (self._filled_value_text(floor_ctrl) or "").strip() if floor_ctrl else ""
                    if floor_val and floor_val.isdigit() and int(floor_val) > 0:
                        logger.warning(
                            "Відновлення: Поверх=%s > Поверховість → встановлюємо Поверховість=%s",
                            floor_val,
                            floor_val,
                        )
                        # Force-fill: clear field first so _fill_by_label won't skip
                        try:
                            sec_obj2 = self._section(root, "Інформація про об'єкт")
                            pov_lbl = self._expected_label("Поверховість") or "Поверховість"
                            pov_ctrl = self._find_control_by_label(sec_obj2, pov_lbl)
                            if pov_ctrl:
                                inp_pov = pov_ctrl.locator("css=input:not([aria-hidden='true'])").first
                                if inp_pov.count():
                                    inp_pov.click()
                                    inp_pov.fill(floor_val)
                        except Exception:
                            self._fill_by_label(root, "Інформація про об'єкт", "Поверховість", floor_val)
                        fixed_any = True
                except Exception:
                    logger.warning("Не вдалось відновити Поверховість", exc_info=True)

            # --- Відновлення 2: Ціна / Валюта ---
            # Окремий блок, бо значення з CRM може мати пробіли ("182 000"),
            # які сайт відкидає — нормалізуємо перед повторним заповненням.
            elif "ціна" in field.lower():
                price_raw = offer_data.get("Ціна") or ""
                if price_raw:
                    # Видалити пробіли та нерозривні пробіли; замінити кому на крапку
                    price_norm = str(price_raw).replace(" ", "").replace("\u00a0", "").replace(",", ".")
                    try:
                        sec_price = self._section(root, "Цінові параметри")
                        price_ctrl = self._find_control_by_label(sec_price, "Ціна")
                        if price_ctrl:
                            inp = price_ctrl.locator("css=input:not([aria-hidden='true'])").first
                            if inp.count():
                                inp.click()
                                inp.fill(price_norm)
                                self._mark_touched(inp)
                                fixed_any = True
                                logger.warning(
                                    "Відновлення: Ціна='%s' (нормалізовано з '%s')", price_norm, price_raw
                                )
                        currency_raw = offer_data.get("Валюта") or ""
                        if currency_raw:
                            self._fill_field_from_dict(
                                root, "Цінові параметри", "Валюта", currency_raw, "select"
                            )
                    except Exception:
                        logger.warning("Не вдалось відновити Ціна/Валюта", exc_info=True)

            # --- Відновлення 3: Обов'язкове поле порожнє ---
            elif "необхідно заповнити" in msg or "необхідно вибрати" in msg:
                # Відновлюємо лише обов'язкові поля (мають '*' у підписі на сайті).
                # Необов'язкові поля (наприклад, "Тип будинку") пропускаємо.
                if "*" not in (err.get("field") or ""):
                    continue
                field_lower = field.lower().strip()

                # Спеціальний випадок: Вулиця — повторне заповнення + Enter
                if "вулиця" in field_lower:
                    addr = offer_data.get("address") or {}
                    street_raw = addr.get("Вулиця") or addr.get("вулиця")
                    if street_raw:
                        street = _strip_street_prefix(str(street_raw))
                        try:
                            sec_addr = self._section(root, "Адреса об'єкта")
                            self._fill_autocomplete(sec_addr, "Вулиця", street, force=True)
                            # Press Enter to confirm typed value
                            inp_v = sec_addr.locator(
                                "xpath=.//label[contains(normalize-space(translate(., '*\u2009', '')), 'Вулиця')]"
                                "/ancestor::div[contains(@class,'MuiFormControl-root')][1]"
                                "//input[not(@aria-hidden='true')]"
                            ).first
                            if inp_v.count():
                                try:
                                    inp_v.press("Enter")
                                except Exception:
                                    pass
                            fixed_any = True
                            logger.warning("Відновлення: повторне заповнення Вулиця='%s'", street)
                        except Exception:
                            logger.warning("Не вдалось повторно заповнити Вулиця", exc_info=True)
                    continue

                # Загальний випадок: поле є у схемі та offer_data
                fi = self._schema["label_to_field"].get(field_lower)
                if fi:
                    lbl = fi["label"]
                    val = offer_data.get(lbl)
                    if val and not self._is_empty_value(val):
                        widget = self._schema["label_to_widget"].get(field_lower, "text")
                        try:
                            logger.warning("Відновлення: повторне заповнення '%s'='%s'", lbl, val)
                            self._fill_field_from_dict(root, sec, lbl, val, widget)
                            fixed_any = True
                        except Exception:
                            logger.warning(
                                "Не вдалось повторно заповнити '%s'", lbl, exc_info=True
                            )

        return fixed_any

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

        # Error page can appear instead of redirect (server failure / stale session)
        self._raise_if_error_page()

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

            # "Точка на карті знаходиться в іншому місті" — server rejects the street geocoding.
            # Recovery: clear the Вулиця field and retry save with city-only address.
            wrong_city = any(self.MAP_WRONG_CITY_SUBSTR in e.get("message", "") for e in report)
            if wrong_city:
                logger.warning(
                    "Помилка карти 'іншому місті' — спроба збереження без вулиці"
                )
                try:
                    root_retry = self._new_offer_root()
                    sec_addr = self._section(root_retry, "Адреса об'єкта")
                    # Click the MUI Autocomplete ×-clear button on the Вулиця field
                    street_clear = sec_addr.locator(
                        "xpath=.//label[contains(normalize-space(translate(., '*\u2009', '')), 'Вулиця')]"
                        "/ancestor::div[contains(@class,'MuiFormControl-root')][1]"
                        "//button[@aria-label='Clear' or @title='Clear']"
                    ).first
                    if street_clear.count():
                        street_clear.click()
                    else:
                        # Fallback: directly clear the input
                        ctrl = self._find_control_by_label(sec_addr, "Вулиця")
                        if ctrl:
                            inp = ctrl.locator("css=input:not([aria-hidden='true'])").first
                            if inp.count():
                                inp.click()
                                inp.fill("")
                    try:
                        self.page.wait_for_timeout(800)
                    except Exception:
                        time.sleep(0.8)
                    # Retry save
                    retry_btn = self.page.locator(f"button:has-text('{btn_text}')").first
                    retry_btn.scroll_into_view_if_needed()
                    retry_btn.click()
                    try:
                        self.page.wait_for_url(self.MANAGEMENT_URL_GLOB, timeout=20_000)
                    except Exception:
                        pass
                    if "/offers/management" in (self.page.url or ""):
                        logger.info("Повторне збереження без вулиці — успішно")
                        return []
                    logger.warning("Повторне збереження без вулиці не допомогло")
                except Exception:
                    logger.warning("Не вдалось спробувати повторне збереження", exc_info=True)

            # Generic recovery: attempt to fix remaining errors and retry once
            remaining = [
                e for e in report
                if self.MAP_WRONG_CITY_SUBSTR not in e.get("message", "")
            ]
            if remaining:
                logger.warning(
                    "Спроба відновлення після помилок валідації (%d помилок)", len(remaining)
                )
                try:
                    root_rec = self._new_offer_root()
                    recovered = self._attempt_error_recovery(root_rec, remaining)
                except Exception:
                    recovered = False
                if recovered:
                    try:
                        self.page.wait_for_timeout(800)
                    except Exception:
                        time.sleep(0.8)
                    retry_btn = self.page.locator(f"button:has-text('{btn_text}')").first
                    try:
                        retry_btn.scroll_into_view_if_needed()
                        retry_btn.click()
                    except Exception:
                        pass
                    try:
                        self.page.wait_for_url(self.MANAGEMENT_URL_GLOB, timeout=20_000)
                    except Exception:
                        pass
                    if "/offers/management" in (self.page.url or ""):
                        logger.info("Збереження після відновлення — успішно")
                        return []
                    # Collect final report
                    try:
                        self.page.wait_for_timeout(900)
                    except Exception:
                        pass
                    root_final = self._new_offer_root()
                    report = self.collect_validation_report(root_final)
                    if report:
                        logger.warning("Залишились помилки валідації після відновлення:")
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
