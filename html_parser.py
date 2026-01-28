"""HTML Parser for Real Estate Objects.

Parses saved HTML pages from CRM and extracts data into dict format
compatible with DictOfferFormFiller.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from bs4 import BeautifulSoup, Tag
from .description_analyzer import DescriptionAnalyzer

from setup_logger import setup_logger

logger = setup_logger(__name__)


class HTMLOfferParser:
    """Parse real estate object HTML and extract data for dict_filler.

    Uses dynamically collected schemas from models/schema_dump/ to map
    Ukrainian field labels to programmatic keys.

    Example:
        >>> parser = HTMLOfferParser("html/Об'єкт.html", property_type="Квартира")
        >>> offer_data = parser.parse()
        >>> print(offer_data['price'], offer_data['address']['city'])
    """

    def __init__(
        self,
        html_content: Union[str, Path],
        property_type: str = "Квартира",
        debug: bool = False
    ):
        """Initialize HTML parser.

        Args:
            html_content: HTML string or path to HTML file
            property_type: Property type to determine schema ("Квартира", "Будинок", etc.)
            debug: Enable debug logging
        """
        self.property_type = property_type
        self.debug = debug

        if debug:
            logger.setLevel("DEBUG")

        # Load HTML
        if isinstance(html_content, (str, Path)):
            path = Path(html_content)
            if path.exists() and path.is_file():
                logger.info(f"Loading HTML from file: {path}")
                with open(path, 'r', encoding='utf-8') as f:
                    html_str = f.read()
            else:
                # Assume it's HTML string
                html_str = str(html_content)
        else:
            html_str = str(html_content)

        self.soup = BeautifulSoup(html_str, 'html.parser')
        logger.debug(f"Parsed HTML, title: {self.soup.title.string if self.soup.title else 'No title'}")

        # Load schema
        self.schema = self._load_schema(property_type)
        self.label_to_field = self._create_label_mapping()
        self.required_fields = self._get_required_fields()

        # Initialize description analyzer
        self.analyzer = DescriptionAnalyzer(self.schema['fields'], debug=debug)

        logger.info(f"Initialized parser: property_type={property_type}, fields={len(self.schema['fields'])}, required={len(self.required_fields)}")

    def _load_schema(self, property_type: str) -> dict:
        """Load schema from models/schema_dump/{property_type}.json.

        Args:
            property_type: Property type name (e.g., "Квартира")

        Returns:
            Schema dict with fields and navigation

        Raises:
            FileNotFoundError: If schema file doesn't exist
        """
        schema_path = Path(__file__).parent.parent / "models" / "schema_dump" / f"{property_type}.json"

        if not schema_path.exists():
            raise FileNotFoundError(
                f"Schema file not found: {schema_path}\n"
                f"Available property types: Квартира, Будинок, Кімната, Комерційна, Ділянка, Паркомісце"
            )

        logger.debug(f"Loading schema from: {schema_path}")
        with open(schema_path, 'r', encoding='utf-8') as f:
            schema = json.load(f)

        return schema

    def _create_label_mapping(self) -> Dict[str, dict]:
        """Create reverse mapping from lowercase labels to field info.

        Returns:
            Dict mapping normalized labels to field definitions
        """
        mapping = {}

        for field in self.schema['fields']:
            label = field['label'].lower().strip()
            # Store field info for this label
            if label not in mapping:
                mapping[label] = field
            else:
                # If duplicate, keep the one with more info (more options, or required)
                existing = mapping[label]
                if field.get('required') and not existing.get('required'):
                    mapping[label] = field
                elif len(field.get('options', [])) > len(existing.get('options', [])):
                    mapping[label] = field

        logger.debug(f"Created label mapping: {len(mapping)} unique labels")
        return mapping

    def _get_required_fields(self) -> List[dict]:
        """Extract required fields from schema.

        Returns:
            List of required field definitions
        """
        required = [f for f in self.schema['fields'] if f.get('required', False)]
        logger.debug(f"Required fields: {[f['label'] for f in required]}")
        return required

    def parse(self) -> dict:
        """Parse HTML and return dict compatible with DictOfferFormFiller.

        Returns:
            Dict with extracted offer data

        Raises:
            ValueError: If required fields are missing
        """
        logger.info("Starting HTML parse")
        result = {}

        # Extract all data sections
        result.update(self._extract_basic_info())
        result.update(self._extract_characteristics())

        # Extract address (nested dict)
        address_data = self._extract_address()
        if address_data:
            result["address"] = address_data

        # Fallback to summary stats if needed
        summary_data = self._extract_summary_stats()
        # Only use summary data if main extraction didn't get these fields
        for key, value in summary_data.items():
            if key not in result and value is not None:
                result[key] = value

        # Extract photos
        photos_data = self._extract_photos()
        result.update(photos_data)

        # Extract text descriptions
        description = self._extract_description()
        note = self._extract_estate_note()
        if note or description:
            combined_notes = []
            if note:
                combined_notes.append(note)
            if description:
                combined_notes.append(description)
            result["personal_notes"] = "\n\n".join(combined_notes).strip()

        # Analyze description for additional fields
        if description or note:
            full_text = "\n\n".join([note or "", description or ""]).strip()
            analyzed_data = self.analyzer.analyze(full_text, result)
            # Merge analyzed data (don't override existing fields)
            for key, value in analyzed_data.items():
                if key not in result and value is not None:
                    result[key] = value
                    if self.debug:
                        logger.debug(f"Added from description analysis: {key}={value}")

        # Validate and fill defaults
        result = self._fill_missing_with_defaults(result)

        missing = self._validate_required_fields(result)
        if missing:
            logger.error(f"Missing required fields: {missing}")
            raise ValueError(f"Missing required fields: {', '.join(missing)}")

        logger.info(f"Parse complete: extracted {len(result)} top-level fields")
        return result

    # ==================== Field Extractors ====================

    def _extract_basic_info(self) -> dict:
        """Extract basic info from summary section.

        Returns:
            Dict with offer_type, property_type, price
        """
        result = {}

        # Extract price
        price_elem = self.soup.select_one('.price-per-object')
        if price_elem:
            price_text = price_elem.get_text(strip=True)
            amount, currency = self._parse_price(price_text)
            if amount is not None:
                result["price"] = amount
            if currency:
                result["currency"] = currency
            logger.debug(f"Extracted price: {amount} {currency}")

        # Extract property type from page title or use parameter
        result["property_type"] = self.property_type

        # Extract offer type from title
        title_elem = self.soup.select_one('.summary-estate-data h4')
        if title_elem:
            title_text = title_elem.get_text(strip=True).lower()
            if 'продаж' in title_text or 'продаж' in title_text:
                result["offer_type"] = "Продаж"
            elif 'оренда' in title_text or 'аренда' in title_text:
                result["offer_type"] = "Оренда"
            logger.debug(f"Inferred offer_type from title: {result.get('offer_type')}")

        return result

    def _extract_characteristics(self) -> dict:
        """Extract data from characteristics tables.

        Returns:
            Dict with fields extracted from detail-view tables
        """
        result = {}

        # Find all tables with class "detail-view"
        tables = self.soup.select('table.detail-view')
        logger.debug(f"Found {len(tables)} characteristic tables")

        for table in tables:
            rows = table.select('tr')
            for row in rows:
                cells = row.select('th, td')
                if len(cells) >= 2:
                    label_text = cells[0].get_text(strip=True)
                    value_text = cells[1].get_text(strip=True)

                    if not label_text or not value_text:
                        continue

                    # Special cases for fields not in schema
                    label_lower = label_text.lower()

                    # Extract property_type from "Тип"
                    if label_lower == 'тип':
                        result['property_type'] = value_text
                        logger.debug(f"Extracted property_type={value_text} from HTML")
                        continue

                    # Extract offer_type from "Тип угоди"
                    if 'тип угоди' in label_lower:
                        result['offer_type'] = value_text
                        logger.debug(f"Extracted offer_type={value_text} from HTML")
                        continue

                    # Look up field in schema by label (handles HTML→Schema label mapping)
                    field_info = self._look_up_field_by_html_label(label_text)

                    if field_info:
                        # Infer programmatic key from label
                        key = self._infer_key_from_label(field_info)
                        if key:
                            # Normalize value based on widget type
                            normalized_value = self._normalize_value(field_info, value_text)
                            if normalized_value is not None:
                                result[key] = normalized_value
                                logger.debug(f"Extracted {key}={normalized_value} from label '{label_text}'")
                    else:
                        logger.debug(f"No schema match for label: '{label_text}'")

        return result

    def _extract_address(self) -> dict:
        """Extract address data from address table section.

        Returns:
            Dict with address fields (city, district, street, etc.)
        """
        address = {}

        # Find address tables
        tables = self.soup.select('table.detail-view')

        for table in tables:
            rows = table.select('tr')
            for row in rows:
                cells = row.select('th, td')
                if len(cells) >= 2:
                    label_text = cells[0].get_text(strip=True)
                    value_text = cells[1].get_text(strip=True)

                    if not label_text or not value_text:
                        continue

                    # Check if this is an address field using HTML→Schema label mapping
                    field_info = self._look_up_field_by_html_label(label_text)

                    if field_info and field_info.get('section', '').lower() == 'адреса об\'єкта':
                        key = self._infer_key_from_label(field_info, is_address=True)
                        if key:
                            # Clean up value
                            value = value_text
                            # Remove prefixes
                            if key == 'street' and value.startswith('вул.'):
                                value = value.replace('вул.', '').strip()
                            elif key == 'condo_complex' and value.startswith('ЖК '):
                                value = value.replace('ЖК ', '').strip()

                            address[key] = value
                            logger.debug(f"Extracted address.{key}={value}")

        # Try to extract metro and house number from characteristics
        for table in tables:
            rows = table.select('tr')
            for row in rows:
                cells = row.select('th, td')
                if len(cells) >= 2:
                    label_text = cells[0].get_text(strip=True).lower()
                    value_text = cells[1].get_text(strip=True)

                    if 'метро' in label_text and value_text:
                        # Store as list
                        address['subway'] = [value_text]
                        logger.debug(f"Extracted subway: {value_text}")
                    elif 'номер будинку' in label_text and value_text:
                        # House number is not in schema but needed for address
                        address['house_number'] = value_text
                        logger.debug(f"Extracted house_number: {value_text}")

        return address if address else {}

    def _extract_summary_stats(self) -> dict:
        """Extract data from summary property values (fallback).

        Returns:
            Dict with rooms, floor, floors_total, areas
        """
        result = {}

        # Find summary property values
        property_values = self.soup.select('.summary-property-value')

        if len(property_values) >= 3:
            # First value: rooms
            rooms_text = property_values[0].get_text(strip=True)
            if rooms_text.isdigit():
                # Need to format as "N кімната/кімнати/кімнат"
                rooms_field = self.label_to_field.get('число кімнат')
                if rooms_field:
                    result['rooms'] = self._normalize_rooms(rooms_text, rooms_field.get('options', []))

            # Second value: floor / total floors
            floor_text = property_values[1].get_text(strip=True)
            match = re.match(r'(\d+)\s*/\s*(\d+)', floor_text)
            if match:
                result['floor'] = match.group(1)
                result['floors_total'] = match.group(2)

            # Third value: areas (total / living / kitchen)
            area_text = property_values[2].get_text(strip=True)
            match = re.match(r'([\d.]+)\s*/\s*([\d.]+)\s*/\s*([\d.]+)', area_text)
            if match:
                result['total_area'] = match.group(1)
                result['living_area'] = match.group(2)
                result['kitchen_area'] = match.group(3)

            logger.debug(f"Extracted summary stats: {result}")

        return result

    def _extract_photos(self) -> dict:
        """Extract photo URLs from gallery.

        Returns:
            Dict with apartment.photos list
        """
        photos = []

        # Find all photo links
        photo_links = self.soup.select('.slider-item.fancybox')
        for link in photo_links:
            href = link.get('href')
            if href:
                photos.append(href)

        logger.debug(f"Extracted {len(photos)} photos")

        if photos:
            return {
                "apartment": {
                    "photos": photos
                }
            }
        return {}

    def _extract_description(self) -> str:
        """Extract description from additional information section.

        Returns:
            Description text or empty string
        """
        # Look for "Додаткова інформація" section
        for elem in self.soup.find_all(['h3', 'h4']):
            if 'додаткова інформація' in elem.get_text(strip=True).lower():
                # Get next paragraph or div
                next_elem = elem.find_next('p')
                if next_elem:
                    text = next_elem.get_text(strip=True)
                    logger.debug(f"Extracted description: {len(text)} chars")
                    return text

        return ""

    def _extract_estate_note(self) -> str:
        """Extract estate note from .estate-note section.

        Returns:
            Note text or empty string
        """
        note_elem = self.soup.select_one('.estate-note span')
        if note_elem:
            text = note_elem.get_text(strip=True)
            logger.debug(f"Extracted estate note: {text}")
            return text
        return ""

    # ==================== Value Normalizers ====================

    def _normalize_value(self, field_info: dict, raw_value: str) -> Any:
        """Normalize value based on field widget type.

        Args:
            field_info: Field definition from schema
            raw_value: Raw text value from HTML

        Returns:
            Normalized value appropriate for the field type
        """
        widget = field_info.get('widget', '')
        options = field_info.get('options', [])

        # Handle select/radio - match against options
        if widget in ['select', 'radio'] and options:
            matched = self._normalize_select_option(raw_value, options)
            return matched

        # Handle checkbox
        if widget == 'checkbox':
            return raw_value.lower() in ['так', 'yes', 'є', 'true', '1']

        # Handle numeric text
        if widget == 'text':
            input_type = field_info.get('meta', {}).get('input_type', '')
            if input_type == 'number':
                # Try to parse as number
                try:
                    if '.' in raw_value:
                        return float(raw_value.replace(',', '.'))
                    return int(raw_value.replace(' ', '').replace(',', ''))
                except ValueError:
                    pass

        # Default: return as-is
        return raw_value

    def _normalize_select_option(self, text: str, options: List[str]) -> str:
        """Fuzzy match text against schema options.

        Args:
            text: Text to match
            options: List of valid options from schema

        Returns:
            Best matching option or original text
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
        if any(word in text_lower for word in ['дизайн', 'євроремонт', 'ремонт']):
            for option in options:
                if 'з ремонтом' in option.lower():
                    return option

        # No match found, return original
        return text

    def _normalize_rooms(self, text: str, options: List[str]) -> str:
        """Normalize room count to schema format.

        Args:
            text: Room count as text or number
            options: Valid room options from schema

        Returns:
            Formatted room string (e.g., "1 кімната", "2 кімнати")
        """
        # Parse number
        try:
            num = int(text)
        except ValueError:
            # Try to extract number from text
            match = re.search(r'\d+', text)
            if match:
                num = int(match.group())
            else:
                return text

        # Match against options
        for option in options:
            if str(num) in option and ('кімнат' in option or 'кімнати' in option or 'кімната' in option):
                return option

        # Fallback: generate standard format
        if num == 1:
            return "1 кімната"
        elif num in [2, 3, 4]:
            return f"{num} кімнати"
        else:
            return f"{num} кімнат"

    def _parse_price(self, text: str) -> tuple[Optional[int], Optional[str]]:
        """Parse price text to amount and currency.

        Args:
            text: Price text like "182 000 $" or "50000 грн"

        Returns:
            Tuple of (amount, currency_text)
        """
        # Remove spaces and find number
        text_clean = text.replace(' ', '').replace(',', '')

        # Extract number
        match = re.search(r'([\d.]+)', text_clean)
        if not match:
            return None, None

        try:
            amount = int(float(match.group(1)))
        except ValueError:
            return None, None

        # Detect currency
        currency = None
        if '$' in text or 'dollar' in text.lower():
            currency = 'доларів'
        elif '€' in text or 'euro' in text.lower():
            currency = 'євро'
        elif 'грн' in text or '₴' in text or 'uah' in text.lower():
            currency = 'гривень'

        return amount, currency

    def _infer_key_from_label(self, field_info: dict, is_address: bool = False) -> Optional[str]:
        """Infer programmatic key from field label.

        Args:
            field_info: Field definition from schema
            is_address: Whether this is an address field

        Returns:
            Programmatic key string or None
        """
        label = field_info['label'].lower().strip()

        # Direct label to key mappings
        key_map = {
            # Basic
            'ціна': 'price',
            'валюта': 'currency',
            'переуступка': 'assignment',
            'комісія з покупця/орендатора': 'buyer_commission',

            # Property info
            'число кімнат': 'rooms',
            'поверх': 'floor',
            'поверховість': 'floors_total',
            'загальний стан': 'condition',
            'тип будинку': 'building_type',
            'технологія будівництва': 'construction_technology',
            'загальна площа, м²': 'total_area',
            'житлова площа, м²': 'living_area',
            'площа кухні, м²': 'kitchen_area',
            'рік будівництва': 'year_built',
            'планування кімнат': 'room_layout',

            # Address
            'місто': 'city',
            'район': 'district',
            'вулиця': 'street',
            'будинок': 'house_number',
            'номер будинку': 'house_number',
            'новобудова': 'condo_complex',
            'жилий комплекс': 'condo_complex',
            'метро': 'subway',
            'орієнтир': 'guide',
            'область': 'region',

            # Additional
            'опалення': 'heating',
            'гаряча вода': 'hot_water',
            'газ': 'gas',
            'інтернет': 'internet',
            'особисті нотатки': 'personal_notes',
        }

        key = key_map.get(label)
        if key:
            return key

        # Fallback: use label as key (with normalization)
        logger.debug(f"No direct key mapping for label: '{label}', skipping")
        return None

    def _look_up_field_by_html_label(self, html_label: str) -> Optional[dict]:
        """Look up field info by HTML table label (not schema label).

        Some HTML tables use different labels than the schema.
        For example, HTML has "Ремонт" but schema has "Загальний стан".

        Args:
            html_label: Label text from HTML

        Returns:
            Field info dict or None
        """
        html_label_lower = html_label.lower().strip()

        # HTML label → Schema label mapping
        html_to_schema = {
            'ремонт': 'загальний стан',
            'площа загальна,м²': 'загальна площа, м²',
            'площа житлова,м²': 'житлова площа, м²',
            'площа кухні,м²': 'площа кухні, м²',
            'кіл. кімнат': 'число кімнат',
            'номер будинку': 'будинок',
            'жилий комплекс': 'новобудова',
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

    def _validate_required_fields(self, data: dict) -> List[str]:
        """Validate that all required fields are present.

        Args:
            data: Parsed offer data

        Returns:
            List of missing required field labels
        """
        missing = []

        for field in self.required_fields:
            label = field['label']
            key = self._infer_key_from_label(field)

            if not key:
                continue

            # Check if key exists in data
            if key in data and data[key]:
                continue

            # Check in nested address
            if 'address' in data and key in data['address'] and data['address'][key]:
                continue

            # Field is missing
            missing.append(label)

        return missing

    def _fill_missing_with_defaults(self, data: dict) -> dict:
        """Fill missing fields with sensible defaults where possible.

        Args:
            data: Parsed offer data

        Returns:
            Data with defaults filled
        """
        # Ensure address dict exists
        if 'address' not in data:
            data['address'] = {}

        # Default currency if price exists but currency doesn't
        if 'price' in data and not data.get('currency'):
            data['currency'] = 'доларів'  # Default to dollars
            logger.debug("Defaulted currency to 'доларів'")

        return data
