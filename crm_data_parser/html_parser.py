"""HTML-парсер об'єктів нерухомості.

Парсить збережені HTML-сторінки з CRM та вилучає дані у форматі словника,
сумісному з DictOfferFormFiller.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from crm_data_parser.description_analyzer import DescriptionAnalyzer
from schemas import ADDRESS_LABELS, load_offer_schema
from setup_logger import setup_logger

logger = setup_logger(__name__)

# ── CRM → Schema mapping tables ──────────────────────────────────────

# CRM "Тип угоди" → subfolder inside schemas/schema_dump/
DEAL_TYPE_TO_FOLDER = {
    "продаж": "sell",
    "оренда": "lease",
}

# CRM "Категорія" → schema filename (takes priority over Тип)
CRM_CATEGORY_TO_SCHEMA = {
    "комерційна нерухомість": "Комерційна",
}

# CRM "Тип" → schema filename (used when category doesn't resolve)
CRM_TYPE_TO_SCHEMA = {
    # Житлова нерухомість
    "квартира": "Квартира",
    "кімната": "Кімната",
    "будинок": "Будинок",
    "дім": "Будинок",
    # Земля
    "ділянка": "Ділянка",
    "земельна ділянка": "Ділянка",
    # Паркомісце subtypes
    "гараж": "Паркомісце_garage",
    "паркомісце": "Паркомісце_parking",
    "паркінг": "Паркомісце_parking",
    # Комерційна subtypes (fallback if category not matched)
    "офіс": "Комерційна",
    "торговельне": "Комерційна",
    "склад": "Комерційна",
    "виробництво": "Комерційна",
}

# Fields handled by dedicated extraction methods — skip in generic characteristics loop
_SKIP_LABELS = frozenset(
    {
        "категорія",
        "тип",
        "тип угоди",
        "реклама",
        "закритий/відкритий продаж",
        "посилання на відео",
    }
)

# CRM-internal fields that must NOT end up in the offer dict
_INTERNAL_LABELS = frozenset(
    {
        "тип об'єкту (екс, макл, власник)",
        "чи платить комісію",
        "вигрузка на сайт",
        "ключі",
        "дата актуалізації",
        "район (сайт)",
        "додав співробітник",
        "відповідальний",
        "комісія",
        "джерело",
        "доданий",
        "змінений",
        "активність",
    }
)

# CRM infrastructure title → schema "Поруч є" option mapping
_INFRA_TO_NEARBY = {
    "школи": "Школа",
    "дитячі садочки": "Дитсадок",
    "магазини": "Супермаркет",
    "трц": "Супермаркет",
    "відпочинок, розваги": "Розважальні заклади",
    "фітнес-центри": "Розважальні заклади",
}


class HTMLOfferParser:
    """Розпарсити HTML об'єкта нерухомості та вилучити дані для dict_filler.

    Автоматично визначає тип нерухомості та тип угоди з HTML, потім завантажує
    відповідну схему з ``schemas/schema_dump/{sell|lease}/``.

    Example:
        >>> parser = HTMLOfferParser("html/Об'єкт.html")
        >>> offer_data = parser.parse()
        >>> print(offer_data['price'], offer_data['address']['city'])
    """

    def __init__(
        self,
        html_content: str | Path,
        debug: bool = False,
    ):
        """Ініціалізувати HTML-парсер.

        Args:
            html_content: HTML-рядок або шлях до HTML-файлу.
            debug: Увімкнути debug-логування.
        """
        self.debug = debug

        if debug:
            logger.setLevel("DEBUG")

        # Load HTML
        if isinstance(html_content, (str, Path)):
            path = Path(html_content)
            if path.exists() and path.is_file():
                logger.info(f"Завантаження HTML з файлу: {path}")
                with open(path, encoding="utf-8") as f:
                    html_str = f.read()
            else:
                html_str = str(html_content)
        else:
            html_str = str(html_content)

        self.full_soup = BeautifulSoup(html_str, "html.parser")
        logger.debug(
            f"HTML розпарсено, заголовок: \
                {self.full_soup.title.string if self.full_soup.title else 'Без заголовку'}"
        )

        # Scope to .page-content to ignore navbars, footers, summary-tags, etc.
        page_content = self.full_soup.select_one(".page-content")
        self.soup = page_content if page_content else self.full_soup

        # Auto-detect deal type and property type from HTML
        self.deal_type = self._detect_deal_type()
        self.property_type = self._detect_property_type()
        logger.info(f"Визначено deal_type={self.deal_type}, property_type={self.property_type}")

        # Load schema based on detected types (uses centralized loader)
        self._schema_data = load_offer_schema(self.deal_type, self.property_type)
        self.schema = {
            "fields": self._schema_data["fields"],
            "navigation": self._schema_data["navigation"],
        }
        self.label_to_field = self._schema_data["label_to_field"]
        self.required_fields = self._get_required_fields()

        # Initialize description analyzer
        self.analyzer = DescriptionAnalyzer(self.schema["fields"], debug=debug)

        logger.info(
            f"Парсер ініціалізовано: deal_type={self.deal_type}, "
            f"property_type={self.property_type}, "
            f"fields={len(self.schema['fields'])}, required={len(self.required_fields)}"
        )

    # ==================== Auto-detection ====================

    def _read_characteristics_table(self) -> dict[str, str]:
        """Зчитати всі пари мітка→значення з першої таблиці характеристик detail-view.

        Returns:
            Словник з маппінгом lowercase мітка → необроблений текст значення.
        """
        pairs: dict[str, str] = {}
        for table in self.soup.select("table.detail-view"):
            for row in table.select("tr"):
                cells = row.select("th, td")
                if len(cells) >= 2:
                    label = cells[0].get_text(strip=True).lower()
                    value = cells[1].get_text(strip=True)
                    if label and value:
                        pairs.setdefault(label, value)
        return pairs

    def _detect_deal_type(self) -> str:
        """Визначити тип угоди (Продаж / Оренда) з HTML.

        Стратегія:
            1. Шукати рядок "Тип угоди" в таблиці характеристик.
            2. Запасний варіант: парсити заголовок зведення (напр. "Продаж / Квартира / ...").

        Returns:
            "Продаж" або "Оренда".

        Raises:
            ValueError: Якщо тип угоди не вдається визначити.
        """
        chars = self._read_characteristics_table()
        raw = chars.get("тип угоди", "").strip()
        if raw:
            logger.debug(f"Тип угоди визначено з таблиці: {raw}")
            return raw

        # Fallback: summary title
        title_elem = self.soup.select_one(".summary-estate-data h4")
        if title_elem:
            title_text = title_elem.get_text(strip=True).lower()
            if "продаж" in title_text:
                return "Продаж"
            if "оренда" in title_text:
                return "Оренда"

        raise ValueError(
            "Cannot detect deal type (Тип угоди) from HTML. "
            "Expected 'Продаж' or 'Оренда' in characteristics table or page title."
        )

    def _detect_property_type(self) -> str:
        """Визначити тип нерухомості та зіставити з іменем файлу схеми.

        Стратегія:
            1. Зчитати "Категорія" з характеристик — якщо відображається через
               CRM_CATEGORY_TO_SCHEMA, використати безпосередньо.
            2. Інакше зчитати "Тип" та знайти в CRM_TYPE_TO_SCHEMA.
            3. Запасний варіант: парсити заголовок зведення на відомі назви типів.

        Returns:
            Ім'я файлу схеми без розширення (напр. "Квартира", "Комерційна").

        Raises:
            ValueError: Якщо тип не вдається визначити.
        """
        chars = self._read_characteristics_table()

        # 1. Try category first
        category = chars.get("категорія", "").lower().strip()
        if category in CRM_CATEGORY_TO_SCHEMA:
            result = CRM_CATEGORY_TO_SCHEMA[category]
            logger.debug(f"Тип нерухомості визначено з категорії '{category}': {result}")
            return result

        # 2. Try "Тип" field
        crm_type = chars.get("тип", "").lower().strip()
        if crm_type in CRM_TYPE_TO_SCHEMA:
            result = CRM_TYPE_TO_SCHEMA[crm_type]
            logger.debug(f"Тип нерухомості визначено з типу '{crm_type}': {result}")
            return result

        # 3. Fallback: summary title
        title_elem = self.soup.select_one(".summary-estate-data h4")
        if title_elem:
            title_lower = title_elem.get_text(strip=True).lower()
            for crm_name, schema_name in CRM_TYPE_TO_SCHEMA.items():
                if crm_name in title_lower:
                    logger.debug(f"Тип нерухомості визначено із заголовку: {schema_name}")
                    return schema_name

        raise ValueError(
            f"Cannot detect property type from HTML. "
            f"Категорія='{chars.get('категорія', '')}', Тип='{chars.get('тип', '')}'. "
            f"Known types: {list(CRM_TYPE_TO_SCHEMA.keys())}"
        )

    # ==================== Schema helpers ====================

    def _get_required_fields(self) -> list[dict]:
        """Вилучити обов'язкові поля зі схеми.

        Returns:
            Список визначень обов'язкових полів.
        """
        required = [f for f in self.schema["fields"] if f.get("required", False)]
        logger.debug(f"Обов'язкові поля: {[f['label'] for f in required]}")
        return required

    def parse(self) -> dict:
        """Розпарсити HTML та повернути словник, сумісний з DictOfferFormFiller.

        Returns:
            Словник з вилученими даними оголошення.

        Raises:
            ValueError: Якщо відсутні обов'язкові поля.
        """
        logger.info("Починаємо парсинг HTML")
        result = {}

        # Detected types (set during __init__)
        result["offer_type"] = self.deal_type
        result["property_type"] = self.property_type

        # Extra fields (article, advertising, photo download link, public link, responsible)
        article = self._extract_article()
        if article is not None:
            result["article"] = article

        public_link = self._extract_public_link()
        if public_link is not None:
            result["public_link"] = public_link

        responsible = self._extract_responsible_person()
        if responsible is not None:
            result["responsible_person"] = responsible

        advertising = self._extract_advertising()
        if advertising is not None:
            result["advertising"] = advertising

        photo_dl = self._extract_photo_download_link()
        if photo_dl is not None:
            result["photo_download_link"] = photo_dl

        # Video tour link
        video_url = self._extract_video_url()
        if video_url:
            if "apartment" not in result:
                result["apartment"] = {}
            result["apartment"]["video_url"] = video_url

        # Extract all data sections
        result.update(self._extract_basic_info())
        result.update(self._extract_characteristics())

        # Infrastructure → "Поруч є"
        nearby = self._extract_infrastructure()
        if nearby:
            result["Поруч є"] = nearby

        # Extract address (nested dict)
        address_data = self._extract_address()
        if address_data:
            result["address"] = address_data

        # Fallback to summary stats if needed
        summary_data = self._extract_summary_stats()
        for key, value in summary_data.items():
            if key not in result and value is not None:
                result[key] = value

        # Extract photos (merge into apartment dict, don't overwrite)
        photos_data = self._extract_photos()
        if "apartment" in photos_data:
            if "apartment" not in result:
                result["apartment"] = {}
            result["apartment"].update(photos_data["apartment"])
        else:
            result.update(photos_data)

        # Extract text descriptions
        description = self._extract_description()
        note = self._extract_estate_note()

        # Description → apartment.description (public "Опис" in photo block)
        if description:
            if "apartment" not in result:
                result["apartment"] = {}
            result["apartment"]["description"] = description

        # Estate note + extra info → personal_notes (private "Особисті нотатки")
        notes_parts: list[str] = []
        if result.get("article"):
            notes_parts.append(f"Артикул: #{result['article']}")
        if result.get("public_link"):
            notes_parts.append(f"CRM: {result['public_link']}")
        if result.get("responsible_person"):
            rp = result["responsible_person"]
            rp_text = f"Відповідальний: {rp['name']}"
            if rp.get("contacts"):
                rp_text += f" ({rp['contacts']})"
            notes_parts.append(rp_text)
        if note:
            notes_parts.append(note)
        if notes_parts:
            result["personal_notes"] = "\n".join(notes_parts)

        # Analyze description for additional fields
        if description or note:
            full_text = "\n\n".join([note or "", description or ""]).strip()
            analyzed_data = self.analyzer.analyze(full_text, result)
            for key, value in analyzed_data.items():
                if key not in result and value is not None:
                    result[key] = value
                    if self.debug:
                        logger.debug(f"Додано з аналізу опису: {key}={value}")

        # Validate and fill defaults
        result = self._fill_missing_with_defaults(result)

        missing = self._validate_required_fields(result)
        if missing:
            missing_with_opts = []
            for label in missing:
                field = self.label_to_field.get(label.lower().strip())
                opts = field.get("options", []) if field else []
                if opts:
                    missing_with_opts.append(f"{label} (options: {opts})")
                else:
                    missing_with_opts.append(label)
            logger.warning(f"Відсутні обов'язкові поля: {missing_with_opts}")

        logger.info(f"Парсинг завершено: вилучено {len(result)} полів верхнього рівня")
        return result

    # ==================== Field Extractors ====================

    def _extract_basic_info(self) -> dict:
        """Вилучити ціну та валюту з розділу зведення.

        Returns:
            Словник з ключами схеми ("Ціна", "Валюта").
        """
        result = {}

        price_elem = self.soup.select_one(".price-per-object")
        if price_elem:
            price_text = price_elem.get_text(strip=True)
            amount, currency = self._parse_price(price_text)
            if amount is not None:
                result["Ціна"] = amount
            if currency:
                result["Валюта"] = currency
            logger.debug(f"Вилучено ціну: {amount} {currency}")

        return result

    def _extract_characteristics(self) -> dict:
        """Вилучити дані з таблиць характеристик.

        Пропускає мітки, що вже обробляються спеціальними методами (див. ``_SKIP_LABELS``)
        та поля адреси (обробляються ``_extract_address``).

        Returns:
            Словник з ключами схеми (напр. "Загальний стан", "Тип будинку").
        """
        result = {}

        tables = self.soup.select("table.detail-view")
        logger.debug(f"Знайдено {len(tables)} таблиць характеристик")

        for table in tables:
            rows = table.select("tr")
            for row in rows:
                cells = row.select("th, td")
                if len(cells) >= 2:
                    label_text = cells[0].get_text(strip=True)
                    value_text = cells[1].get_text(strip=True)

                    if not label_text or not value_text:
                        continue

                    label_lower = label_text.lower().strip()

                    # Skip fields handled by dedicated methods
                    if label_lower in _SKIP_LABELS:
                        continue

                    # Skip CRM-internal fields
                    if label_lower in _INTERNAL_LABELS:
                        continue

                    # Special: "Наявність генератору або інвертору: Так" →
                    # "Працює без світла": "Резервне живлення квартири (акумулятори)"
                    if label_lower == "наявність генератору або інвертору":
                        if value_text.lower() == "так":
                            result["Працює без світла"] = "Резервне живлення квартири (акумулятори)"
                        continue

                    # Look up field in schema by HTML label
                    field_info = self._look_up_field_by_html_label(label_text)

                    if field_info:
                        # Skip address fields (handled separately)
                        if field_info["label"].lower().strip() in ADDRESS_LABELS:
                            continue

                        # Use schema label as key
                        schema_label = field_info["label"]
                        normalized_value = self._normalize_value(field_info, value_text)
                        if normalized_value is not None:
                            result[schema_label] = normalized_value
                            logger.debug(
                                f"Вилучено '{schema_label}'={normalized_value} \
                                    з HTML мітки '{label_text}'"
                            )
                    else:
                        logger.debug(f"Збігу в схемі для мітки не знайдено: '{label_text}'")

        return result

    def _extract_address(self) -> dict:
        """Вилучити дані адреси з розділу адресної таблиці.

        Returns:
            Словник з ключами схеми ("Місто", "Район", "Вулиця" тощо).
        """
        address = {}

        tables = self.soup.select("table.detail-view")

        for table in tables:
            rows = table.select("tr")
            for row in rows:
                cells = row.select("th, td")
                if len(cells) >= 2:
                    label_text = cells[0].get_text(strip=True)
                    value_text = cells[1].get_text(strip=True)

                    if not label_text or not value_text:
                        continue

                    # Check if this is an address field
                    field_info = self._look_up_field_by_html_label(label_text)

                    if field_info and field_info["label"].lower().strip() in ADDRESS_LABELS:
                        schema_label = field_info["label"]
                        value = value_text

                        # Clean up prefixes
                        label_lower = schema_label.lower().strip()
                        if label_lower == "вулиця" and value.startswith("вул."):
                            value = value.replace("вул.", "").strip()
                        elif label_lower == "новобудова" and value.startswith("ЖК "):
                            value = value.replace("ЖК ", "").strip()

                        # Метро and Орієнтир are multi-value
                        if label_lower in ("метро", "орієнтир"):
                            address[schema_label] = [value]
                        else:
                            address[schema_label] = value
                        logger.debug(f"Вилучено address.{schema_label}={value}")

        # Fallback: try to extract house number from "Номер будинку" (CRM label)
        if "Будинок" not in address:
            for table in tables:
                for row in table.select("tr"):
                    cells = row.select("th, td")
                    if len(cells) >= 2:
                        lbl = cells[0].get_text(strip=True).lower()
                        val = cells[1].get_text(strip=True)
                        if "номер будинку" in lbl and val:
                            address["Будинок"] = val
                            logger.debug(f"Вилучено address.Будинок={val} (з 'Номер будинку')")
                            break

        return address if address else {}

    def _extract_summary_stats(self) -> dict:
        """Вилучити дані зі зведених значень властивостей (запасний варіант).

        Returns:
            Словник з ключами схеми ("Число кімнат", "Поверх" тощо).
        """
        result = {}

        property_values = self.soup.select(".summary-property-value")

        if len(property_values) >= 3:
            # First value: rooms
            rooms_text = property_values[0].get_text(strip=True)
            if rooms_text.isdigit():
                rooms_field = self.label_to_field.get("число кімнат")
                if rooms_field:
                    result["Число кімнат"] = self._normalize_rooms(rooms_text, rooms_field.get("options", []))

            # Second value: floor / total floors
            floor_text = property_values[1].get_text(strip=True)
            match = re.match(r"(\d+)\s*/\s*(\d+)", floor_text)
            if match:
                result["Поверх"] = match.group(1)
                result["Поверховість"] = match.group(2)

            # Third value: areas (total / living / kitchen)
            area_text = property_values[2].get_text(strip=True)
            match = re.match(r"([\d.]+)\s*/\s*([\d.]+)\s*/\s*([\d.]+)", area_text)
            if match:
                result["Загальна площа, м²"] = match.group(1)
                result["Житлова площа, м²"] = match.group(2)
                result["Площа кухні, м²"] = match.group(3)

            logger.debug(f"Вилучено зведені характеристики: {result}")

        return result

    def _extract_photos(self) -> dict:
        """Вилучити URL фотографій з галереї.

        Returns:
            Словник зі списком apartment.photos.
        """
        photos = []

        # Find all photo links
        photo_links = self.soup.select(".slider-item.fancybox")
        for link in photo_links:
            href = link.get("href")
            if href:
                photos.append(href)

        logger.debug(f"Вилучено {len(photos)} фотографій")

        if photos:
            return {"apartment": {"photos": photos}}
        return {}

    def _extract_description(self) -> str:
        """Вилучити опис з розділу додаткової інформації.

        Returns:
            Текст опису або порожній рядок.
        """
        # Look for "Додаткова інформація" section
        for elem in self.soup.find_all(["h3", "h4"]):
            if "додаткова інформація" in elem.get_text(strip=True).lower():
                # Get next paragraph or div
                next_elem = elem.find_next("p")
                if next_elem:
                    text = next_elem.get_text(strip=True)
                    logger.debug(f"Вилучено опис: {len(text)} символів")
                    return text

        return ""

    def _extract_estate_note(self) -> str:
        """Вилучити нотатку об'єкта з розділу .estate-note.

        Returns:
            Текст нотатки або порожній рядок.
        """
        note_elem = self.soup.select_one(".estate-note span")
        if note_elem:
            text = note_elem.get_text(strip=True)
            logger.debug(f"Вилучено нотатку об'єкта: {text}")
            return text
        return ""

    def _extract_article(self) -> str | None:
        """Вилучити номер артикула з елемента .article-label.

        Returns:
            Рядок номера артикула (без '#') або None.
        """
        elem = self.soup.select_one(".article-label")
        if elem:
            text = elem.get_text(strip=True).lstrip("#")
            logger.debug(f"Вилучено артикул: {text}")
            return text
        return None

    def _extract_public_link(self) -> str | None:
        """Вилучити публічне посилання з input#public-view.

        Returns:
            Рядок публічного URL або None.
        """
        inp = self.soup.select_one("input#public-view")
        if inp:
            value = inp.get("value", "").strip()
            if value:
                logger.debug(f"Вилучено публічне посилання: {value}")
                return value
        return None

    def _extract_responsible_person(self) -> dict[str, str] | None:
        """Вилучити ім'я та посилання профілю відповідального з 'Службова інформація'.

        Returns:
            Словник з ключами 'name' та 'profile_url', або None.
        """
        # Find the "Службова інформація" section
        for h3 in self.soup.find_all("h3", class_="item-relation-header"):
            if "службова інформація" in h3.get_text(strip=True).lower():
                section = h3.find_parent("div", class_="item-relation")
                if not section:
                    continue
                # Look for "Відповідальний" row
                for row in section.select("table.detail-view tr"):
                    th = row.select_one("th")
                    td = row.select_one("td")
                    if th and td and "відповідальний" in th.get_text(strip=True).lower():
                        link = td.select_one("a")
                        if link:
                            name = link.get_text(strip=True)
                            href = link.get("href", "")
                            logger.debug(f"Вилучено відповідального: {name} ({href})")
                            return {"name": name, "profile_url": href}
                        else:
                            name = td.get_text(strip=True)
                            if name:
                                logger.debug(f"Вилучено відповідального (без посилання): {name}")
                                return {"name": name, "profile_url": ""}
        return None

    def _extract_advertising(self) -> str | None:
        """Вилучити дозвіл на рекламу з таблиці характеристик.

        Перевіряє як нову мітку CRM "Закритий/відкритий продаж",
        так і застарілу мітку "Реклама".

        Returns:
            Текст реклами (напр. "Відкритий продаж можна рекламувати") або None.
        """
        chars = self._read_characteristics_table()
        value = chars.get("закритий/відкритий продаж") or chars.get("реклама")
        if value:
            logger.debug(f"Вилучено рекламу: {value}")
        return value

    def _extract_photo_download_link(self) -> str | None:
        """Вилучити URL для масового завантаження фотографій.

        Returns:
            Відносний URL вигляду "/estate/17637/download-all-watermark-images" або None.
        """
        link = self.soup.select_one('a[href*="download-all-watermark-images"]')
        if link:
            href = link.get("href")
            logger.debug(f"Вилучено посилання для завантаження фото: {href}")
            return href
        return None

    def _extract_video_url(self) -> str | None:
        """Вилучити URL відеотуру з таблиці характеристик.

        Returns:
            Рядок URL відео або None.
        """
        for table in self.soup.select("table.detail-view"):
            for row in table.select("tr"):
                cells = row.select("th, td")
                if len(cells) >= 2:
                    label = cells[0].get_text(strip=True).lower().strip()
                    if label == "посилання на відео":
                        link = cells[1].select_one("a")
                        if link:
                            href = link.get("href", "").strip()
                            if href:
                                logger.debug(f"Вилучено URL відео: {href}")
                                return href
                        # Fallback: plain text URL
                        text = cells[1].get_text(strip=True)
                        if text.startswith("http"):
                            logger.debug(f"Вилучено URL відео (текст): {text}")
                            return text
        return None

    def _extract_infrastructure(self) -> list[str] | None:
        """Вилучити навколишню інфраструктуру та зіставити з варіантами схеми 'Поруч є'.

        Returns:
            Список відповідних значень варіантів схеми або None.
        """
        infra_div = self.soup.select_one(".infrastructures.clearfix")
        if not infra_div:
            return None

        # Collect unique infrastructure titles
        titles: set = set()
        for item in infra_div.select(".infrastructure"):
            title_elem = item.select_one(".infrastructure-title")
            if title_elem:
                titles.add(title_elem.get_text(strip=True).lower())

        if not titles:
            return None

        # Map to schema options
        matched: list[str] = []
        for title in titles:
            option = _INFRA_TO_NEARBY.get(title)
            if option and option not in matched:
                matched.append(option)

        # Also check schema options directly (e.g. "Парк" in title)
        field_info = self.label_to_field.get("поруч є")
        if field_info:
            schema_options = field_info.get("options", [])
            for option in schema_options:
                if option in matched:
                    continue
                option_lower = option.lower()
                for title in titles:
                    if option_lower in title or title in option_lower:
                        matched.append(option)
                        break

        if matched:
            logger.debug(f"Вилучено інфраструктуру → Поруч є: {matched}")
            return matched
        return None

    # ==================== Value Normalizers ====================

    def _normalize_value(self, field_info: dict, raw_value: str) -> Any:
        """Нормалізувати значення відповідно до типу віджета поля.

        Args:
            field_info: Визначення поля зі схеми.
            raw_value: Необроблений текст значення з HTML.

        Returns:
            Нормалізоване значення, відповідне типу поля.
        """
        widget = field_info.get("widget", "")
        options = field_info.get("options", [])

        # Handle select/radio - match against options
        if widget in ["select", "radio"] and options:
            matched = self._normalize_select_option(raw_value, options)
            return matched

        # Handle checkbox
        if widget == "checkbox":
            return raw_value.lower() in ["так", "yes", "є", "true", "1"]

        # Handle numeric text
        if widget == "text":
            input_type = field_info.get("meta", {}).get("input_type", "")
            if input_type == "number":
                # Try to parse as number
                try:
                    if "." in raw_value:
                        return float(raw_value.replace(",", "."))
                    return int(raw_value.replace(" ", "").replace(",", ""))
                except ValueError:
                    pass

        # Default: return as-is
        return raw_value

    def _normalize_select_option(self, text: str, options: list[str]) -> str:
        """Нечітке зіставлення тексту з варіантами схеми.

        Args:
            text: Текст для зіставлення.
            options: Список допустимих варіантів зі схеми.

        Returns:
            Найкращий відповідний варіант або вихідний текст.
        """
        text_lower = text.lower().strip()

        # Try exact match first
        for option in options:
            if option.lower().strip() == text_lower:
                return option

        # Try partial match
        for option in options:
            if text_lower in option.lower() or option.lower() in text_lower:
                return option

        # Special handling for condition field
        import re as _re

        if any(word in text_lower for word in ["дизайн", "євроремонт", "ремонт"]):
            if _re.search(r"без\s*ремонт", text_lower):
                for option in options:
                    if "без ремонт" in option.lower():
                        return option
            elif "частков" in text_lower:
                for option in options:
                    if "частков" in option.lower():
                        return option
            else:
                for option in options:
                    if "з ремонтом" in option.lower():
                        return option

        # No match found, return original
        return text

    def _normalize_rooms(self, text: str, options: list[str]) -> str:
        """Нормалізувати кількість кімнат до формату схеми.

        Args:
            text: Кількість кімнат як текст або число.
            options: Допустимі варіанти кімнат зі схеми.

        Returns:
            Відформатований рядок кімнат (напр. "1 кімната", "2 кімнати").
        """
        # Parse number
        try:
            num = int(text)
        except ValueError:
            # Try to extract number from text
            match = re.search(r"\d+", text)
            if match:
                num = int(match.group())
            else:
                return text

        # Match against options
        for option in options:
            if str(num) in option and ("кімнат" in option or "кімнати" in option or "кімната" in option):
                return option

        # Fallback: generate standard format
        if num == 1:
            return "1 кімната"
        elif num in [2, 3, 4]:
            return f"{num} кімнати"
        else:
            return f"{num} кімнат"

    def _parse_price(self, text: str) -> tuple[int | None, str | None]:
        """Розпарсити текст ціни на суму та валюту.

        Args:
            text: Текст ціни вигляду "182 000 $" або "50000 грн".

        Returns:
            Кортеж (сума, текст_валюти).
        """
        # Remove spaces and find number
        text_clean = text.replace(" ", "").replace(",", "")

        # Extract number
        match = re.search(r"([\d.]+)", text_clean)
        if not match:
            return None, None

        try:
            amount = int(float(match.group(1)))
        except ValueError:
            return None, None

        # Detect currency
        currency = None
        if "$" in text or "dollar" in text.lower():
            currency = "доларів"
        elif "€" in text or "euro" in text.lower():
            currency = "євро"
        elif "грн" in text or "₴" in text or "uah" in text.lower():
            currency = "гривень"

        return amount, currency

    def _look_up_field_by_html_label(self, html_label: str) -> dict | None:
        """Знайти інформацію поля за міткою HTML-таблиці (не міткою схеми).

        Деякі HTML-таблиці використовують інші мітки, ніж схема.
        Наприклад, HTML має "Ремонт", а схема — "Загальний стан".

        Args:
            html_label: Текст мітки з HTML.

        Returns:
            Словник інформації поля або None.
        """
        html_label_lower = html_label.lower().strip()

        # HTML label → Schema label mapping
        html_to_schema = {
            "ремонт": "загальний стан",
            "площа загальна,м²": "загальна площа, м²",
            "площа житлова,м²": "житлова площа, м²",
            "площа кухні,м²": "площа кухні, м²",
            "кіл. кімнат": "число кімнат",
            "номер будинку": "будинок",
            "жилий комплекс": "новобудова",
            "є оселя": "власник погоджується продати по програмі єоселя",
        }

        # Try direct lookup first
        field = self.label_to_field.get(html_label_lower)
        if field:
            return field

        # Try mapped lookup
        schema_label = html_to_schema.get(html_label_lower)
        if schema_label:
            return self.label_to_field.get(schema_label)

        return None

    # ==================== Validation ====================

    def _validate_required_fields(self, data: dict) -> list[str]:
        """Перевірити наявність усіх обов'язкових полів.

        Args:
            data: Розпарсені дані оголошення (ключі — мітки схеми).

        Returns:
            Список міток відсутніх обов'язкових полів.
        """
        missing = []

        for field in self.required_fields:
            label = field["label"]
            label_lower = label.lower().strip()

            # Check if label key exists in data
            if label in data and data[label]:
                continue

            # Check in nested address (for address fields)
            if label_lower in ADDRESS_LABELS:
                if "address" in data and label in data["address"] and data["address"][label]:
                    continue

            missing.append(label)

        return missing

    def _fill_missing_with_defaults(self, data: dict) -> dict:
        """Заповнити відсутні поля розумними значеннями за замовчуванням там, де можливо.

        Args:
            data: Розпарсені дані оголошення (ключі — мітки схеми).

        Returns:
            Дані із заповненими значеннями за замовчуванням.
        """
        # Ensure address dict exists
        if "address" not in data:
            data["address"] = {}

        # Default currency if price exists but currency doesn't
        if "Ціна" in data and not data.get("Валюта"):
            data["Валюта"] = "доларів"
            logger.debug("Валюта встановлена за замовчуванням: 'доларів'")

        # Fallback: living area ≈ total area - (1.4 × kitchen area)
        # The multiplier accounts for corridors, hallways, bathrooms, etc.
        if not data.get("Житлова площа, м²"):
            total = data.get("Загальна площа, м²")
            kitchen = data.get("Площа кухні, м²")
            if total and kitchen:
                try:
                    living = round(float(total) - 1.4 * float(kitchen), 1)
                    if living > 0:
                        data["Житлова площа, м²"] = str(living)
                        logger.debug(f"Обчислено Житлова площа: {total} - 1.4*{kitchen} = {living}")
                except (ValueError, TypeError):
                    pass

        return data
