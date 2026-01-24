from __future__ import annotations

from dataclasses import is_dataclass
from enum import Enum
from typing import Any, List


def deal_text(offer_type: Any) -> str:
    """
    Normalize deal type to UI button text.
    """
    if isinstance(offer_type, Enum):
        v = str(offer_type.value).lower()
    else:
        v = str(offer_type).lower()

    if "прод" in v:
        return "Продаж"
    if "орен" in v or "аренд" in v:
        return "Оренда"
    return v


def truthy_fields_as_keys(dc_obj) -> List[str]:
    """
    Return list of dataclass field names where value is True.
    Used for checkbox groups like without_power_supply.
    """
    if not is_dataclass(dc_obj):
        return []

    out: List[str] = []
    for f in dc_obj.__dataclass_fields__.keys():
        if getattr(dc_obj, f, None) is True:
            out.append(f)
    return out
