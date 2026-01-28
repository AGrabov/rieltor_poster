from __future__ import annotations

from setup_logger import setup_logger
logger = setup_logger(__name__)

from .helpers import _cf, _norm


class _ParkingTypeMixin:
    """Mixin for selecting parking subtype (Гараж/Паркомісце)."""

    PARKING_TYPE_GARAGE = "Гараж"
    PARKING_TYPE_PARKING = "Паркомісце"

    def select_parking_type(self, parking_type: str) -> None:
        """
        Select parking subtype: 'Гараж' or 'Паркомісце'.

        Args:
            parking_type: Either 'Гараж'/'garage' or 'Паркомісце'/'parking'
        """
        # Normalize parking type name
        parking_type_map = {
            "garage": self.PARKING_TYPE_GARAGE,
            "гараж": self.PARKING_TYPE_GARAGE,
            "parking": self.PARKING_TYPE_PARKING,
            "паркомісце": self.PARKING_TYPE_PARKING,
        }
        ui_text = parking_type_map.get(parking_type.lower(), parking_type)

        logger.info("Select parking type: %s", ui_text)
        root = self._root()

        # Find the radiogroup for parking type selection
        # It's a radiogroup with options "Гараж" and "Паркомісце"
        rgs = root.locator("xpath=.//*[@role='radiogroup']")

        target_cf = _cf(ui_text)
        found = False

        for i in range(rgs.count()):
            rg = rgs.nth(i)
            labels = rg.locator("xpath=.//label[contains(@class,'MuiFormControlLabel-root')]")

            # Check if this radiogroup has both "Гараж" and "Паркомісце" options
            has_garage = False
            has_parking = False
            target_label = None

            for j in range(labels.count()):
                lab = labels.nth(j)
                try:
                    txt = _norm(lab.locator("css=span.MuiFormControlLabel-label").inner_text() or "")
                except Exception:
                    txt = ""

                txt_cf = _cf(txt)
                if txt_cf == _cf(self.PARKING_TYPE_GARAGE):
                    has_garage = True
                if txt_cf == _cf(self.PARKING_TYPE_PARKING):
                    has_parking = True
                if txt_cf == target_cf:
                    target_label = lab

            if has_garage and has_parking and target_label:
                # This is the parking type radiogroup
                found = True
                logger.debug("Found parking type radiogroup")

                # Click the target option
                try:
                    span = target_label.locator("css=span.MuiFormControlLabel-label").first
                    if span.count():
                        self._click_best_effort(span)
                    else:
                        self._click_best_effort(target_label)
                except Exception as e:
                    logger.warning("Failed to click parking type option: %s", e)

                break

        if not found:
            logger.warning("Parking type radiogroup not found")
            return

        self._wait_ready()
        try:
            self.page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        self.page.wait_for_timeout(self.ui_delay_ms + 450)

        self._epoch += 1
        self.open_all_blocks_sticky()
        logger.info("Parking type selected: %s (epoch=%s)", ui_text, self._epoch)

    def get_current_parking_type(self) -> str | None:
        """Get currently selected parking type."""
        root = self._root()
        rgs = root.locator("xpath=.//*[@role='radiogroup']")

        for i in range(rgs.count()):
            rg = rgs.nth(i)
            labels = rg.locator("xpath=.//label[contains(@class,'MuiFormControlLabel-root')]")

            # Check if this is the parking type radiogroup
            has_garage = False
            has_parking = False

            for j in range(labels.count()):
                lab = labels.nth(j)
                try:
                    txt = _norm(lab.locator("css=span.MuiFormControlLabel-label").inner_text() or "")
                except Exception:
                    txt = ""

                txt_cf = _cf(txt)
                if txt_cf == _cf(self.PARKING_TYPE_GARAGE):
                    has_garage = True
                if txt_cf == _cf(self.PARKING_TYPE_PARKING):
                    has_parking = True

            if has_garage and has_parking:
                # Found the radiogroup, now find selected option
                for j in range(labels.count()):
                    lab = labels.nth(j)
                    try:
                        inp = lab.locator("css=input[type='radio']").first
                        if inp.count() and inp.is_checked():
                            return _norm(lab.locator("css=span.MuiFormControlLabel-label").inner_text() or "")
                    except Exception:
                        continue

        return None
