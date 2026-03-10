"""Завантажувач схем для форм оголошень Rieltor.

Завантажує JSON-схеми з schemas/schema_dump/{sell|lease}/ та будує
словники пошуку, що використовуються як html_parser, так і dict_filler.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCHEMAS_DIR = Path(__file__).parent / "schema_dump"

_DEAL_TYPE_FOLDER = {
    "продаж": "sell",
    "оренда": "lease",
}

# Filler-specific widget overrides.
# The JSON schema uses generic types ("text", "select"), but the form filler
# needs more specific types for certain controls.
WIDGET_OVERRIDES_BY_LABEL = {
    # Address autocomplete fields (JSON has "text")
    "місто": "text_autocomplete",
    "район": "text_autocomplete",
    "вулиця": "text_autocomplete",
    "будинок": "text_autocomplete",
    "новобудова": "text_autocomplete",
    "область": "text_autocomplete",
    # Address multi-select autocomplete
    "метро": "autocomplete_multi",
    "орієнтир": "autocomplete_multi",
}

# Labels that belong to the address section (for html_parser grouping)
ADDRESS_LABELS = frozenset({
    "місто", "район", "вулиця", "будинок",
    "новобудова", "метро", "орієнтир", "область",
})


@lru_cache(maxsize=16)
def load_offer_schema(deal_type: str, property_type: str) -> dict:
    """Завантажити JSON схеми та побудувати словники пошуку.

    Args:
        deal_type: "Продаж" або "Оренда"
        property_type: Ім'я файлу схеми без розширення, напр. "Квартира", "Комерційна"

    Returns:
        Словник з ключами:
          - ``fields``: list[dict] — необроблені визначення полів з JSON
          - ``navigation``: list[str] — порядок секцій/вкладок
          - ``label_to_field``: dict[str, dict] — label_lower → словник поля
          - ``label_to_section``: dict[str, str] — label_lower → назва секції
          - ``label_to_widget``: dict[str, str] — label_lower → тип віджета
                                                   (з застосованими перевизначеннями filler)

    Raises:
        FileNotFoundError: Якщо файл схеми не існує.
        ValueError: Якщо deal_type невідомий.
    """
    folder = _DEAL_TYPE_FOLDER.get(deal_type.lower())
    if not folder:
        raise ValueError(
            f"Unknown deal type '{deal_type}'. "
            f"Expected one of: {list(_DEAL_TYPE_FOLDER.keys())}"
        )

    schema_path = _SCHEMAS_DIR / folder / f"{property_type}.json"
    if not schema_path.exists():
        raise FileNotFoundError(
            f"Schema file not found: {schema_path}\n"
            f"deal_type={deal_type}, property_type={property_type}"
        )

    with open(schema_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    fields: List[dict] = raw.get("fields", [])
    navigation: List[str] = raw.get("navigation", [])

    label_to_field: Dict[str, dict] = {}
    label_to_section: Dict[str, str] = {}
    label_to_widget: Dict[str, str] = {}

    for field in fields:
        label = field.get("label", "")
        label_lower = label.lower().strip()
        if not label_lower:
            continue

        # Keep the first occurrence (if duplicates, first has priority)
        if label_lower not in label_to_field:
            label_to_field[label_lower] = field
            label_to_section[label_lower] = field.get("section", "")
            label_to_widget[label_lower] = WIDGET_OVERRIDES_BY_LABEL.get(
                label_lower, field.get("widget", "text")
            )

    return {
        "fields": fields,
        "navigation": navigation,
        "label_to_field": label_to_field,
        "label_to_section": label_to_section,
        "label_to_widget": label_to_widget,
    }
