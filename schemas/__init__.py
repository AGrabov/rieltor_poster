"""Schema loader for Rieltor offer forms.

Loads JSON schemas from schemas/schema_dump/{sell|lease}/ and builds
lookup dicts used by both html_parser and dict_filler.
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
    """Load schema JSON and build lookup dicts.

    Args:
        deal_type: "Продаж" or "Оренда"
        property_type: Schema filename stem, e.g. "Квартира", "Комерційна"

    Returns:
        Dict with keys:
          - ``fields``: list[dict] — raw field definitions from JSON
          - ``navigation``: list[str] — section/tab order
          - ``label_to_field``: dict[str, dict] — label_lower → field dict
          - ``label_to_section``: dict[str, str] — label_lower → section name
          - ``label_to_widget``: dict[str, str] — label_lower → widget type
                                                   (with filler overrides applied)

    Raises:
        FileNotFoundError: If the schema file doesn't exist.
        ValueError: If deal_type is unknown.
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
