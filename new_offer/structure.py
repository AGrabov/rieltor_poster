from __future__ import annotations

from playwright.sync_api import Locator


class StructureMixin:
    ROOT_H5_TEXT = "Нове оголошення"

    def _new_offer_root(self) -> Locator:
        h5 = self.page.locator("h5", has_text=self.ROOT_H5_TEXT).first
        h5.wait_for(state="visible")
        # ближайший контейнер блока "Нове оголошення"
        return h5.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][1]").first

    def _section(self, root: Locator, h6_text: str) -> Locator:
        h6 = root.locator("h6", has_text=h6_text).first
        h6.wait_for(state="visible")
        # контейнер секции (заголовок + контент)
        return h6.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][2]").first

    @staticmethod
    def _xpath_literal(s: str) -> str:
        """Safely embed arbitrary string into XPath literal."""
        s = "" if s is None else str(s)
        if "'" not in s:
            return f"'{s}'"
        if '"' not in s:
            return f'"{s}"'
        parts = s.split("'")
        return "concat(" + ", ".join(
            [f"'{p}'" if i == len(parts) - 1 else f"'{p}', \"'\"" for i, p in enumerate(parts)]
        ) + ")"
