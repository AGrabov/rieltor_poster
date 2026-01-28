from __future__ import annotations


from setup_logger import setup_logger
logger = setup_logger(__name__)

from playwright.sync_api import Locator
from .helpers import (_norm, _cf)


class _CoreMixin:
    # ---------------- open / root ----------------
    def open(self) -> None:
        logger.info("Open create page: %s", self.CREATE_URL)
        self.page.goto(self.CREATE_URL, wait_until="domcontentloaded")
        self._wait_ready()
        logger.info("Create page ready: %s", _norm(self.page.title() or ""))

    def _wait_ready(self) -> None:
        logger.debug("Wait ready (h5 'Нове оголошення' visible)")
        self.page.locator(
            "xpath=//h5[normalize-space(.)='Нове оголошення' or .//*[normalize-space(.)='Нове оголошення']]"
        ).first.wait_for(state="visible", timeout=30_000)
        try:
            self.page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass
        logger.debug("Ready: url=%s", self.page.url)

    def _offer_container(self) -> Locator:
        h5 = self.page.locator(
            "xpath=//h5[normalize-space(.)='Нове оголошення' or .//*[normalize-space(.)='Нове оголошення']]"
        ).first
        if h5.count():
            return h5.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][1]").first

        h6 = self.page.locator("h6", has_text="Тип угоди").first
        if h6.count():
            return h6.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][4]").first

        return self.page.locator("css=body")

    def _root(self) -> Locator:
        return self._offer_container()

    # ---------------- helpers ----------------
    def _scroll_to(self, y: int) -> None:
        try:
            self.page.evaluate("(yy)=>window.scrollTo(0, yy)", y)
        except Exception:
            pass

    def _scroll_to_top(self) -> None:
        self._scroll_to(0)

    def _scroll_to_bottom(self) -> None:
        try:
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass

    def _click_best_effort(self, el: Locator, *, timeout: int = 2500) -> bool:
        # do not click while listbox/dialog is open (but allow autocomplete tooltips)
        try:
            # Check for blocking overlays, but exclude autocomplete poppers
            dialogs = self.page.locator("css=[role='dialog']:visible").count()
            listboxes = self.page.locator("css=[role='listbox']:visible:not(#autocomplete-popper *)").count()
            ui_overlays = dialogs + listboxes

            # Skip overlay check if we're clicking inside a listbox (intentional select operation)
            if ui_overlays and listboxes > 0:
                try:
                    # Check if the element is inside a listbox (we're intentionally clicking it)
                    is_inside_listbox = el.locator("xpath=ancestor::*[@role='listbox']").count() > 0
                    if is_inside_listbox:
                        # Don't close the listbox if we're clicking an element inside it
                        ui_overlays = dialogs  # Only count dialogs as blocking
                except Exception:
                    pass

            if ui_overlays:
                logger.debug("%d UI overlay visible (dialogs=%d, listboxes=%d): closing with Escape", ui_overlays, dialogs, listboxes)
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
                # self.page.wait_for_timeout(self.ui_delay_ms)
        except Exception:
            pass

        try:
            el.scroll_into_view_if_needed(timeout=timeout)
        except Exception:
            pass
        try:
            el.click(timeout=timeout)
            return True
        except Exception:
            try:
                el.click(force=True, timeout=timeout)
                return True
            except Exception:
                return False

    def _is_action_button_text(self, t: str) -> bool:
        t = _cf(t)
        return ("зберегти" in t) or ("опублікувати" in t)
