from __future__ import annotations

from setup_logger import setup_logger
logger = setup_logger(__name__)

from .helpers import _cf


class _DealTypeMixin:
    """Mixin for selecting deal type (Продаж/Оренда)."""

    DEAL_TYPE_SELL = "Продаж"
    DEAL_TYPE_LEASE = "Оренда"

    def select_deal_type(self, deal_type: str) -> None:
        """
        Select deal type: 'Продаж' (sell) or 'Оренда' (lease).

        Args:
            deal_type: Either 'Продаж'/'sell' or 'Оренда'/'lease'
        """
        # Normalize deal type name
        deal_type_map = {
            "sell": self.DEAL_TYPE_SELL,
            "продаж": self.DEAL_TYPE_SELL,
            "lease": self.DEAL_TYPE_LEASE,
            "rent": self.DEAL_TYPE_LEASE,
            "оренда": self.DEAL_TYPE_LEASE,
        }
        ui_text = deal_type_map.get(deal_type.lower(), deal_type)

        logger.info("Select deal type: %s", ui_text)
        root = self._root()

        # Find the "Тип угоди" section
        sec = root.locator(
            "xpath=.//h6[normalize-space(.)='Тип угоди']/ancestor::div[contains(@class,'MuiBox-root')][2]"
        ).first
        sec.wait_for(state="visible", timeout=15_000)

        # Find all deal type options (divs with span inside)
        target = _cf(ui_text)
        options = sec.locator("xpath=.//div[contains(@class,'MuiBox-root')]//span[normalize-space()]")

        chosen = None
        for i in range(options.count()):
            opt = options.nth(i)
            text = _cf(opt.inner_text().strip())
            if target in text or text in target:
                chosen = opt
                break

        if not chosen:
            raise RuntimeError(f"Deal type option not found: {ui_text}")

        # Click the option
        if not self._click_best_effort(chosen):
            logger.warning("Failed to click deal type option: %s", ui_text)

        self._wait_ready()
        try:
            self.page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        self.page.wait_for_timeout(self.ui_delay_ms + 450)

        self._epoch += 1
        self.open_all_blocks_sticky()
        logger.info("Deal type selected: %s (epoch=%s)", ui_text, self._epoch)

    def get_current_deal_type(self) -> str | None:
        """Get currently selected deal type."""
        root = self._root()
        try:
            sec = root.locator(
                "xpath=.//h6[normalize-space(.)='Тип угоди']/ancestor::div[contains(@class,'MuiBox-root')][2]"
            ).first
            # Find selected option (has '-selected' class)
            selected = sec.locator("xpath=.//div[contains(@class,'-selected')]//span[normalize-space()]").first
            if selected.count():
                return selected.inner_text().strip()
        except Exception:
            pass
        return None
