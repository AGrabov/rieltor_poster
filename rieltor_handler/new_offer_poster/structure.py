from __future__ import annotations

from playwright.sync_api import Locator


class StructureMixin:
    ROOT_H5_TEXT = "Нове оголошення"

    def _new_offer_root(self) -> Locator:
        """Return container with offer sections on create/edit page."""

        h5 = self.page.locator("h5", has_text=self.ROOT_H5_TEXT).first
        if h5.count():
            try:
                h5.wait_for(state="visible", timeout=10_000)
            except Exception:
                pass
            if h5.count():
                return h5.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][1]").first

        # Fallback: anchor by the first stable section header.
        h6 = self.page.locator("h6", has_text="Тип угоди").first
        if h6.count():
            try:
                h6.wait_for(state="visible", timeout=15_000)
            except Exception:
                pass

            # Find an ancestor that looks like the whole form (contains multiple section headers).
            for i in range(2, 10):
                anc = h6.locator(f"xpath=ancestor::div[contains(@class,'MuiBox-root')][{i}]").first
                if anc.count() == 0:
                    continue
                try:
                    if anc.locator("css=h6").count() >= 4:
                        return anc
                except Exception:
                    continue

            return h6.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][4]").first

        return self.page.locator("css=body")

    def _section(self, root: Locator, h6_text: str) -> Locator:
        h6 = root.locator("h6", has_text=h6_text).first
        h6.wait_for(state="visible")
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
