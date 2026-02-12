from __future__ import annotations

import re
from typing import Dict, List

from setup_logger import setup_logger

logger = setup_logger(__name__)


# Contextual regex patterns: schema_label_lower → list of (regex, matched_value)
# These patterns look for specific phrases in description text.
CONTEXTUAL_PATTERNS: Dict[str, List[tuple]] = {
    "у квартирі є": [
        (r"холодильник", "Холодильник"),
        (r"телевізор", "Телевізор"),
        (r"пральн\w+\s+машин", "Пральна машина"),
        (r"сушильн\w+\s+машин", "Сушильна машина"),
        (r"посудомийн\w+\s+машин", "Посудомийна машина"),
        (r"кондиціонер", "Кондиціонер"),
        (r"мікрохвильов", "Мікрохвильовка"),
        (r"душов\w+\s+кабін", "Душова кабіна"),
        (r"джакузі", "Джакузі"),
        (r"камін(?!ь)", "Камін"),
        (r"підігрів\s+підлоги|тепл\w+\s+підлог", "Підігрів підлоги"),
        (r"сигналізаці", "Сигналізація"),
        (r"лічильник", "Лічильники"),
        (r"сейф", "Сейф"),
        (r"(?:вбудован\w+\s+)?шаф[аіу]", "Шафа"),
        (r"ліжк[оа]", "Ліжко"),
        (r"ванн[аіу](?!\s*кімнат)", "Ванна"),
    ],
    "тип опалення": [
        (r"(?:автономн\w+|індивідуальн\w+)\s+опаленн", "Автономне"),
        (r"центральн\w+\s+опаленн", "Центральне"),
        (r"індивідуальн\w+\s+опаленн", "Індивідуальне"),
    ],
    "технологія будівництва": [
        (r"цегл(?:ян\w+|а)\s*(?:будинок|буд\.)?", "Цегляна"),
        (r"панельн\w+", "Панельна"),
        (r"монолітн\w+[-\s]?каркасн\w+", "Монолітно-каркасна"),
        (r"блочн\w+", "Блочна"),
    ],
    "загальний стан": [
        (r"(?:з\s+)?ремонт(?:ом|у)|після\s+ремонту|авторськ\w+\s+ремонт", "З ремонтом"),
        (r"без\s+ремонту", "Без ремонту"),
        (r"частков\w+\s+ремонт", "Частковий ремонт"),
    ],
    "санвузол": [
        (r"роздільн\w+\s+санвузол", "Роздільний"),
        (r"суміщен\w+\s+санвузол", "Суміщений"),
    ],
    "планування кімнат": [
        (r"студі[яю]", "Студія"),
        (r"пентхаус", "Пентхаус"),
        (r"кухня[-\s]?вітальн", "Кухня-вітальня"),
    ],
    "вид із вікон": [
        (r"вид(?:ом)?\s+(?:у|на|в)\s+двір", "У двір"),
        (r"вид(?:ом)?\s+на\s+парк", "На парк"),
        (r"вид(?:ом)?\s+на\s+місто", "На місто"),
        (r"вид(?:ом)?\s+на\s+море", "На море"),
        (r"вид(?:ом)?\s+на\s+рік[уа]", "На ріку"),
    ],
    "поруч є": [
        (r"парк(?!\s*tower|\s*city|інг)", "Парк"),
        (r"школ[аи]", "Школа"),
        (r"дитсадок|дитяч\w+\s+сад", "Дитсадок"),
        (r"супермаркет", "Супермаркет"),
        (r"зупинк", "Зупинки"),
    ],
}


