from __future__ import annotations

from typing import Optional
from setup_logger import setup_logger
logger = setup_logger(__name__)

from playwright.sync_api import Locator

from .helpers import (_norm, _cf)



class _PropertyTypeMixin:
    # ---------------- property type ----------------
    def select_property_type(self, ui_text: str) -> None:
        # New property type => reset cached select options (same labels can have different options).
        self._select_options_cache = {}

        logger.info("Select property type: %s", ui_text)
        root = self._root()
        self.open_all_blocks_sticky()

        sec = root.locator(
            "xpath=.//h6[normalize-space(.)='Тип нерухомості']/ancestor::div[contains(@class,'MuiBox-root')][2]"
        ).first
        sec.wait_for(state="visible", timeout=15_000)

        target = _cf(ui_text)
        cards = sec.locator(
            "xpath=.//div[contains(@class,'MuiBox-root')][.//img[@alt] and .//span and not(.//div[contains(@class,'MuiBox-root')][.//img[@alt] and .//span])]"
        )

        chosen: Optional[Locator] = None
        for i in range(cards.count()):
            c = cards.nth(i)
            alt = _cf(c.locator("css=img[alt]").first.get_attribute("alt") or "")
            spans = _cf(" ".join(_norm(t) for t in c.locator("css=span").all_inner_texts() if _norm(t)))
            if (alt and target in alt) or (spans and target in spans):
                chosen = c
                break
        if not chosen:
            raise RuntimeError(f"Property type card not found: {ui_text}")

        inner = chosen.locator("xpath=.//span[normalize-space()]").first
        if not self._click_best_effort(inner if inner.count() else chosen):
            logger.warning("Failed to click property type card: %s", ui_text)

        self._wait_ready()
        try:
            self.page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        self.page.wait_for_timeout(self.ui_delay_ms + 450)

        self._epoch += 1
        self.open_all_blocks_sticky()
        logger.info("Property type selected: %s (epoch=%s)", ui_text, self._epoch)
