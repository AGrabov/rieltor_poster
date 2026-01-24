# /mnt/data/mappings.py
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from playwright.sync_api import Locator

from models.choice_labels import OFFER_LABELS, ADDITIONAL_PARAMS_LABELS

from setup_logger import setup_logger
logger = setup_logger(__name__)



class MappingMixin:
    """
    Отвечает только за:
        - преобразование значений в текст (_to_text)
        - получение ожидаемого label по ключу (_expected_label)
        - поиск контрола по label внутри секции (_find_control_by_label)

    Больше НИКАКИХ offer_mapping/селекторов — всё строится на schema.py + choice_labels.py.
    """

    @staticmethod
    def _to_text(v: Any) -> str:
        if isinstance(v, Enum):
            return str(v.value)
        return "" if v is None else str(v)

    def _expected_label(self, key: str) -> Optional[str]:
        # 1) общий словарь
        if key in OFFER_LABELS:
            return OFFER_LABELS[key]
        # 2) доп.параметры
        if key in ADDITIONAL_PARAMS_LABELS:
            return ADDITIONAL_PARAMS_LABELS[key]
        # 3) если лейбла нет на сайте/в словаре — вернём None,
        # дальше логика может использовать fallback на key
        return None

    def _find_control_by_label(self, section: Locator, label_text: str) -> Optional[Locator]:
        """
        Надёжный способ: ищем label внутри section, потом поднимаемся до MuiFormControl-root
        и берём типичный контрол:
            - input (кроме radio/checkbox)
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
        normalized = " ".join(raw.split())
        normalized_no_ast = normalized.replace("*", "").strip()
        lit_exact = self._xpath_literal(normalized_no_ast)
        lit_contains = self._xpath_literal(normalized)

        label = None
        candidates = [
            # exact match by label text node
            f"xpath=.//label[normalize-space(translate(text(), '*', ''))={lit_exact}]",
            # exact match by full label text (may include nested spans)
            f"xpath=.//label[normalize-space(translate(., '*', ''))={lit_exact}]",
            # fallback contains
            f"xpath=.//label[contains(normalize-space(translate(., '*', '')), {lit_contains})]",
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
            "input:not([type='radio']):not([type='checkbox']), "
            "textarea, "
            "[role='combobox'], "
            "div.MuiSelect-select[role='button']"
        ).first

        try:
            ctrl.wait_for(state="attached", timeout=1500)
            return ctrl
        except Exception:
            return None


    def _find_formcontrol_by_label(self, sec: Locator, label_text: str) -> Locator | None:
        lit = (label_text or "").strip()
        if not lit:
            return None

        form = sec.locator(
            f"xpath=.//div[contains(@class,'MuiFormControl-root')][.//label[contains(@class,'MuiFormLabel-root') and normalize-space(text())='{lit}']]"
        ).first
        try:
            form.wait_for(state="visible", timeout=2500)
        except Exception as e:
            return None
        if not form.count():
            return None

        logger.debug("Find formcontrol by label: %s", label_text)
        group = form.locator("xpath=.//div[@role='radiogroup']").first
        if group.count():
            return group
        return None
