from __future__ import annotations

from typing import Any, Dict, List, Sequence

from playwright.sync_api import Page, Locator

from setup_logger import setup_logger

from .structure import StructureMixin
from .mappings import MappingMixin
from .autocomplete import AutocompleteMixin
from .fields import FieldsMixin
from .address import AddressMixin
from .photos import PhotosMixin
from .validation import ValidationMixin, FormValidationError
from .misc import deal_text, truthy_fields_as_keys

from models.schema import SECTION_BY_KEY, WIDGET_BY_KEY


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

from models.rieltor_dataclasses import Offer

logger = setup_logger(__name__)


class NewOfferFormFiller(
    StructureMixin,
    MappingMixin,
    AutocompleteMixin,
    FieldsMixin,
    AddressMixin,
    PhotosMixin,
    ValidationMixin,
):
    """Fill only the 'Нове оголошення' form on /offers/create.
    Assumes Page already exists and user is logged in.
    """

    CREATE_URL = "https://my.rieltor.ua/offers/create"
    ROOT_H5_TEXT = "Нове оголошення"

    def __init__(self, page: Page, debug: bool = False) -> None:
        self.page = page
        if debug:
            logger.setLevel("DEBUG")

    # -------- label resolution (REPLACES offer_name_mapping) --------
    def _expected_label(self, key: str) -> str | None:
        # address / main / object / etc
        if key in OFFER_LABELS:
            return OFFER_LABELS[key]
        if key in ADDITIONAL_PARAMS_LABELS:
            return ADDITIONAL_PARAMS_LABELS[key]
        return None

    # ---------- public API ----------
    def open(self) -> None:
        self.page.goto(self.CREATE_URL, wait_until="domcontentloaded")
        logger.info("Opened create-offer page")

    def create_offer_draft(self, offer: Offer) -> None:
        self.open()
        root = self._new_offer_root()
        logger.info("Start filling offer draft")

        additional_opened = False
        address_filled = False
        photos_filled = False

        # Address-related keys from schema (we fill them only via AddressMixin once)
        ADDRESS_KEYS = {
            "address",
            "region",
            "city",
            "district",
            "street",
            "house_number",
            "subway",
            "guide",
            "condo_complex",
        }

        PHOTO_BLOCK_KEYS = {"apartment", "interior", "layout", "yard", "infrastructure"}

        for key, section in SECTION_BY_KEY.items():
            widget = WIDGET_BY_KEY.get(key)

            # ---- 1) special groups handled once ----
            if key in ADDRESS_KEYS:
                if not address_filled and hasattr(offer, "address") and offer.address is not None:
                    self._fill_address(root, offer)
                    address_filled = True
                continue

            if key in PHOTO_BLOCK_KEYS:
                # fill all photo blocks once
                if not photos_filled:
                    try:
                        self._fill_photos(root, offer)
                        photos_filled = True
                    except Exception:
                        logger.exception("Failed to fill photos")
                        pass
                continue

            if key == "additional_params" and widget == "button":
                if not additional_opened:
                    self._click_section_toggle(root, section)
                    additional_opened = True
                continue

            # ---- 2) get value from Offer / nested groups ----
            value = self._get_offer_value(offer, key)
            if value is None:
                continue

            # ignore empty strings / empty lists
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, (list, tuple, set)) and len(value) == 0:
                continue

            # ---- 3) dispatch by widget ----
            if widget == "box_select":
                if key == "offer_type":
                    self._click_box_button_in_section(root, section, deal_text(value))
                else:
                    self._click_box_button_in_section(root, section, self._to_text(value))
                continue

            if widget == "text_autocomplete":
                # address should not be here (skipped above), but keep safe
                sec = self._section(root, section)
                self._fill_autocomplete(sec, key, self._to_text(value), next_key=None)
                continue

            if widget == "autocomplete_multi":
                sec = self._section(root, section)
                self._fill_autocomplete_multi(sec, key, [self._to_text(v) for v in value])
                continue

            if widget == "checkbox":
                # most boolean toggles are checkbox-like, but we keep using existing method
                self._set_checkbox_by_label_if_present(root, section, key, bool(value))
                continue

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
                continue

            if widget == "select":
                self._fill_select_or_text(root, section, key, self._to_text(value))
                continue

            if widget == "text":
                self._fill_by_label(root, section, key, self._to_text(value))
                continue

            if widget == "multiline_text":
                # handled in FieldsMixin via _fill_by_label to textarea (it already supports textarea)
                self._fill_by_label(root, section, key, self._to_text(value))
                continue

            if widget == "datetime":
                # handled as text for now (FieldsMixin can later implement a dedicated handler)
                self._fill_by_label(root, section, key, self._to_text(value))
                continue

            if widget == "file":
                # delegate to PhotosMixin / or FieldsMixin if you have file upload helper there
                # Here: use a generic set_input_files on first file input in section labeled by key.
                self._upload_file_in_section(root, section, key, value)
                continue

            if widget == "checklist":
                items = self._checklist_items_for_key(key, value)
                if not items:
                    continue
                self._open_checklist_and_check(root, section, key, items)
                continue

            # Unknown widget -> try generic fill
            self._fill_by_label(root, section, key, self._to_text(value))

        # Required validation (touched fields)
        self._assert_required_filled(root)
        logger.info("Offer draft filled")

    def save(self) -> None:
        logger.info("Click save")
        self.page.locator("button:has-text('Зберегти чернетку')").first.click()
        self.page.wait_for_load_state("networkidle")

    def save_and_get_report(self, raise_on_errors: bool = False) -> List[dict]:
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

    # -------------------- helpers --------------------
    def _get_offer_value(self, offer: Offer, key: str) -> Any | None:
        """Resolve value by key from Offer and its nested groups."""
        # direct
        if hasattr(offer, key):
            return getattr(offer, key)

        # nested: address.*
        if hasattr(offer, "address") and offer.address is not None and hasattr(offer.address, key):
            return getattr(offer.address, key)

        # nested: additional_params.*
        if hasattr(offer, "additional_params") and offer.additional_params is not None and hasattr(offer.additional_params, key):
            return getattr(offer.additional_params, key)

        # nested: in_apartment.*
        if hasattr(offer, "in_apartment") and offer.in_apartment is not None and hasattr(offer.in_apartment, key):
            return getattr(offer.in_apartment, key)

        return None

    def _selected_keys(self, obj: Any) -> List[str]:
        """Return selected keys from BoolGroup or dataclass of bool fields."""
        if obj is None:
            return []
        if hasattr(obj, "selected_keys") and callable(getattr(obj, "selected_keys")):
            try:
                return list(obj.selected_keys())
            except Exception:
                pass
        # fallback: dataclass bools
        try:
            return truthy_fields_as_keys(obj)
        except Exception:
            return []

    def _checklist_items_for_key(self, key: str, value: Any) -> List[str]:
        """Convert checklist value into UI labels."""
        # value already list[str] of UI labels
        if isinstance(value, (list, tuple, set)):
            return [str(v).strip() for v in value if str(v).strip()]

        selected = self._selected_keys(value)
        if not selected:
            return []

        labels_map: Dict[str, str] = {}

        if key == "without_power_supply":
            labels_map = WITHOUT_POWER_SUPPLY_LABELS
        elif key == "nearby":
            labels_map = NEARBY_LABELS
        elif key == "windows_view":
            labels_map = WINDOW_VIEW_LABELS
        elif key == "additional":
            labels_map = BUILDING_OPTIONS_LABELS
        elif key == "in_apartment":
            labels_map = IN_APARTMENT_LABELS
        elif key == "special_conditions":
            labels_map = DEAL_OPTIONS_LABELS
        elif key == "accessibility":
            labels_map = ACCESSIBILITY_LABELS

        # if map missing -> assume site uses same field names as labels (fallback)
        out: List[str] = []
        for k in selected:
            out.append(labels_map.get(k, k))
        logger.debug("Checklist items for key %s: %s", key, out)
        return out

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
