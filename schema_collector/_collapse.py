from __future__ import annotations


from setup_logger import setup_logger
logger = setup_logger(__name__)

from playwright.sync_api import Locator

from .helpers import _norm



class _CollapseMixin:
    # ---------------- collapse detection ----------------
    def _toggle_button_for_h6(self, h6: Locator) -> Locator:
        return h6.locator("xpath=ancestor::button[1]").first

    def _collapse_container_for_toggle_button(self, btn: Locator) -> Locator:
        for lvl in range(1, 11):
            wrap = btn.locator(f"xpath=ancestor::div[contains(@class,'MuiBox-root')][{lvl}]").first
            if not wrap.count():
                break
            sib = wrap.locator("xpath=following-sibling::div[contains(@class,'MuiCollapse-container')][1]").first
            if sib.count():
                return sib

        parent = btn.locator("xpath=parent::*").first
        sib = parent.locator("xpath=following-sibling::div[contains(@class,'MuiCollapse-container')][1]").first
        if sib.count():
            return sib

        return btn.locator(
            "xpath=ancestor::div[contains(@class,'MuiBox-root')][1]//div[contains(@class,'MuiCollapse-container')][1]"
        ).first

    def _collapse_container_for_h6(self, h6: Locator) -> Locator:
        btn = self._toggle_button_for_h6(h6)
        if not btn.count():
            return self.page.locator("css=__none__")
        return self._collapse_container_for_toggle_button(btn)

    def _is_collapse_entered(self, collapse: Locator) -> bool:
        try:
            if not collapse.count():
                return False
            cls = collapse.get_attribute("class") or ""
            return "MuiCollapse-entered" in cls
        except Exception:
            return False

    def _wait_collapse_entered(self, collapse: Locator, timeout_ms: int = 9000) -> None:
        if not collapse.count():
            self.page.wait_for_timeout(self.ui_delay_ms)
            return
        try:
            eh = collapse.element_handle()
            if eh:
                self.page.wait_for_function(
                    """
                    (el) => {
                      const cls = el.className || '';
                      if (cls.includes('MuiCollapse-entered')) return true;
                      const r = el.getBoundingClientRect();
                      return !!r && r.height > 1;
                    }
                    """,
                    eh,
                    timeout=timeout_ms,
                )
        except Exception:
            self.page.wait_for_timeout(self.ui_delay_ms)

    def _open_toggle_if_closed(self, h6: Locator) -> bool:
        col = self._collapse_container_for_h6(h6)
        if col.count() and self._is_collapse_entered(col):
            return False

        btn = self._toggle_button_for_h6(h6)
        clicked = False
        if btn.count():
            clicked = self._click_best_effort(btn)
        if not clicked:
            clicked = self._click_best_effort(h6)

        if clicked:
            self.page.wait_for_timeout(self.ui_delay_ms)
            if col.count():
                self._wait_collapse_entered(col)
        return clicked

    # ---------------- expand all blocks (open-only) ----------------
    def expand_all_collapsibles(self, scope: Locator | None = None, *, max_rounds: int = 12) -> None:
        scope = scope or self._root()

        total_opened = 0
        for _ in range(max_rounds):
            changed = 0
            h6s = scope.locator("xpath=.//button[.//h6]//h6")
            for i in range(h6s.count()):
                h6 = h6s.nth(i)
                title = _norm(h6.inner_text() or "")
                if not title:
                    continue
                if title in self._NAV_EXCLUDE_FROM_LIST:
                    continue
                if self._is_action_button_text(title):
                    continue

                col = self._collapse_container_for_h6(h6)
                if not col.count():
                    continue
                if self._is_collapse_entered(col):
                    continue

                if self._open_toggle_if_closed(h6):
                    logger.debug("Expanded: %s", title)
                    changed += 1

            total_opened += changed
            if changed == 0:
                break

        if total_opened:
            logger.info("Expanded %d collapsibles", total_opened)

    def open_all_blocks_sticky(self) -> None:
        logger.debug("Open all blocks sticky")
        root = self._root()
        self._scroll_to_bottom()
        self.page.wait_for_timeout(self.ui_delay_ms + 250)
        self.expand_all_collapsibles(root, max_rounds=18)
        self._scroll_to_top()
        self.page.wait_for_timeout(self.ui_delay_ms + 250)
        self.expand_all_collapsibles(root, max_rounds=18)
        self._scroll_to_top()
        self.page.wait_for_timeout(self.ui_delay_ms + 250)
        self.expand_all_collapsibles(root, max_rounds=18)
