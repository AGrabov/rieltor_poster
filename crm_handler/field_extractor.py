"""
Field extractor using spaCy for Ukrainian language.
Extracts real estate field data from text based on schema definitions.
"""

import json
import re
from pathlib import Path
from typing import Any

import spacy


class FieldExtractor:
    """Extracts field data from text using spaCy Ukrainian model."""

    SCHEMA_DIR = Path(__file__).parent / "schemas" / "schema_dump"

    # Options that are too generic to match without context
    AMBIGUOUS_OPTIONS = {
        "є", "немає", "так", "ні",
        "1", "2", "3", "4", "5",
        "нормальний", "нові",
    }

    # Specific field extraction patterns with context
    CONTEXTUAL_PATTERNS = {
        "в квартирі є": [
            (r"холодильник", "Холодильник"),
            (r"телевізор", "Телевізор"),
            (r"пральн\w+\s+машин", "Пральна машина"),
            (r"сушильн\w+\s+машин", "Сушильна машина"),
            (r"посудомийн\w+\s+машин", "Посудомийна машина"),
            (r"кондиціонер", "Кондиціонер"),
            (r"мікрохвильов", "Мікрохвильовка"),
            (r"душов\w+\s+кабін", "Душова кабіна"),
            (r"джакузі", "Джакузі"),
            (r"камін", "Камін"),
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
            (r"вид(?:ом)?\s+на\s+рік[у|а]", "На ріку"),
        ],
        "поруч є": [
            (r"парк(?!\s*tower|\s*city|інг)", "Парк"),
            (r"школ[аи]", "Школа"),
            (r"дитсадок|дитяч\w+\s+сад", "Дитсадок"),
            (r"супермаркет", "Супермаркет"),
            (r"зупинк", "Зупинки"),
            (r"метро", "Зупинки"),
        ],
    }

    def __init__(self, schema_name: str = "Квартира"):
        """
        Initialize the field extractor.

        Args:
            schema_name: Name of the schema file (without .json extension).
                        Available: Квартира, Кімната, Будинок, Комерційна, Ділянка, Паркомісце
        """
        self.nlp = self._load_spacy_model()
        self.schema = self._load_schema(schema_name)
        self.fields = self._parse_fields()

    def _load_spacy_model(self) -> spacy.Language:
        """Load Ukrainian spaCy model."""
        try:
            return spacy.load("uk_core_news_sm")
        except OSError:
            raise RuntimeError(
                "Ukrainian spaCy model not found. Install it with:\n"
                "python -m spacy download uk_core_news_sm"
            )

    def _load_schema(self, schema_name: str) -> dict:
        """Load schema from JSON file."""
        schema_path = self.SCHEMA_DIR / f"{schema_name}.json"
        if not schema_path.exists():
            available = [f.stem for f in self.SCHEMA_DIR.glob("*.json")]
            raise FileNotFoundError(
                f"Schema '{schema_name}' not found. Available schemas: {available}"
            )
        with open(schema_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _parse_fields(self) -> dict[str, dict]:
        """Parse fields from schema into a lookup dictionary."""
        fields = {}
        for field in self.schema.get("fields", []):
            label = field.get("label", "").lower()
            if label:
                fields[label] = {
                    "label": field.get("label"),
                    "widget": field.get("widget"),
                    "required": field.get("required", False),
                    "options": field.get("options", []),
                    "section": field.get("section"),
                    "field_key": field.get("meta", {}).get("field_key", ""),
                }
        return fields

    def extract(self, text: str) -> dict[str, Any]:
        """
        Extract field values from text.

        Args:
            text: Input text describing a real estate property.

        Returns:
            Dictionary with extracted field values.
        """
        # Clean HTML tags
        clean_text = re.sub(r"<[^>]+>", " ", text)
        clean_text = re.sub(r"\s+", " ", clean_text)

        extracted = {}

        # Extract using contextual patterns (most reliable)
        extracted.update(self._extract_by_context(clean_text))

        # Extract numeric values (area, floor, price, etc.)
        extracted.update(self._extract_numeric_fields(clean_text))

        # Extract address components
        extracted.update(self._extract_address(clean_text))

        return extracted

    def _extract_by_context(self, text: str) -> dict[str, Any]:
        """Extract values using contextual patterns."""
        extracted = {}
        text_lower = text.lower()

        for field_label, patterns in self.CONTEXTUAL_PATTERNS.items():
            matches = []
            for pattern, value in patterns:
                if re.search(pattern, text_lower):
                    if value not in matches:
                        matches.append(value)

            if matches:
                if len(matches) == 1:
                    extracted[field_label] = matches[0]
                else:
                    extracted[field_label] = matches

        return extracted

    def _extract_numeric_fields(self, text: str) -> dict[str, Any]:
        """Extract numeric field values using regex patterns."""
        extracted = {}
        text_lower = text.lower()

        # Area patterns - handle various formats
        # Format: "Загальна площа 45 м.кв." or "45 м²"
        area_patterns = [
            (r"загальн\w*\s+площ\w*[:\s-]*(\d+[.,]?\d*)\s*(?:м²|м\.?\s*кв\.?|кв\.?\s*м)", "загальна площа, м²"),
            (r"(\d+[.,]?\d*)\s*(?:м²|м\.?\s*кв\.?)\s*загальн", "загальна площа, м²"),
            (r"житлов\w*\s+площ\w*[:\s-]*(\d+[.,]?\d*)", "житлова площа, м²"),
            (r"площ\w*\s+кухн\w*[:\s-]*(\d+[.,]?\d*)", "площа кухні, м²"),
            (r"кухн\w*[:\s-]*(\d+[.,]?\d*)\s*(?:м²|м\.?\s*кв\.?|кв\.?\s*м)?", "площа кухні, м²"),
        ]

        for pattern, field_name in area_patterns:
            if field_name not in extracted:
                match = re.search(pattern, text_lower)
                if match:
                    extracted[field_name] = match.group(1).replace(",", ".")

        # Format: "85/50/12" (total/living/kitchen)
        area_slash = re.search(r"(\d+)[/\\](\d+)[/\\](\d+)", text_lower)
        if area_slash:
            if "загальна площа, м²" not in extracted:
                extracted["загальна площа, м²"] = area_slash.group(1)
            if "житлова площа, м²" not in extracted:
                extracted["житлова площа, м²"] = area_slash.group(2)
            if "площа кухні, м²" not in extracted:
                extracted["площа кухні, м²"] = area_slash.group(3)

        # Floor patterns: "3 поверх / 25" or "3 поверх з 25"
        floor_pattern = r"(\d+)\s*поверх\s*[/зі]+\s*(\d+)"
        floor_match = re.search(floor_pattern, text_lower)
        if floor_match:
            extracted["поверх"] = floor_match.group(1)
            extracted["поверховість"] = floor_match.group(2)

        # Room count
        room_patterns = [
            (r"однокімнатн", "1 кімната"),
            (r"двокімнатн", "2 кімнати"),
            (r"трикімнатн|трьохкімнатн|3[-\s]?кімнатн", "3 кімнати"),
            (r"чотирикімнатн|4[-\s]?кімнатн", "4 кімнати"),
            (r"(\d+)[-\s]?кімнатн", None),
        ]

        for item in room_patterns:
            if "число кімнат" in extracted:
                break
            if len(item) == 2:
                pattern, value = item
                if value is None:
                    match = re.search(pattern, text_lower)
                    if match:
                        num = match.group(1)
                        room_map = {
                            "1": "1 кімната", "2": "2 кімнати", "3": "3 кімнати",
                            "4": "4 кімнати", "5": "5 кімнат", "6": "6 кімнат і більше"
                        }
                        extracted["число кімнат"] = room_map.get(num, f"{num} кімнат")
                elif re.search(pattern, text_lower):
                    extracted["число кімнат"] = value

        # Price with currency
        price_patterns = [
            (r"ціна[:\s]*(\d[\d\s]*)\s*(?:грн|гривень)", "ціна", "гривень"),
            (r"ціна[:\s]*(\d[\d\s]*)\s*(?:дол|\$|usd)", "ціна", "доларів"),
            (r"ціна[:\s]*(\d[\d\s]*)\s*(?:євро|€|eur)", "ціна", "євро"),
            (r"(\d[\d\s]*)\s*(?:грн|гривень)", "ціна", "гривень"),
            (r"(\d[\d\s]*)\s*(?:дол(?:ар)?|\$|usd)", "ціна", "доларів"),
            (r"(\d[\d\s]*)\s*(?:євро|€|eur)", "ціна", "євро"),
        ]

        for pattern, field_name, currency in price_patterns:
            if field_name not in extracted:
                match = re.search(pattern, text_lower)
                if match:
                    price = re.sub(r"\s+", "", match.group(1))
                    if len(price) >= 4:  # Reasonable price (at least 1000)
                        extracted[field_name] = price
                        extracted["валюта"] = currency
                        break

        # Year built (building construction, NOT renovation)
        year_patterns = [
            r"(?:рік\s+)?(?:будівництва|побудови)[:\s]*(\d{4})",
            r"(?:побудов\w+|збудов\w+)\s+(?:в|у)?\s*(\d{4})",
            r"(\d{4})\s*(?:рік|р\.?)\s*(?:будівництва|побудови)",
        ]
        for pattern in year_patterns:
            match = re.search(pattern, text_lower)
            if match:
                year = match.group(1)
                if 1900 <= int(year) <= 2030:
                    extracted["рік будівництва"] = year
                    break

        # Ceiling height
        height_match = re.search(r"висот\w*\s+стел\w*[:\s-]*(\d+[.,]?\d*)", text_lower)
        if height_match:
            extracted["висота стель"] = height_match.group(1).replace(",", ".")

        return extracted

    def _extract_address(self, text: str) -> dict[str, Any]:
        """Extract address components from text."""
        extracted = {}
        text_lower = text.lower()

        # Street patterns
        street_patterns = [
            r"([А-ЯІЇЄҐа-яіїєґ']+)\s+вул(?:иц[яі])?\.?,?\s*(\d+[-/]?[А-ЯІЇЄҐа-яіїєґ]?)?",
            r"(?:вул(?:иц[яі])?\.?|вулиця)\s+([А-ЯІЇЄҐа-яіїєґ']+(?:\s+[А-ЯІЇЄҐа-яіїєґ']+)?),?\s*(\d+[-/]?[А-ЯІЇЄҐа-яіїєґ]?)?",
            r"(?:просп(?:ект)?\.?)\s+([А-ЯІЇЄҐа-яіїєґ']+(?:\s+[А-ЯІЇЄҐа-яіїєґ']+)?),?\s*(\d+[-/]?[А-ЯІЇЄҐа-яіїєґ]?)?",
            r"(?:бульв(?:ар)?\.?)\s+([А-ЯІЇЄҐа-яіїєґ']+(?:\s+[А-ЯІЇЄҐа-яіїєґ']+)?),?\s*(\d+[-/]?[А-ЯІЇЄҐа-яіїєґ]?)?",
        ]

        for pattern in street_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                street = match.group(1).strip()
                # Skip words that are not street names
                if street.lower() not in {"адресою", "за", "по", "на"}:
                    extracted["вулиця"] = street
                    # Also extract building number if present
                    if match.lastindex >= 2 and match.group(2):
                        extracted["будинок"] = match.group(2).strip()
                    break

        # District
        district_patterns = [
            r"([А-ЯІЇЄҐа-яіїєґ']+ськ(?:ий|ого))\s+район",
            r"район[:\s]+([А-ЯІЇЄҐа-яіїєґ']+ськ(?:ий|ого)?)",
        ]

        for pattern in district_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                district = match.group(1)
                # Normalize to nominative case
                if district.endswith("ого"):
                    district = district[:-3] + "ий"
                extracted["район"] = district.capitalize()
                break

        # City - common Ukrainian cities
        cities = ["київ", "харків", "одеса", "дніпро", "львів", "запоріжжя", "кривий ріг"]
        for city in cities:
            if city in text_lower:
                extracted["місто"] = city.capitalize()
                break

        # Metro station - capture only the station name (one or two words, stop at punctuation or conjunctions)
        metro_match = re.search(r"метро\s+([А-ЯІЇЄҐа-яіїєґ']+(?:[-\s][А-ЯІЇЄҐа-яіїєґ']+)?)", text, re.IGNORECASE)
        if metro_match:
            metro = metro_match.group(1).strip()
            # Remove trailing conjunctions
            metro = re.sub(r"\s+(та|і|й|або)$", "", metro, flags=re.IGNORECASE)
            extracted["метро"] = metro

        # Residential complex (ЖК) - capture only the name, stop at common words
        jk_match = re.search(
            r"(?:жк|житлов\w+\s+комплекс)\s+[«\"']?([A-Za-zА-ЯІЇЄҐа-яіїєґ]+(?:\s+[A-Za-zА-ЯІЇЄҐа-яіїєґ]+)?)[»\"']?",
            text, re.IGNORECASE
        )
        if jk_match:
            jk_name = jk_match.group(1).strip()
            # Remove trailing common words
            jk_name = re.sub(r"\s+(є|це|має|знаходиться|розташован).*$", "", jk_name, flags=re.IGNORECASE)
            if jk_name and jk_name.lower() not in {"є", "це", "має"}:
                extracted["новобудова"] = jk_name

        return extracted

    def get_field_info(self, field_label: str) -> dict | None:
        """Get information about a specific field."""
        return self.fields.get(field_label.lower())

    def get_all_fields(self) -> list[dict]:
        """Get all field definitions from the schema."""
        return list(self.fields.values())

    def get_required_fields(self) -> list[dict]:
        """Get only required fields from the schema."""
        return [f for f in self.fields.values() if f.get("required")]

    def validate_extracted(self, extracted: dict) -> dict[str, list[str]]:
        """
        Validate extracted data against schema.

        Returns:
            Dictionary with 'missing_required' and 'invalid_options' lists.
        """
        issues = {
            "missing_required": [],
            "invalid_options": [],
        }

        # Check required fields
        for label, field_data in self.fields.items():
            if field_data.get("required") and label not in extracted:
                issues["missing_required"].append(field_data["label"])

        # Check option validity
        for label, value in extracted.items():
            if label in self.fields:
                field_data = self.fields[label]
                options = field_data.get("options", [])
                if options:
                    values = value if isinstance(value, list) else [value]
                    for v in values:
                        if v not in options and str(v) not in [str(o) for o in options]:
                            issues["invalid_options"].append(
                                f"{field_data['label']}: '{v}' not in {options}"
                            )

        return issues


if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    # Example usage
    extractor = FieldExtractor("Квартира")

    sample_text = """
    Пропонується ексклюзивна однокімнатна квартира з авторським ремонтом та повним меблюванням у сучасному житловому комплексі Бізнес-класу Creator City - символі нового рівня комфорту та стилю в серці Шевченківського району. Квартира створена для тих, хто цінує простір, естетику та технологічність. Тут продумана кожна деталь - від планування до інженерних рішень. За адресою Дегтярівська вул., 17.<br>
    <br>
    - Авторський ремонт 2026 року<br>
    - В квартирі ніхто не проживав<br>
    - Загальна площа 45 м.кв.<br>
    - Безпечний 3 поверх / 25 (з видом у двір)<br>
    - Чудовий інвестиційний варіант<br>
    <br>
    Повністю укомплектована меблями та всією необхідною технікою для життя без зайвих турбот: вбудований холодильник, індукційна плита, духова шафа, мікрохвильова піч, посудомийна машина, пральна та сушильні машини, телевізор, витяжка, бойлер. Додатково встановлені система очищення води та центральне кондиціонування, що забезпечує комфорт у будь-яку пору року.<br>
    <br>
    ЖК Creator City є концепція «місто в місті» - вся необхідна для життя інфраструктура знаходиться на території комплексу. Для безпеки майбутніх мешканців в громадських місцях встановлять камери відеоспостереження, внутрішні двори огородять парканом, а увійти в під'їзд та ліфт можна буде тільки з картою-пропуском. Для дітей різного віку розмістять кілька ігрових комплексів, для спортсменів — вуличні тренажери і футбольне поле, а родзинкою комплексу стане власний ландшафтний парк площею 2 га з водоймою. Щоб комфортному відпочинку не заважали автомобілі, забудовник передбачив підземний дворівневий паркінг з ліфтом.<br>
    <br>
    Локація - ще одна сильна сторона. Поруч зелений парк імені Івана Багряного, Київський зоопарк, метро Лук'янівська та Шулявська, КПІ, інноваційний простір Unit City, житлові комплекси Crystal Park Tower та інші знакові об'єкти району. Тут зручно жити, працювати й відпочивати.<br>
    <br>
    Це не просто нерухомість - це готовий простір для життя, куди можна заїхати з валізою і відразу відчути себе вдома.<br>
    Запрошую на перегляд, щоб ви змогли відчути цю атмосферу особисто.
    """

    result = extractor.extract(sample_text)
    print("Extracted fields:")
    for field, value in result.items():
        print(f"  {field}: {value}")

    print("\nValidation:")
    issues = extractor.validate_extracted(result)
    if issues["missing_required"]:
        print(f"  Missing required: {issues['missing_required']}")
    if issues["invalid_options"]:
        print(f"  Invalid options: {issues['invalid_options']}")
