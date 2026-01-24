SECTION_BY_KEY = {
    # Тип угоди
    "offer_type": "Тип угоди",

    # Тип нерухомості
    "property_type": "Тип нерухомості",

    # Адреса об'єкта
    "address": "Адреса об'єкта",
    "city": "Адреса об'єкта",
    "district": "Адреса об'єкта",
    "street": "Адреса об'єкта",
    "house_number": "Адреса об'єкта",
    "subway": "Адреса об'єкта",
    "guide": "Адреса об'єкта",
    "condo_complex": "Адреса об'єкта",
    "region": "Адреса об'єкта",

    # Основні параметри
    "price": "Основні параметри",
    "currency": "Основні параметри",
    "assignment": "Основні параметри",
    "buyer_commission": "Основні параметри",
    "commission": "Основні параметри",
    "commission_unit": "Основні параметри",
    "commission_share": "Основні параметри",

    # Інформація про об'єкт
    "room_layout": "Інформація про об'єкт",
    "rooms": "Інформація про об'єкт",
    "floor": "Інформація про об'єкт",
    "floors_total": "Інформація про об'єкт",
    "condition": "Інформація про об'єкт",
    "building_type": "Інформація про об'єкт",
    "construction_technology": "Інформація про об'єкт",
    "special_conditions": "Інформація про об'єкт",
    "construction_stage": "Інформація про об'єкт",
    "total_area": "Інформація про об'єкт",
    "living_area": "Інформація про об'єкт",
    "kitchen_area": "Інформація про об'єкт",
    "year_built": "Інформація про об'єкт",
    "home_program": "Інформація про об'єкт",
    "renewal_program": "Інформація про об'єкт",
    "without_power_supply": "Інформація про об'єкт",
    "accessibility": "Інформація про об'єкт",

    # Додаткові параметри (раскрываем секцию один раз)
    "additional_params": "Додаткові параметри",
    "heating": "Інформація про об'єкт",
    "heating_type": "Інформація про об'єкт",
    "hot_water": "Інформація про об'єкт",
    "hot_water_type": "Інформація про об'єкт",
    "gas": "Інформація про об'єкт",
    "internet": "Інформація про об'єкт",
    "internet_type": "Інформація про об'єкт",
    "nearby": "Інформація про об'єкт",
    "apartment_type": "Інформація про об'єкт",
    "ceiling_height": "Інформація про об'єкт",
    "windows_view": "Інформація про об'єкт",
    "apartment_layout": "Інформація про об'єкт",
    "kitchen_stove": "Інформація про об'єкт",
    "bathroom": "Інформація про об'єкт",
    "plumbing": "Інформація про об'єкт",
    "entrance_door": "Інформація про об'єкт",
    "floor_covering": "Інформація про об'єкт",
    "balconies": "Інформація про об'єкт",
    "windows_type": "Інформація про об'єкт",
    "windows_condition": "Інформація про об'єкт",
    "additional": "Інформація про об'єкт",

    # Блок 1 з 5: Про квартиру
    "apartment": "Блок 1 з 5: Про квартиру",

    # В квартирі є
    "in_apartment": "В квартирі є",

    # Блок 2 з 5: Деталі інтер’єру
    "interior": "Блок 2 з 5: Деталі інтер’єру",

    # Блок 3 з 5: Планування
    "layout": "Блок 3 з 5: Планування",

    # Блок 4 з 5: Будинок та двір
    "yard": "Блок 4 з 5: Будинок та двір",

    # Блок 5 з 5: Інфраструктура
    "infrastructure": "Блок 5 з 5: Інфраструктура",

    # Ексклюзивний договір з власником
    "exclusive": "Ексклюзивний договір з власником",
    "exclusive_contract_scan": "Ексклюзивний договір з власником",
    "exclusive_expiration_date": "Ексклюзивний договір з власником",
    "exclusive_verify": "Ексклюзивний договір з власником",

    # Особисті нотатки
    "personal_notes": "Особисті нотатки",
}


WIDGET_BY_KEY = {
    # Тип угоди
    "offer_type": "box_select",

    # Тип нерухомості
    "property_type": "box_select",

    # Адреса об'єкта
    "address": None,
    "city": "text_autocomplete",
    "district": "text_autocomplete",
    "street": "text_autocomplete",
    "house_number": "text_autocomplete",
    "subway": "autocomplete_multi",
    "guide": "autocomplete_multi",
    "condo_complex": "text_autocomplete",
    "region": "text_autocomplete",

    # Основні параметри
    "price": "text",
    "currency": "select",
    "assignment": "checkbox",
    "buyer_commission": "radio",
    "commission": "text",
    "commission_unit": "select",
    "commission_share": "text",

    # Інформація про об'єкт
    "room_layout": "select",
    "rooms": "select",
    "floor": "text",
    "floors_total": "text",
    "condition": "radio",
    "building_type": "select",
    "construction_technology": "select",
    "special_conditions": "checklist",
    "construction_stage": "select",
    "total_area": "text",
    "living_area": "text",
    "kitchen_area": "text",
    "year_built": "text",
    "home_program": "radio",
    "renewal_program": "radio",
    "without_power_supply": "checklist",
    "accessibility": "checklist",
    "additional_params": "button",

    # Додаткові параметри (раскрываем секцию один раз)
    "heating": "radio",
    "heating_type": "select",
    "hot_water": "radio",
    "hot_water_type": "select",
    "gas": "radio",
    "internet": "radio",
    "internet_type": "select",
    "nearby": "checklist",
    "apartment_type": "select",
    "ceiling_height": "text",
    "windows_view": "checklist",
    "apartment_layout": "select",
    "kitchen_stove": "select",
    "bathroom": "radio",
    "plumbing": "radio",
    "entrance_door": "select",
    "floor_covering": "radio",
    "balconies": "select",
    "windows_type": "select",
    "windows_condition": "select",
    "additional": "checklist",

    # Блок 1 з 5: Про квартиру
    "apartment": "button",

    # В квартирі є
    "in_apartment": "checklist",

    # Блок 2 з 5: Деталі інтер’єру
    "interior": "button",

    # Блок 3 з 5: Планування
    "layout": "button",

    # Блок 4 з 5: Будинок та двір
    "yard": "button",

    # Блок 5 з 5: Інфраструктура
    "infrastructure": "button",

    # Ексклюзивний договір з власником
    "exclusive": "radio",
    "exclusive_contract_scan": "file",
    "exclusive_expiration_date": "datetime",
    "exclusive_verify": "radio",

    # Особисті нотатки
    "personal_notes": "multiline_text",
}
