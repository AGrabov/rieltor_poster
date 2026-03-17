from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from playwright.sync_api import Locator

from setup_logger import setup_logger

logger = setup_logger(__name__)


class MappingMixin:
    """
    Відповідає лише за:
        - перетворення значень у текст (_to_text)
        - отримання очікуваного label за ключем (_expected_label)
        - пошук елемента керування за label всередині секції (_find_control_by_label)

    Ключі offer_data = українські підписи з JSON-схеми (напр. "Число кімнат"),
    тому _expected_label(key) просто повертає key.
    """

    @staticmethod
    def _to_text(v: Any) -> str:
        if isinstance(v, Enum):
            return str(v.value)
        return "" if v is None else str(v)

    def _expected_label(self, key: str) -> str | None:
        """Ключ і є українським підписом у новому форматі зі схемою."""
        return key

    def _find_control_by_label(self, section: Locator, label_text: str) -> Locator | None:
        """
        Надійний спосіб: шукаємо label всередині section, потім піднімаємось до MuiFormControl-root
        і беремо типовий елемент керування:
            - input (крім radio/checkbox)
            - textarea
            - role=combobox
            - div.MuiSelect-select[role=button]  (MUI Select)
        """
        raw = (label_text or "").strip()
        if not raw:
            return None

        # IMPORTANT: avoid ambiguous "contains" matches.
        # Example: "Планування" must NOT match "Планування кімнат".
        # We try exact match (ignoring asterisks) first, then fall back to contains.
        # Thin space U+2009 appears before required asterisks on rieltor.ua — strip it too.
        normalized = " ".join(raw.split())
        normalized_no_ast = normalized.replace("*", "").replace("\u2009", "").strip()
        lit_exact = self._xpath_literal(normalized_no_ast)
        lit_contains = self._xpath_literal(normalized_no_ast)

        label = None
        # translate() strips '*' and thin-space (&#x2009;) from label text before comparing
        _strip = f"translate(., '*\u2009', '')"
        candidates = [
            # exact match by label text node (thin-space + asterisk stripped)
            f"xpath=.//label[normalize-space(translate(text(), '*\u2009', ''))={lit_exact}]",
            # exact match by full label text including nested spans
            f"xpath=.//label[normalize-space({_strip})={lit_exact}]",
            # fallback contains (still safe because lit_contains has asterisks/thin-space removed)
            f"xpath=.//label[contains(normalize-space({_strip}), {lit_contains})]",
        ]
        for sel in candidates:
            loc = section.locator(sel).first
            try:
                loc.wait_for(state="visible", timeout=1200)
                label = loc
                break
            except Exception:
                continue

        if not label:
            return None

        form = label.locator("xpath=ancestor::div[contains(@class,'MuiFormControl-root')][1]").first

        ctrl = form.locator(
            "css="
            "input:not([type='radio']):not([type='checkbox']):not([aria-hidden='true']), "
            "textarea, "
            "div.MuiSelect-select[role='button']"
        ).first

        try:
            ctrl.wait_for(state="attached", timeout=1500)
            return ctrl
        except Exception:
            return None

    def _find_formcontrol_by_label(self, sec: Locator, label_text: str) -> Locator | None:
        lit_raw = (label_text or "").strip().replace("*", "").replace("\u2009", "").strip()
        if not lit_raw:
            return None
        lit = self._xpath_literal(lit_raw)

        form = sec.locator(
            f"xpath=.//div[contains(@class,'MuiFormControl-root')][.//label[contains(@class,'MuiFormLabel-root') and normalize-space(translate(text(), '*\u2009', ''))={lit}]]"
        ).first
        try:
            form.wait_for(state="visible", timeout=2500)
        except Exception:
            return None
        if not form.count():
            return None

        logger.debug("Пошук formcontrol за label: %s", label_text)
        group = form.locator("xpath=.//div[@role='radiogroup']").first
        if group.count():
            return group
        return None
