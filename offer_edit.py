"""Чисті помічники для ручного редагування offer_data у дашборді.

Без залежності від Streamlit — щоб логіку злиття правок можна було покрити
юніт-тестами окремо від UI.
"""

from __future__ import annotations

import json
from typing import Any

# Поля адреси, які редагуються структурованою формою. Решта ключів адреси
# (Новобудова, Орієнтир, Метро тощо) редагуються лише через сирий JSON.
ADDRESS_FORM_FIELDS = ("Місто", "Вулиця", "Будинок", "Район", "Кадастровий номер")


def merge_offer_edits(raw_json: str, address_edits: dict[str, str]) -> dict[str, Any]:
    """Злити сирий JSON offer_data з полями адреси зі структурованої форми.

    Модель: основа — це ``raw_json`` (джерело істини для всіх полів), а поля
    форми адреси накладаються поверх ``offer_data["address"]`` і завжди
    перемагають для своїх ключів. Порожнє значення поля форми = свідоме
    очищення (записуємо "").

    Args:
        raw_json: Текст offer_data як JSON-об'єкт.
        address_edits: {підпис: значення} лише для полів форми адреси.

    Returns:
        Злитий offer_data (dict).

    Raises:
        ValueError: якщо ``raw_json`` невалідний або не є JSON-об'єктом.
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Невалідний JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("offer_data має бути JSON-об'єктом (dict)")

    address = data.get("address")
    if not isinstance(address, dict):
        address = {}
        data["address"] = address

    for label, value in address_edits.items():
        address[label] = value

    return data
