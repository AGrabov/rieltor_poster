from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from setup_logger import setup_logger

logger = setup_logger(__name__)


class DescriptionAnalyzer:
    """Analyzes description text to extract additional field values.

    Uses schema field options and text pattern matching to mine
    information from unstructured description text.
    """

    def __init__(self, schema: List[dict], debug: bool = False):
        """Initialize analyzer with schema.

        Args:
            schema: List of field definitions from schema_dump
            debug: Enable debug logging
        """
        self.schema = schema
        self.debug = debug
        self.label_to_field = self._create_label_mapping()

    def _create_label_mapping(self) -> Dict[str, dict]:
        """Create reverse mapping from label to field info."""
        mapping = {}
        for field in self.schema:
            label = field.get('label', '').lower().strip()
            if label:
                mapping[label] = field
        return mapping

    def analyze(self, description: str, existing_data: dict) -> dict:
        """Analyze description text and extract additional field values.

        Only extracts fields that are NOT already in existing_data.

        Args:
            description: Description text to analyze
            existing_data: Already extracted data (won't override)

        Returns:
            Dict with newly extracted fields
        """
        if not description:
            return {}

        extracted = {}
        description_lower = description.lower()

        # Match against field options for select/radio/checklist widgets
        option_matches = self._match_field_options(description_lower, existing_data)
        extracted.update(option_matches)

        # Extract specific patterns (year, numbers, etc.)
        pattern_matches = self._extract_patterns(description_lower, existing_data)
        extracted.update(pattern_matches)

        if self.debug and extracted:
            logger.debug(f"DescriptionAnalyzer extracted {len(extracted)} fields: {list(extracted.keys())}")

        return extracted

    def _match_field_options(self, text: str, existing_data: dict) -> dict:
        """Match description text against field options.

        For fields with options (select, radio, checklist), check if any
        option value appears in the description text.

        Args:
            text: Lowercase description text
            existing_data: Already extracted data

        Returns:
            Dict with matched fields
        """
        extracted = {}

        for field in self.schema:
            widget = field.get('widget')
            options = field.get('options', [])

            # Skip if no options or already extracted
            if not options:
                continue

            # Get programmatic key for this field
            key = self._infer_key_from_field(field)
            if not key or key in existing_data:
                continue

            # Check if field is in address section
            is_address = field.get('section', '').lower() == 'адреса об\'єкта'
            if is_address and 'address' in existing_data and key in existing_data.get('address', {}):
                continue

            # Match options against text
            if widget in ['select', 'radio']:
                # Single select - find first matching option
                for option in options:
                    option_lower = option.lower()
                    if option_lower in text:
                        extracted[key] = option
                        if self.debug:
                            logger.debug(f"Matched {key}={option} in description")
                        break

            elif widget == 'checklist':
                # Multi-select - find all matching options
                matched_options = []
                for option in options:
                    option_lower = option.lower()
                    if option_lower in text:
                        matched_options.append(option)

                if matched_options:
                    extracted[key] = matched_options
                    if self.debug:
                        logger.debug(f"Matched {key}={matched_options} in description")

        return extracted

    def _extract_patterns(self, text: str, existing_data: dict) -> dict:
        """Extract specific patterns from text.

        Looks for:
        - Year built (4-digit year between 1800-2100)
        - Ceiling height (numbers + м)
        - Common keywords for boolean fields

        Args:
            text: Lowercase description text
            existing_data: Already extracted data

        Returns:
            Dict with pattern-matched fields
        """
        extracted = {}

        # Extract year_built if not present
        if 'year_built' not in existing_data:
            year_patterns = [
                r'(?:побудован[оаи]?|збудован[оаи]?|рік будівництва|року)\s*(?:в|у)?\s*(\d{4})',
                r'(\d{4})\s*рік[уа]?\s*(?:будівництва|побудови)',
                r'новобудова\s*(\d{4})',
            ]
            for pattern in year_patterns:
                match = re.search(pattern, text)
                if match:
                    year = int(match.group(1))
                    if 1800 <= year <= 2100:
                        extracted['year_built'] = str(year)
                        if self.debug:
                            logger.debug(f"Extracted year_built={year} from description")
                        break

        # Extract ceiling_height if not present
        if 'ceiling_height' not in existing_data:
            height_patterns = [
                r'висота стель\s*[-–—]?\s*(\d+(?:[.,]\d+)?)\s*м',
                r'стелі\s*[-–—]?\s*(\d+(?:[.,]\d+)?)\s*м',
                r'(\d+(?:[.,]\d+)?)\s*м\s*стелі',
            ]
            for pattern in height_patterns:
                match = re.search(pattern, text)
                if match:
                    height = match.group(1).replace(',', '.')
                    extracted['ceiling_height'] = height
                    if self.debug:
                        logger.debug(f"Extracted ceiling_height={height} from description")
                    break

        # Check for heating/hot_water boolean fields
        if 'heating' not in existing_data:
            if any(word in text for word in ['опалення', 'опален', 'тепло']):
                extracted['heating'] = True

        if 'hot_water' not in existing_data:
            if any(word in text for word in ['гаряча вода', 'бойлер', 'водонагрівач']):
                extracted['hot_water'] = True

        return extracted

    def _infer_key_from_field(self, field: dict) -> Optional[str]:
        """Infer programmatic key from field definition.

        Args:
            field: Field definition from schema

        Returns:
            Programmatic key or None
        """
        label = field.get('label', '').lower().strip()

        # Mapping of schema labels to programmatic keys
        key_map = {
            # Basic fields
            'рік будівництва': 'year_built',
            'висота стелі, м': 'ceiling_height',
            'опалення': 'heating',
            'тип опалення': 'heating_type',
            'гаряча вода': 'hot_water',
            'тип квартири': 'apartment_type',
            'санвузол': 'bathroom',
            'планування кімнат': 'room_layout',
            'тип будівлі': 'building_type',
            'технологія будівництва': 'construction_technology',

            # BoolGroups
            'доступність для людей з інвалідністю': 'accessibility',
            'поруч є': 'nearby',
            'у квартирі є': 'in_apartment',
            'особливі умови': 'special_conditions',
            'без світла': 'without_power_supply',
            'вид з вікон': 'windows_view',
            'додатково': 'additional',
        }

        return key_map.get(label)