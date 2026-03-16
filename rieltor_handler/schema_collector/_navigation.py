from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from playwright.sync_api import Locator

from setup_logger import setup_logger

from .helpers import _cf, _norm

logger = setup_logger(__name__)


class _NavigationMixin:
    # ---------------- navigation items (h6) ----------------
    def list_navigation_items(self) -> list[tuple[str, int]]:
        root = self._root()
        self.open_all_blocks_sticky()

        h6s = root.locator("css=h6")
        seen: dict[str, int] = {}
        out: list[tuple[str, int]] = []

        for i in range(h6s.count()):
            h = h6s.nth(i)
            try:
                if h.locator("xpath=ancestor::*[@role='dialog' or @role='listbox'][1]").count():
                    continue
            except Exception:
                pass

            title = _norm(h.inner_text() or "")
            if not title:
                continue
            if title in self._NAV_EXCLUDE_FROM_LIST:
                continue
            if self._is_action_button_text(title):
                continue

            k = _cf(title)
            occ = seen.get(k, 0)
            seen[k] = occ + 1
            out.append((title, occ))

        compact: list[tuple[str, int]] = []
        prev: str | None = None
        for t, occ in out:
            if prev is not None and _cf(prev) == _cf(t):
                continue
            compact.append((t, occ))
            prev = t

        logger.debug("Елементів навігації: %d", len(compact))
        logger.debug("Список навігації: %s", [t for (t, _) in compact])
        return compact

    def _h6_by_title_occ(self, title: str, occ: int) -> Locator | None:
        root = self._root()
        h6s = root.locator("css=h6", has_text=title)
        if h6s.count() <= occ:
            return None
        return h6s.nth(occ)

    def _scope_for_nav_item(self, title: str, occ: int) -> Locator | None:
        h6 = self._h6_by_title_occ(title, occ)
        if not h6 or not h6.count():
            return None

        logger.debug("Scope для nav: %s (occ=%d)", title, occ)

        try:
            h6.scroll_into_view_if_needed(timeout=1500)
        except Exception:
            pass

        if h6.locator("xpath=ancestor::button[1]").count():
            col = self._collapse_container_for_h6(h6)
            if col.count() and (not self._is_collapse_entered(col)):
                self._open_toggle_if_closed(h6)
                self.page.wait_for_timeout(self.ui_delay_ms)

            if col.count():
                self.expand_all_collapsibles(col, max_rounds=10)
                self.page.wait_for_timeout(self.ui_delay_ms)
                return col

        box = h6.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][2]").first
        if not box.count():
            box = h6.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][1]").first
        if not box.count():
            return None

        self.expand_all_collapsibles(box, max_rounds=10)
        self.page.wait_for_timeout(self.ui_delay_ms)
        return box

    def _find_nav_scope(self, title: str) -> Locator | None:
        items = self.list_navigation_items()
        for t, occ in items:
            if _cf(t) == _cf(title):
                return self._scope_for_nav_item(t, occ)
        return None
