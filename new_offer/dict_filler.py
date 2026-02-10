from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List

from playwright.sync_api import Page, Locator

from setup_logger import setup_logger

from .structure import StructureMixin
from .mappings import MappingMixin
from .autocomplete import AutocompleteMixin
from .fields import FieldsMixin
from .address import AddressMixin
from .photos import PhotosMixin, _LABEL_DESCRIPTION, _LABEL_VIDEO_URL
from .validation import ValidationMixin, FormValidationError
from .misc import deal_text

from schemas import load_offer_schema

logger = setup_logger(__name__)

# Photo block keys (programmatic) → matched to "Блок N з 5: ..." sections in order
_PHOTO_BLOCK_KEY_ORDER = ("apartment", "interior", "layout", "yard", "infrastructure")

# Keys in offer_data that are handled specially (not schema fields)
_SPECIAL_KEYS = frozenset({
    "offer_type", "property_type", "article", "advertising",
    "photo_download_link", "personal_notes", "address",
})


class DictOfferFormFiller(
    StructureMixin,
    MappingMixin,
    AutocompleteMixin,
    FieldsMixin,
    AddressMixin,
    PhotosMixin,
    ValidationMixin,
):
    """Dict-based offer form filler — schema-driven version.

    Fill only the 'Нове оголошення' form on /offers/create using dict data.
    All field metadata comes from JSON schemas in schemas/schema_dump/.
    Assumes Page already exists and user is logged in.

    Keys in offer_data are Ukrainian labels from the schema
    (e.g. "Число кімнат", "Поверх") except for a few special keys
    ("offer_type", "property_type", "address", photo block keys, etc.).
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

        # Build photo block key → section title mapping from navigation
        self._photo_block_sections: Dict[str, str] = {}
        block_sections = [n for n in self._schema["navigation"] if n.startswith("Блок ")]
        for i, section_name in enumerate(block_sections):
            if i < len(_PHOTO_BLOCK_KEY_ORDER):
                self._photo_block_sections[_PHOTO_BLOCK_KEY_ORDER[i]] = section_name

        logger.debug(
            "Schema loaded: %d fields, photo blocks: %s",
            len(self._schema["fields"]),
            self._photo_block_sections,
        )

    # ---------- public API ----------
    def open(self) -> None:
        """Open the create offer page."""
        self.page.goto(self.CREATE_URL, wait_until="domcontentloaded")
        logger.info("Opened create-offer page")

    def create_offer_draft(self, offer_data: dict) -> None:
        """Fill form using dict data — schema-driven version.

        Args:
            offer_data: Dict with offer fields. Keys are Ukrainian labels
                from the schema (e.g. "Число кімнат", "Ціна") plus special
                keys ("offer_type", "property_type", "address", photo blocks).
        """
        self.open()
        root = self._new_offer_root()
        logger.info("Start filling offer draft (dict-based, schema-driven)")

        state = {
            'address_filled': False,
            'additional_opened': False,
            'photos_filled': False,
        }

        for key, value in offer_data.items():
            if self._is_empty_value(value):
                continue

            # ── Special: offer_type (box_select) ──
            if key == "offer_type":
                self._click_box_button_in_section(root, "Тип угоди", deal_text(value))
                continue

            # ── Special: property_type (box_select) ──
            if key == "property_type":
                self._click_box_button_in_section(
                    root, "Тип нерухомості", self._to_text(value).lower()
                )
                continue

            # ── Special: address block ──
            if key == "address" and isinstance(value, dict):
                if not state['address_filled']:
                    self._fill_address_from_dict(root, value)
                    state['address_filled'] = True
                continue

            # ── Special: photo blocks ──
            if key in self._photo_block_sections:
                if not state['photos_filled']:
                    self._fill_photos_from_dict(root, offer_data)
                    state['photos_filled'] = True
                continue

            # ── Special: personal_notes ──
            if key == "personal_notes":
                section = "Особисті нотатки"
                self._fill_by_label(root, section, "Особисті нотатки", self._to_text(value))
                continue

            # ── Skip non-schema special keys ──
            if key in _SPECIAL_KEYS:
                continue

            # ── Schema field lookup by label ──
            label_lower = key.lower().strip()
            field_info = self._schema["label_to_field"].get(label_lower)
            if not field_info:
                logger.debug("Key '%s' not in schema, skipping", key)
                continue

            section = self._schema["label_to_section"].get(label_lower, "")
            widget = self._schema["label_to_widget"].get(label_lower, field_info.get("widget", "text"))

            # Open "Додаткові параметри" if needed (lazy, once)
            if not state['additional_opened'] and "Додаткові параметри" in self._schema["navigation"]:
                # Check if we need to open it: field is in "Інформація про об'єкт"
                # and its index suggests it's behind the toggle
                if self._is_additional_param(field_info):
                    self._click_section_toggle(root, "Додаткові параметри")
                    state['additional_opened'] = True

            self._fill_field_from_dict(root, section, key, value, widget)

        # Map error handling (only if address was filled)
        if state['address_filled'] and self._map_error_visible():
            self._handle_map_error(root, offer_data.get('address', {}))

        # Required validation
        self._assert_required_filled(root)
        logger.info("Offer draft filled (dict-based, schema-driven)")

    def _is_additional_param(self, field_info: dict) -> bool:
        """Check if a field belongs to the 'Додаткові параметри' collapsible section.

        These are fields in 'Інформація про об'єкт' that appear after the toggle
        in the form (field_index >= 16 typically: Опалення, Гаряча вода, etc.).
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
        """Fill single field using widget-specific handler."""
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
        """Fill address section from dict with Ukrainian label keys.

        Args:
            address_data: Dict like {"Місто": "Київ", "Новобудова": "ЖК Панорама",
                           "Район": "...", "Будинок": "1", "Метро": [...]}
        """
        sec = self._section(root, "Адреса об'єкта")

        if not address_data:
            logger.warning("Address data is empty, skip")
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
            "Fill address: city=%s, condo=%s, district=%s, street=%s, house=%s",
            city, condo, district, street, house,
        )

        # 0) CITY
        if city:
            next_key = "Новобудова" if condo else "Район"
            self._fill_autocomplete(sec, "Місто", city, next_key=next_key)

        # 1) CONDO COMPLEX (triggers autofill of district/street/house)
        condo_used = False
        if condo:
            self._fill_autocomplete(sec, "Новобудова", condo)
            condo_used = True
            try:
                self.page.wait_for_timeout(1000)
            except Exception:
                time.sleep(0.6)

        # 2) REGION
        if region:
            self._fill_autocomplete(sec, "Область", region)

        # 3) DISTRICT (if not autofilled by condo)
        district_ctrl = self._find_control_by_label(sec, "Район")
        if district_ctrl and not self._control_has_value(district_ctrl):
            if district:
                self._fill_autocomplete(sec, "Район", district, next_key="Вулиця")

        # 4) STREET (if not autofilled)
        street_ctrl = self._find_control_by_label(sec, "Вулиця")
        if street_ctrl and not self._control_has_value(street_ctrl):
            if street:
                self._fill_autocomplete(sec, "Вулиця", street, next_key="Будинок")

        # 5) HOUSE NUMBER (if not autofilled)
        house_ctrl = self._find_control_by_label(sec, "Будинок")
        if house_ctrl and not self._control_has_value(house_ctrl):
            if house:
                self._fill_autocomplete(sec, "Будинок", house)

        # If condo was used — force reselect house to snap map pin
        if condo_used and house:
            self._force_reselect_house_number(sec, house, house_label="Будинок")
            if self._map_error_visible():
                self._force_reselect_house_number(sec, house, house_label="Будинок")

        # 6) Multi-select fields
        if subway:
            items = subway if isinstance(subway, (list, tuple)) else [subway]
            self._fill_autocomplete_multi(sec, "Метро", [str(v) for v in items])
        if guide:
            items = guide if isinstance(guide, (list, tuple)) else [guide]
            self._fill_autocomplete_multi(sec, "Орієнтир", [str(v) for v in items])

    def _handle_map_error(self, root: Locator, address_data: dict) -> None:
        """Handle map pin error — try to snap pin by reselecting house number."""
        logger.warning("Map pin error is visible — trying to snap pin by reselecting house number")

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
            logger.error("Map pin error still visible — refilling address")
            self._fill_address_from_dict(root, address_data)

    # ── Photos ──

    def _fill_photos_from_dict(self, root: Locator, offer_data: dict) -> None:
        """Fill photo blocks that have data.

        Photo block keys ("apartment", "interior", etc.) are mapped to
        section titles from schema navigation ("Блок 1 з 5: Про квартиру", etc.).
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
                    "PhotoBlock '%s': video_url set, but UI only has it in first block — skipping",
                    key,
                )

            if photos:
                self._upload_photos_in_photo_section(sec, list(photos))

    # ── Checklists ──

    def _checklist_items(self, key: str, value: Any) -> List[str]:
        """Convert checklist value to UI labels.

        In the new schema-driven format, checklist values in offer_data are
        already the Ukrainian UI label texts (from schema field options).
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
    ) -> List[dict]:
        """Common submit: save draft or publish."""
        action = "publish" if publish_immediately else "save"
        btn_text = "Опублікувати" if publish_immediately else "Зберегти чернетку"
        logger.info("Click %s", action)

        btn = self.page.locator(f"button:has-text('{btn_text}')").first
        btn.wait_for(state="visible", timeout=15_000)

        if publish_immediately:
            try:
                if btn.is_disabled():
                    logger.warning("Publish button is disabled")
                    root = self._new_offer_root()
                    report = self.collect_validation_report(root)
                    if report and raise_on_errors:
                        raise FormValidationError(report)
                    return report
            except Exception:
                pass

        def _ok(resp) -> bool:
            try:
                req = resp.request
                if req.method not in ("POST", "PUT", "PATCH"):
                    return False
                url = resp.url or ""
                if "/api/offers" not in url:
                    return False
                return 200 <= int(resp.status) < 400
            except Exception:
                return False

        resp = None
        got_resp = False

        try:
            with self.page.expect_response(_ok, timeout=35_000) as rinfo:
                btn.click()
            resp = rinfo.value
            got_resp = True
            try:
                logger.info("Submit response: %s %s -> %s", resp.request.method, resp.url, resp.status)
            except Exception:
                pass

            try:
                data = resp.json()
                if isinstance(data, dict):
                    for k in ("id", "offer_id", "offerId"):
                        if k in data:
                            self.last_saved_offer_id = data[k]
                            break
                if self.last_saved_offer_id is not None:
                    logger.info("Offer id: %s", self.last_saved_offer_id)
            except Exception:
                pass

        except Exception:
            try:
                btn.click(force=True)
            except Exception:
                pass

        if publish_immediately:
            try:
                dlg = self.page.locator("css=[role='dialog']").first
                if dlg.count():
                    confirm = dlg.locator("button:has-text('Опублікувати')").first
                    if confirm.count():
                        try:
                            with self.page.expect_response(_ok, timeout=35_000):
                                confirm.click()
                            got_resp = True
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
            logger.info("Redirected to offers management: %s", self.page.url)
            return []

        if got_resp:
            try:
                self.page.wait_for_timeout(900)
            except Exception:
                pass

        root = self._new_offer_root()
        report = self.collect_validation_report(root)
        if report:
            logger.warning("Validation errors after %s: %s", action, report)
            if raise_on_errors:
                raise FormValidationError(report)
        else:
            logger.warning("%s finished without redirect and without visible validation errors", action)

        return report

    # ── Public API (thin wrappers) ──

    def save(self) -> None:
        self._submit_and_get_report(publish_immediately=False, raise_on_errors=True)

    def save_and_get_report(self, publish_immediately: bool = False) -> List[dict]:
        return self._submit_and_get_report(publish_immediately=publish_immediately, raise_on_errors=False)

    def publish(self) -> None:
        self._submit_and_get_report(publish_immediately=True, raise_on_errors=True)

    def publish_and_get_report(self) -> List[dict]:
        return self._submit_and_get_report(publish_immediately=True, raise_on_errors=False)

    # ── Helpers ──

    def _upload_file_in_section(self, root: Locator, section: str, key: str, value: Any) -> None:
        """Upload file(s) in a section."""
        files: List[str] = []
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
        inp = form.locator("css=input[type='file']").first if form.count() else sec.locator("css=input[type='file']").first

        if inp.count() == 0:
            logger.warning("File input not found for %s/%s", section, key)
            return

        try:
            logger.info("Upload %s/%s: %d file(s)", section, key, len(files))
            inp.set_input_files(files)
            self._mark_touched(inp)
        except Exception:
            logger.exception("Failed uploading files for %s/%s", section, key)