class DescriptionAnalyzer:
    """Analyzes description text to extract additional field values.

    Uses schema field options, contextual regex patterns, and numeric
    pattern matching to mine information from unstructured description text.
    """

    def __init__(self, schema: List[dict], debug: bool = False):
        self.schema = schema
        self.debug = debug
        self.label_to_field = self._create_label_mapping()

    def _create_label_mapping(self) -> Dict[str, dict]:
        """Create reverse mapping from label_lower to field info."""
        mapping = {}
        for field in self.schema:
            label = field.get('label', '').lower().strip()
            if label:
                mapping[label] = field
        return mapping

    def analyze(self, description: str, existing_data: dict) -> dict:
        """Analyze description text and extract additional field values.

        Only extracts fields that are NOT already in existing_data.
        """
        if not description:
            return {}

        extracted = {}
        description_lower = description.lower()

        # 1) Match against field options for select/radio/checklist widgets
        option_matches = self._match_field_options(description_lower, existing_data)
        extracted.update(option_matches)

        # 2) Contextual patterns (appliances, heating type, condition, etc.)
        context_matches = self._extract_by_context(description_lower, existing_data)
        extracted.update(context_matches)

        # 3) Numeric patterns (areas, floor, rooms, price, year, ceiling)
        numeric_matches = self._extract_numeric_fields(description_lower, existing_data)
        extracted.update(numeric_matches)

        # 4) Simple keyword patterns (heating boolean, hot water)
        pattern_matches = self._extract_keyword_patterns(description_lower, existing_data)
        extracted.update(pattern_matches)

        if self.debug and extracted:
            logger.debug(f"DescriptionAnalyzer extracted {len(extracted)} fields: {list(extracted.keys())}")

        return extracted

    @staticmethod
    def _option_in_text(option_lower: str, text: str) -> bool:
        """Check if option appears as a whole word in text (not as a substring of another word)."""
        escaped = re.escape(option_lower)
        # For purely numeric options (e.g. "1", "2"), require digit boundaries
        # to avoid matching inside larger numbers like "#27274"
        if re.fullmatch(r'\d+', option_lower):
            pattern = r'(?<!\d)' + escaped + r'(?!\d)'
        else:
            pattern = escaped + r'(?![а-яіїєґь])'
        return bool(re.search(pattern, text))

    def _match_field_options(self, text: str, existing_data: dict) -> dict:
        """Match description text against field options.

        For fields with options (select, radio, checklist), check if any
        option value appears in the description text.
        """
        extracted = {}

        for field in self.schema:
            widget = field.get('widget')
            options = field.get('options', [])

            if not options:
                continue

            key = field.get('label', '').strip()
            if not key or key in existing_data:
                continue

            is_address = field.get('section', '').lower() == 'адреса об\'єкта'
            if is_address:
                continue

            if widget in ['select', 'radio']:
                for option in options:
                    option_lower = option.lower()
                    if self._option_in_text(option_lower, text):
                        extracted[key] = option
                        if self.debug:
                            logger.debug(f"Matched {key}={option} in description")
                        break

            elif widget == 'checklist':
                matched_options = []
                for option in options:
                    option_lower = option.lower()
                    if self._option_in_text(option_lower, text):
                        matched_options.append(option)

                if matched_options:
                    extracted[key] = matched_options
                    if self.debug:
                        logger.debug(f"Matched {key}={matched_options} in description")

        return extracted

    def _extract_by_context(self, text: str, existing_data: dict) -> dict:
        """Extract values using contextual regex patterns.

        Matches patterns from CONTEXTUAL_PATTERNS against the text,
        resolving pattern keys to actual schema labels.
        """
        extracted = {}

        for pattern_key, patterns in CONTEXTUAL_PATTERNS.items():
            # Resolve pattern key to actual schema label
            field_info = self.label_to_field.get(pattern_key)
            if not field_info:
                continue

            schema_label = field_info['label']
            if schema_label in existing_data:
                continue

            widget = field_info.get('widget', '')
            options = field_info.get('options', [])

            matches = []
            for regex, value in patterns:
                if re.search(regex, text):
                    # Validate against schema options if available
                    if options and value not in options:
                        continue
                    if value not in matches:
                        matches.append(value)

            if not matches:
                continue

            # checklist fields → list of values, select/radio → single value
            if widget == 'checklist' or len(matches) > 1:
                # Merge with existing option_matches if already extracted above
                if schema_label in extracted:
                    existing = extracted[schema_label]
                    if isinstance(existing, list):
                        for m in matches:
                            if m not in existing:
                                existing.append(m)
                    continue
                extracted[schema_label] = matches
            else:
                if schema_label not in extracted:
                    extracted[schema_label] = matches[0]

            if self.debug:
                logger.debug(f"Context matched {schema_label}={extracted[schema_label]}")

        return extracted

    def _extract_numeric_fields(self, text: str, existing_data: dict) -> dict:
        """Extract numeric field values (areas, floors, rooms, price, year, ceiling)."""
        extracted = {}

        # --- Areas ---
        area_patterns = [
            (r"загальн\w*\s+площ\w*[:\s-]*(\d+[.,]?\d*)\s*(?:м²|м\.?\s*кв\.?|кв\.?\s*м)", "Загальна площа, м²"),
            (r"(\d+[.,]?\d*)\s*(?:м²|м\.?\s*кв\.?)\s*загальн", "Загальна площа, м²"),
            (r"житлов\w*\s+площ\w*[:\s-]*(\d+[.,]?\d*)", "Житлова площа, м²"),
            (r"площ\w*\s+кухн\w*[:\s-]*(\d+[.,]?\d*)", "Площа кухні, м²"),
            (r"кухн\w*[:\s-]*(\d+[.,]?\d*)\s*(?:м²|м\.?\s*кв\.?|кв\.?\s*м)", "Площа кухні, м²"),
        ]

        for pattern, label in area_patterns:
            if label not in existing_data and label not in extracted:
                match = re.search(pattern, text)
                if match:
                    extracted[label] = match.group(1).replace(",", ".")

        # Format: "85/50/12" (total/living/kitchen)
        area_slash = re.search(r"(\d+)[/\\](\d+)[/\\](\d+)", text)
        if area_slash:
            for label, group in [
                ("Загальна площа, м²", 1),
                ("Житлова площа, м²", 2),
                ("Площа кухні, м²", 3),
            ]:
                if label not in existing_data and label not in extracted:
                    extracted[label] = area_slash.group(group)

        # --- Floor ---
        if 'Поверх' not in existing_data and 'Поверх' not in extracted:
            floor_pattern = r"(\d+)\s*поверх\s*[/зі]+\s*(\d+)"
            floor_match = re.search(floor_pattern, text)
            if floor_match:
                extracted['Поверх'] = floor_match.group(1)
                if 'Поверховість' not in existing_data:
                    extracted['Поверховість'] = floor_match.group(2)

        # --- Room count ---
        if 'Число кімнат' not in existing_data and 'Число кімнат' not in extracted:
            room_patterns = [
                (r"однокімнатн", "1 кімната"),
                (r"двокімнатн", "2 кімнати"),
                (r"трикімнатн|трьохкімнатн|3[-\s]?кімнатн", "3 кімнати"),
                (r"чотирикімнатн|4[-\s]?кімнатн", "4 кімнати"),
            ]
            for pattern, value in room_patterns:
                if re.search(pattern, text):
                    # Validate against schema options
                    field_info = self.label_to_field.get('число кімнат')
                    if field_info:
                        options = field_info.get('options', [])
                        if options and value not in options:
                            for opt in options:
                                if value.split()[0] in opt:
                                    value = opt
                                    break
                    extracted['Число кімнат'] = value
                    break

            # Generic N-кімнатна
            if 'Число кімнат' not in extracted:
                match = re.search(r"(\d+)[-\s]?кімнатн", text)
                if match:
                    num = match.group(1)
                    room_map = {
                        "1": "1 кімната", "2": "2 кімнати", "3": "3 кімнати",
                        "4": "4 кімнати", "5": "5 кімнат", "6": "6 кімнат і більше",
                    }
                    value = room_map.get(num, f"{num} кімнат")
                    field_info = self.label_to_field.get('число кімнат')
                    if field_info:
                        options = field_info.get('options', [])
                        if options and value not in options:
                            for opt in options:
                                if num in opt:
                                    value = opt
                                    break
                    extracted['Число кімнат'] = value

        # --- Price ---
        if 'Ціна' not in existing_data and 'Ціна' not in extracted:
            price_patterns = [
                (r"ціна[:\s]*(\d[\d\s]*)\s*(?:грн|гривень)", "гривень"),
                (r"ціна[:\s]*(\d[\d\s]*)\s*(?:дол|\$|usd)", "доларів"),
                (r"ціна[:\s]*(\d[\d\s]*)\s*(?:євро|€|eur)", "євро"),
                (r"(\d[\d\s]*)\s*(?:грн|гривень)", "гривень"),
                (r"(\d[\d\s]*)\s*(?:дол(?:ар)?|\$|usd)", "доларів"),
                (r"(\d[\d\s]*)\s*(?:євро|€|eur)", "євро"),
            ]
            for pattern, currency in price_patterns:
                match = re.search(pattern, text)
                if match:
                    price = re.sub(r"\s+", "", match.group(1))
                    if len(price) >= 4:  # at least 1000
                        extracted['Ціна'] = price
                        if 'Валюта' not in existing_data:
                            # Validate currency against schema options
                            field_info = self.label_to_field.get('валюта')
                            if field_info:
                                options = field_info.get('options', [])
                                if options:
                                    for opt in options:
                                        if currency.lower() in opt.lower():
                                            currency = opt
                                            break
                            extracted['Валюта'] = currency
                        break

        # --- Year built ---
        if 'Рік будівництва' not in existing_data and 'Рік будівництва' not in extracted:
            year_patterns = [
                r'(?:рік\s+)?(?:будівництва|побудови)[:\s]*(\d{4})',
                r'(?:побудован[оаи]?|збудован[оаи]?)\s*(?:в|у)?\s*(\d{4})',
                r'(\d{4})\s*рік[уа]?\s*(?:будівництва|побудови)',
                r'новобудова\s*(\d{4})',
            ]
            for pattern in year_patterns:
                match = re.search(pattern, text)
                if match:
                    year = int(match.group(1))
                    if 1800 <= year <= 2100:
                        extracted['Рік будівництва'] = str(year)
                        if self.debug:
                            logger.debug(f"Extracted Рік будівництва={year}")
                        break

        # --- Bathroom count ---
        if 'Кількість санвузлів' not in existing_data and 'Кількість санвузлів' not in extracted:
            _WORD_TO_NUM = {
                'один': 1, 'одн': 1,
                'два': 2, 'двох': 2, 'двома': 2,
                'три': 3, 'трьох': 3, 'трьома': 3,
                'чотири': 4, 'п\'ять': 5,
            }
            bathroom_count = 0
            # 1) Explicit number before санвузл: "2 санвузли", "два санвузли"
            num_match = re.search(r'(\d+|один|одн\w*|два|двох|двома|три|трьох|трьома|чотири|п\'ять)\s+санвуз', text)
            if num_match:
                token = num_match.group(1)
                if token.isdigit():
                    bathroom_count = int(token)
                else:
                    for word, num in _WORD_TO_NUM.items():
                        if token.startswith(word):
                            bathroom_count = num
                            break
            # 2) Fallback: count separate mentions
            if bathroom_count == 0:
                bathroom_count = len(re.findall(r'санвузо[лк]|санвузл', text))

            if bathroom_count > 0:
                field_info = self.label_to_field.get('кількість санвузлів')
                if field_info:
                    options = field_info.get('options', [])
                    if bathroom_count >= 3:
                        value = '3 і більше'
                    else:
                        value = str(bathroom_count)
                    # Validate against options
                    if options and value not in options:
                        for opt in options:
                            if str(bathroom_count) in opt:
                                value = opt
                                break
                    extracted['Кількість санвузлів'] = value

        # --- Ceiling height ---
        if 'Висота стель' not in existing_data and 'Висота стель' not in extracted:
            height_patterns = [
                r'висот\w*\s+стел\w*[:\s-]*(\d+(?:[.,]\d+)?)\s*м?',
                r'стелі\s*[-–—]?\s*(\d+(?:[.,]\d+)?)\s*м',
                r'(\d+(?:[.,]\d+)?)\s*м\s*стелі',
            ]
            for pattern in height_patterns:
                match = re.search(pattern, text)
                if match:
                    height = match.group(1).replace(',', '.')
                    extracted['Висота стель'] = height
                    if self.debug:
                        logger.debug(f"Extracted Висота стель={height}")
                    break

        return extracted

    def _extract_keyword_patterns(self, text: str, existing_data: dict) -> dict:
        """Extract simple keyword-based boolean fields."""
        extracted = {}

        if 'Опалення' not in existing_data:
            if any(word in text for word in ['опалення', 'опален', 'тепло']):
                extracted['Опалення'] = True

        if 'Гаряча вода' not in existing_data:
            if any(word in text for word in ['гаряча вода', 'бойлер', 'водонагрівач']):
                extracted['Гаряча вода'] = True

        return extracted
