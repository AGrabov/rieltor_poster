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
from .photos import PhotosMixin
from .validation import ValidationMixin, FormValidationError
from .misc import deal_text

# labels
from models.choice_labels import (
    OFFER_LABELS,
    ADDITIONAL_PARAMS_LABELS,
    WITHOUT_POWER_SUPPLY_LABELS,
    NEARBY_LABELS,
    WINDOW_VIEW_LABELS,
    BUILDING_OPTIONS_LABELS,
    IN_APARTMENT_LABELS,
    DEAL_OPTIONS_LABELS,
    ACCESSIBILITY_LABELS
)

from models.schema import SECTION_BY_KEY, WIDGET_BY_KEY

logger = setup_logger(__name__)


class DictOfferFormFiller(
    StructureMixin,
    MappingMixin,
    AutocompleteMixin,
    FieldsMixin,
    AddressMixin,
    PhotosMixin,
    ValidationMixin,
):
    """Dict-based offer form filler - optimized version.

    Fill only the 'Нове оголошення' form on /offers/create using dict data.
    Significantly faster than dataclass version by only processing populated fields.
    Assumes Page already exists and user is logged in.
    """

    CREATE_URL = "https://my.rieltor.ua/offers/create"
    ROOT_H5_TEXT = "Нове оголошення"
    MANAGEMENT_URL_GLOB = "**/offers/management**"

    # Class-level schema cache (shared across instances)
    _schema_cache: Dict[str, dict] = {}

    # Photo block keys
    PHOTO_BLOCK_KEYS = {"apartment", "interior", "layout", "yard", "infrastructure"}

    # Address keys
    ADDRESS_KEYS = {
        "city", "region", "district", "street", "house_number",
        "subway", "guide", "condo_complex"
    }

    def __init__(self, page: Page, property_type: str = "Квартира", debug: bool = False) -> None:
        """Initialize dict-based filler.

        Args:
            page: Playwright Page object
            property_type: Property type ("Квартира", "Будинок", "Ділянка", etc.)
            debug: Enable debug logging
        """
        self.page = page
        self.property_type = property_type
        self.last_saved_offer_id: str | int | None = None

        if debug:
            logger.setLevel("DEBUG")

    # -------- label resolution (REPLACES offer_name_mapping) --------
    def _expected_label(self, key: str) -> str | None:
        """Get UI label for field key."""
        if key in OFFER_LABELS:
            return OFFER_LABELS[key]
        if key in ADDITIONAL_PARAMS_LABELS:
            return ADDITIONAL_PARAMS_LABELS[key]
        return None

    # ---------- public API ----------
    def open(self) -> None:
        """Open the create offer page."""
        self.page.goto(self.CREATE_URL, wait_until="domcontentloaded")
        logger.info("Opened create-offer page")

    def create_offer_draft(self, offer_data: dict) -> None:
        """Fill form using dict data - OPTIMIZED version.

        Args:
            offer_data: Dict with offer fields. Supports hybrid structure:
                - Direct fields: "price", "rooms", "floor", etc.
                - Nested address: "address": {"city": "Київ", ...}
                - Nested photos: "apartment": {"description": "...", "photos": [...]}
                - BoolGroups as lists: "accessibility": ["ramp", "ground_level_entrance"]
        """
        self.open()
        root = self._new_offer_root()
        logger.info("Start filling offer draft (dict-based)")

        # Track state for special groups
        state = {
            'address_filled': False,
            'additional_opened': False,
            'photos_filled': False
        }

        # OPTIMIZATION: Only process keys in offer_data (not all schema keys)
        for key, value in offer_data.items():
            # Skip empty values early
            if self._is_empty_value(value):
                continue

            # Special handling: address block
            if key == 'address' and isinstance(value, dict):
                if not state['address_filled']:
                    self._fill_address_from_dict(root, value)
                    state['address_filled'] = True
                continue

            # Special handling: photo blocks
            if key in self.PHOTO_BLOCK_KEYS:
                if not state['photos_filled']:
                    self._fill_photos_from_dict(root, offer_data)
                    state['photos_filled'] = True
                continue

            # Get section for this key
            section = SECTION_BY_KEY.get(key)
            widget = WIDGET_BY_KEY.get(key)

            # Handle additional params toggle
            if key == "additional_params" and widget == "button":
                if not state['additional_opened']:
                    self._click_section_toggle(root, section)
                    state['additional_opened'] = True
                continue

            # Skip if key not in schema
            if not section:
                logger.debug(f"Key '{key}' not in schema, skipping")
                continue

            # Open additional params section if needed
            if self._needs_additional_section(key) and not state['additional_opened']:
                self._click_section_toggle(root, "Додаткові параметри")
                state['additional_opened'] = True

            # Fill field using widget-specific handlers
            self._fill_field_from_dict(root, section, key, value, widget)

        # Map error handling (only if address was filled)
        if state['address_filled'] and self._map_error_visible():
            self._handle_map_error_optimized(root, offer_data.get('address', {}))

        # Required validation
        self._assert_required_filled(root)
        logger.info("Offer draft filled (dict-based)")

    def _is_empty_value(self, value: Any) -> bool:
        """Check if value is empty."""
        if value is None:
            return True
        if isinstance(value, str) and not value.strip():
            return True
        if isinstance(value, (list, tuple, set)) and len(value) == 0:
            return True
        if isinstance(value, dict) and len(value) == 0:
            return True
        return False

    def _needs_additional_section(self, key: str) -> bool:
        """Check if key belongs to additional params section."""
        additional_keys = {
            "heating", "heating_type", "hot_water", "hot_water_type",
            "gas", "gas_type", "internet", "internet_type",
            "apartment_type", "bathroom", "ceiling_height",
            "nearby", "windows_view", "additional"
        }
        return key in additional_keys

    def _fill_field_from_dict(
        self,
        root: Locator,
        section: str,
        key: str,
        value: Any,
        widget: str | None
    ) -> None:
        """Fill single field using widget-specific handler."""
        # Dispatch by widget type
        if widget == "box_select":
            if key == "offer_type":
                self._click_box_button_in_section(root, section, deal_text(value))
            else:
                self._click_box_button_in_section(root, section, self._to_text(value).lower())
            return

        if widget == "text_autocomplete":
            sec = self._section(root, section)
            self._fill_autocomplete(sec, key, self._to_text(value), next_key=None)
            return

        if widget == "autocomplete_multi":
            sec = self._section(root, section)
            self._fill_autocomplete_multi(sec, key, [self._to_text(v) for v in value])
            return

        if widget == "checkbox":
            self._set_checkbox_by_label_if_present(root, section, key, bool(value))
            return

        if widget == "radio":
            # site expects "Так/Ні" for bool radios
            if isinstance(value, bool):
                if key in ["renewal_program", "home_program"]:
                    desired = "Так" if value else "Ні"
                else:
                    desired = "Є" if value else "Немає"
            else:
                desired = self._to_text(value)
            self._fill_select_or_text(root, section, key, desired)
            return

        if widget == "select":
            self._fill_select_or_text(root, section, key, self._to_text(value))
            return

        if widget == "text":
            self._fill_by_label(root, section, key, self._to_text(value))
            return

        if widget == "multiline_text":
            self._fill_by_label(root, section, key, self._to_text(value))
            return

        if widget == "datetime":
            self._fill_by_label(root, section, key, self._to_text(value))
            return

        if widget == "file":
            self._upload_file_in_section(root, section, key, value)
            return

        if widget == "checklist":
            items = self._checklist_items_for_key(key, value)
            if not items:
                return
            self._open_checklist_and_check(root, section, key, items)
            return

        # Unknown widget -> try generic fill
        self._fill_by_label(root, section, key, self._to_text(value))

    def _fill_address_from_dict(self, root: Locator, address_data: dict) -> None:
        """Fill address section from dict - only populated fields.

        Args:
            address_data: Dict with address fields like:
                {"city": "Київ", "district": "Печерський", "street": "Хрещатик",
                 "house_number": "1", "subway": ["Майдан"], "guide": ["Біля ЦУМу"]}
        """
        sec = self._section(root, "Адреса об'єкта")

        if not address_data:
            logger.warning("Address data is empty, skip")
            return

        # Normalize street and condo_complex
        if "street" in address_data:
            s = str(address_data["street"]).strip()
            if s.startswith("вул.") or s.startswith("вулиця "):
                s = s.replace("вул.", "").replace("вулиця ", "").strip()
            address_data["street"] = s

        if "condo_complex" in address_data:
            cc = str(address_data["condo_complex"]).strip()
            if cc.startswith("ЖК "):
                cc = cc.replace("ЖК ", "").strip()
            address_data["condo_complex"] = cc

        logger.info(
            "Fill address: city=%s, condo=%s, district=%s, street=%s, house=%s",
            address_data.get("city"),
            address_data.get("condo_complex"),
            address_data.get("district"),
            address_data.get("street"),
            address_data.get("house_number"),
        )

        # Fill in optimal order based on what's present

        # 0) CITY
        if "city" in address_data:
            next_key = "condo_complex" if "condo_complex" in address_data else "district"
            self._fill_autocomplete(sec, "city", address_data["city"], next_key=next_key)

        # 1) CONDO COMPLEX
        condo_used = False
        if "condo_complex" in address_data:
            self._fill_autocomplete(sec, "condo_complex", address_data["condo_complex"])
            condo_used = True
            # Give UI time to render map/error
            try:
                self.page.wait_for_timeout(1000)
            except Exception:
                import time
                time.sleep(0.6)

        # 2) REGION
        if "region" in address_data:
            self._fill_autocomplete(sec, "region", address_data["region"])

        # 3) DISTRICT (if not autofilled)
        district_label = self._expected_label("district") or "district"
        district_ctrl = self._find_control_by_label(sec, district_label)
        if district_ctrl and not self._control_has_value(district_ctrl):
            if "district" in address_data:
                self._fill_autocomplete(sec, "district", address_data["district"], next_key="street")

        # 4) STREET (if not autofilled)
        street_label = self._expected_label("street") or "street"
        street_ctrl = self._find_control_by_label(sec, street_label)
        if street_ctrl and not self._control_has_value(street_ctrl):
            if "street" in address_data:
                self._fill_autocomplete(sec, "street", address_data["street"], next_key="house_number")

        # 5) HOUSE NUMBER (if not autofilled)
        house_label = self._expected_label("house_number") or "house_number"
        house_ctrl = self._find_control_by_label(sec, house_label)
        if house_ctrl and not self._control_has_value(house_ctrl):
            if "house_number" in address_data:
                self._fill_autocomplete(sec, "house_number", address_data["house_number"])

        # KEY: if used ЖК - force reselect house_number (as manual interaction)
        if condo_used and "house_number" in address_data:
            self._force_reselect_house_number(sec, address_data["house_number"])
            # If error still visible - try again
            if self._map_error_visible():
                self._force_reselect_house_number(sec, address_data["house_number"])

        # 6) Multi-select fields
        if "subway" in address_data:
            self._fill_autocomplete_multi(sec, "subway", list(address_data["subway"]))
        if "guide" in address_data:
            self._fill_autocomplete_multi(sec, "guide", list(address_data["guide"]))

    def _handle_map_error_optimized(self, root: Locator, address_data: dict) -> None:
        """Handle map pin error - optimized version."""
        logger.warning("Map pin error is visible — trying to snap pin by reselecting house number")

        if not address_data:
            return

        try:
            sec_addr = self._section(root, "Адреса об'єкта")
            house_number = address_data.get("house_number")
            if house_number:
                self._force_reselect_house_number(sec_addr, house_number)
        except Exception:
            pass

        # If still not ok - refill address completely
        if self._map_error_visible():
            logger.error("Map pin error still visible — refilling address")
            self._fill_address_from_dict(root, address_data)

    def _fill_photos_from_dict(self, root: Locator, offer_data: dict) -> None:
        """Fill only photo blocks that have data.

        Args:
            offer_data: Dict that may contain photo blocks like:
                {"apartment": {"description": "...", "photos": [...]}, ...}
        """
        from models.choice_labels import PHOTO_BLOCK_LABELS

        label_description = PHOTO_BLOCK_LABELS.get("description", "Опис")
        label_video = PHOTO_BLOCK_LABELS.get("video_url", "Посилання на відеотур")

        for key in self.PHOTO_BLOCK_KEYS:
            photo_block = offer_data.get(key)
            if not photo_block or not isinstance(photo_block, dict):
                continue

            desc = str(photo_block.get("description", "")).strip()
            video = str(photo_block.get("video_url", "")).strip()
            photos = photo_block.get("photos", [])

            # Skip if all fields empty
            if not (desc or video or photos):
                continue

            section_title = self._expected_label(key) or key
            sec = self._section(root, section_title)

            self._ensure_photo_block_open(sec)

            if desc:
                self._fill_text_in_photo_section(sec, label_description, desc)

            # video_url exists only in first block "Блок 1 з 5: Про квартиру"
            if key == "apartment" and video:
                self._fill_text_in_photo_section(sec, label_video, video)
            elif video and key != "apartment":
                logger.debug(
                    "PhotoBlock '%s': video_url set, but UI only has it in first block — skipping",
                    key,
                )

            if photos:
                self._upload_photos_in_photo_section(sec, list(photos))

    def _checklist_items_for_key(self, key: str, value: Any) -> List[str]:
        """Convert checklist value to UI labels.

        Handles both:
        - New dict format: list of keys like ['ramp', 'ground_level_entrance']
        - Old dataclass format: BoolGroup with selected_keys() method (backward compat)
        """
        # New dict-based format: already a list
        if isinstance(value, (list, tuple, set)):
            return self._resolve_labels_for_keys(key, list(value))

        # Old dataclass format: BoolGroup with selected_keys()
        if hasattr(value, 'selected_keys'):
            try:
                keys = list(value.selected_keys())
                return self._resolve_labels_for_keys(key, keys)
            except Exception:
                pass

        return []

    def _resolve_labels_for_keys(self, field_key: str, keys: List[str]) -> List[str]:
        """Map internal keys to UI labels using choice_labels."""
        labels_map_dict = {
            'without_power_supply': WITHOUT_POWER_SUPPLY_LABELS,
            'accessibility': ACCESSIBILITY_LABELS,
            'nearby': NEARBY_LABELS,
            'windows_view': WINDOW_VIEW_LABELS,
            'additional': BUILDING_OPTIONS_LABELS,
            'in_apartment': IN_APARTMENT_LABELS,
            'special_conditions': DEAL_OPTIONS_LABELS,
        }

        labels_map = labels_map_dict.get(field_key, {})
        result = [labels_map.get(k, k) for k in keys]
        logger.debug("Checklist items for key %s: %s", field_key, result)
        return result

    # -------------- Save draft / Publish (same as original) --------------
    def _submit_and_get_report(
        self,
        *,
        publish_immediately: bool,
        raise_on_errors: bool = False,
    ) -> List[dict]:
        """Common submit: save draft or publish.

        - save -> click 'Зберегти чернетку'
        - publish -> click 'Опублікувати' (+ optional confirm dialog)

        Success signal: redirect to /offers/management...
        If we stay on form, collect validation report.
        """
        action = "publish" if publish_immediately else "save"
        btn_text = "Опублікувати" if publish_immediately else "Зберегти чернетку"
        logger.info("Click %s", action)

        btn = self.page.locator(f"button:has-text('{btn_text}')").first
        btn.wait_for(state="visible", timeout=15_000)

        # If publish button is disabled -> definitely not ready
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
            """Any successful write to /api/offers (covers both save & publish flows)."""
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

        # 1) click + wait backend response (best signal)
        try:
            with self.page.expect_response(_ok, timeout=35_000) as rinfo:
                btn.click()
            resp = rinfo.value
            got_resp = True
            try:
                logger.info("Submit response: %s %s -> %s", resp.request.method, resp.url, resp.status)
            except Exception:
                pass

            # best-effort parse offer id on save/publish
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

        # 2) optional confirm dialog for publish
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

        # 3) wait redirect to management page (most common after submit)
        try:
            self.page.wait_for_url(self.MANAGEMENT_URL_GLOB, timeout=60_000)
        except Exception:
            pass

        # 4) If redirected -> consider success (no form errors to collect)
        if "/offers/management" in (self.page.url or ""):
            logger.info("Redirected to offers management: %s", self.page.url)
            return []

        # 5) settle + collect form errors (only if we stayed on form)
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

    # ---------------- public API (thin wrappers) ----------------
    def save(self) -> None:
        """Save draft and raise on errors."""
        self._submit_and_get_report(publish_immediately=False, raise_on_errors=True)

    def save_and_get_report(self, publish_immediately: bool = False) -> List[dict]:
        """Save draft and return validation report."""
        return self._submit_and_get_report(publish_immediately=publish_immediately, raise_on_errors=False)

    def publish(self) -> None:
        """Publish offer and raise on errors."""
        self._submit_and_get_report(publish_immediately=True, raise_on_errors=True)

    def publish_and_get_report(self) -> List[dict]:
        """Publish offer and return validation report."""
        return self._submit_and_get_report(publish_immediately=True, raise_on_errors=False)

    # -------------------- helpers --------------------
    def _upload_file_in_section(self, root: Locator, section: str, key: str, value: Any) -> None:
        """Upload file(s) in a section. `value` may be str path or list[str]."""
        files: List[str] = []
        if isinstance(value, str):
            if value.strip():
                files = [value.strip()]
        elif isinstance(value, (list, tuple, set)):
            files = [str(x).strip() for x in value if str(x).strip()]

        if not files:
            return

        sec = self._section(root, section)

        # Prefer finding input[type=file] near label
        label = self._expected_label(key) or key
        if label:
            lit = self._xpath_literal(label)
            form = sec.locator(f"xpath=.//*[contains(normalize-space(.), {lit})]").first
            inp = form.locator("css=input[type='file']").first if form.count() else sec.locator("css=input[type='file']").first
        else:
            inp = sec.locator("css=input[type='file']").first

        if inp.count() == 0:
            logger.warning("File input not found for %s/%s", section, key)
            return

        try:
            logger.info("Upload %s/%s: %d file(s)", section, key, len(files))
            inp.set_input_files(files)
            self._mark_touched(inp)
        except Exception:
            logger.exception("Failed uploading files for %s/%s", section, key)
