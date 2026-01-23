from __future__ import annotations

from playwright.sync_api import Locator

from setup_logger import setup_logger
from models.rieltor_dataclasses import Offer

logger = setup_logger(__name__)


class AddressMixin:
    def _fill_address(self, root: Locator, offer: Offer) -> None:
        sec = self._section(root, "Адреса об'єкта")
        a = offer.address
        if a is None:
            logger.warning("Offer has no address, skip")
            return

        # normalize street + condo name
        try:
            if getattr(a, "street", None):
                s = a.street.strip()
                if s.startswith("вул.") or s.startswith("вулиця "):
                    s = s.replace("вул.", "").replace("вулиця ", "").strip()
                a.street = s

            if getattr(a, "condo_complex", None):
                cc = a.condo_complex.strip()
                if cc.startswith("ЖК "):
                    cc = cc.replace("ЖК ", "").strip()
                a.condo_complex = cc
        except Exception:
            pass

        logger.info(
            "Fill address: city=%s, condo=%s, district=%s, street=%s, house=%s",
            getattr(a, "city", None),
            getattr(a, "condo_complex", None),
            getattr(a, "district", None),
            getattr(a, "street", None),
            getattr(a, "house_number", None),
        )

        # 0) CITY (first)
        if getattr(a, "city", None):
            next_key = "condo_complex" if getattr(a, "condo_complex", None) else "district"
            self._fill_autocomplete(sec, "city", a.city, next_key=next_key)

        # 1) CONDO COMPLEX (try early; may auto-fill the rest)
        if getattr(a, "condo_complex", None):
            self._fill_autocomplete(sec, "condo_complex", a.condo_complex)

        # 2) REGION (optional)
        if getattr(a, "region", None):
            self._fill_autocomplete(sec, "region", a.region)

        # 3) District is required on site
        district_label = self._expected_label("district") or "district"
        district_ctrl = self._find_control_by_label(sec, district_label)
        if district_ctrl and not self._control_has_value(district_ctrl):
            if getattr(a, "district", None):
                self._fill_autocomplete(sec, "district", a.district, next_key="street")
            else:
                logger.warning("District is required on site, but offer.address.district is empty")

        # 4) Street (only if not auto-filled)
        street_label = self._expected_label("street") or "street"
        street_ctrl = self._find_control_by_label(sec, street_label)
        if street_ctrl and not self._control_has_value(street_ctrl):
            if getattr(a, "street", None):
                self._fill_autocomplete(sec, "street", a.street, next_key="house_number")

        # 5) House number (only if not auto-filled)
        house_label = self._expected_label("house_number") or "house_number"
        house_ctrl = self._find_control_by_label(sec, house_label)
        if house_ctrl and not self._control_has_value(house_ctrl):
            if getattr(a, "house_number", None):
                self._fill_autocomplete(sec, "house_number", a.house_number)

        # 6) Multi fields
        if getattr(a, "subway", None):
            self._fill_autocomplete_multi(sec, "subway", list(a.subway))
        if getattr(a, "guide", None):
            self._fill_autocomplete_multi(sec, "guide", list(a.guide))
