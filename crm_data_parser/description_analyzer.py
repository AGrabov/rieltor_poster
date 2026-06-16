from __future__ import annotations

import re

from setup_logger import setup_logger

logger = setup_logger(__name__)


# Minimum plausible building total area, m². A value below this (e.g. "0.7"
# misparsed from "180,7") is a parse error — drop it rather than trust it.
# Self-contained sanity bound: does NOT compare against CRM data (often unreliable).
_MIN_TOTAL_AREA_M2 = 5.0

# Max plausible floor / storey count. Higher values are mis-parsed areas/power
# (e.g. "площа поверху — 209 м²" → 209), not real floors.
_MAX_FLOOR = 60

# Multi-value amenity fields: checkbox groups on rieltor.ua (the schema collector
# mislabels them "select"). Several options are true at once, so the analyzer must
# return the FULL list of matches — never a single value, which would collapse the
# list (e.g. ['Школа','Супермаркет'] → 'Супермаркет').
MULTI_VALUE_LABELS = frozenset(
    {
        "поруч є",
        "у квартирі є",
        "у будинку є",
        "в кімнаті є",
        "на ділянці є",
        "працює без світла",
    }
)


def _is_multi_value(label: str) -> bool:
    return label.lower().strip() in MULTI_VALUE_LABELS


def merge_multi_value(existing, new):
    """Об'єднати значення мультизначного поля (CRM + опис) у список без дублів.

    Зберігає порядок: спершу наявні значення, потім нові. Приймає скаляр або список.
    """

    def _as_list(v):
        if v is None:
            return []
        return list(v) if isinstance(v, (list, tuple)) else [v]

    out: list = []
    for item in _as_list(existing) + _as_list(new):
        if item not in out:
            out.append(item)
    return out


# Contextual regex patterns: schema_label_lower → list of (regex, matched_value)
# These patterns look for specific phrases in description text.
CONTEXTUAL_PATTERNS: dict[str, list[tuple]] = {
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
        # "без ремонту" / "потребує ремонту" — перевіряємо ПЕРШИМ, щоб не перекрило "З ремонтом"
        (r"без\s+ремонту|потребує\s+ремонту|ремонт\s+не\s+зроблен", "Без ремонту"),
        (r"частков\w+\s+ремонт", "Частковий ремонт"),
        # "ремонтом" (орудний відмінок) — завжди "з ремонтом", не плутається з "без ремонту"
        # Також явний префікс "з ремонт", "після ремонту", авторський/якісний/... ремонт
        (
            r"(?:з\s+)?ремонтом"
            r"|після\s+ремонту"
            r"|авторськ\w+(?:\s+\w+)?\s+ремонт"
            r"|(?:якісн|дизайнерськ|сучасн|євро|гарн)\w*\s+(?:\w+\s+)?ремонт\b"
            r"|ремонт\s+(?:висок|якісн|класу|преміум|дизайнерськ|сучасн)\w*",
            "З ремонтом",
        ),
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
    "призначення": [
        (r"банківськ\w+\s+приміщенн", "Банківське приміщення"),
        (r"офісн\w+\s+приміщенн", "Офісне приміщення"),
        (
            r"приміщенн\w+\s+(?:для\s+)?надання\s+послуг|сервісн\w+\s+приміщенн",
            "Приміщення для надання послуг",
        ),
        (r"(?:складськ\w+\s+приміщенн|під\s+склад(?!\s*\w))", "Склад"),
        (r"виробнич\w+\s+приміщенн|під\s+виробництво", "Виробниче приміщення"),
        (r"вільн\w+\s+призначенн", "Приміщення вільного призначення"),
        (
            r"торгівельн\w+\s+приміщенн|торговельн\w+\s+приміщенн|магазин",
            "Торгівельне приміщення",
        ),
    ],
    "вид будівлі": [
        # Space inside an office/business center
        (
            r"(?:в|у)\s+(?:офісн\w+|бізнес[-\s]?)[-\s]?центр",
            "Приміщення в офісному центрі",
        ),
        # The whole building is an office/business center
        (
            r"(?:офісн\w+|бізнес[-\s]?)[-\s]?центр(?!\s*(?:у|в|на|до|з|із|від))",
            "Офісний центр",
        ),
        (
            r"житлов\w+\s+будинок|жк\s|житлов\w+\s+комплекс",
            "Приміщення в житловому будинку",
        ),
        (
            r"окремо\s+стояч\w+\s+будівл|окрем\w+\s+будівл|адміністративн\w+\s+будівл",
            "Окремо стояча будівля",
        ),
        (r"комплекс\s+будівель", "Комплекс будівель"),
        (r"частина\s+будівл", "Частина будівлі"),
    ],
}


class DescriptionAnalyzer:
    """Аналізує текст опису для вилучення додаткових значень полів.

    Використовує варіанти полів схеми, контекстуальні regex-патерни та пошук
    числових значень для видобування інформації з неструктурованого тексту опису.
    """

    def __init__(self, schema: list[dict], debug: bool = False):
        self.schema = schema
        self.debug = debug
        self.label_to_field = self._create_label_mapping()

    def _create_label_mapping(self) -> dict[str, dict]:
        """Створити зворотний маппінг від label_lower до інформації поля."""
        mapping = {}
        for field in self.schema:
            label = field.get("label", "").lower().strip()
            if label:
                mapping[label] = field
        return mapping

    @staticmethod
    def _preprocess_description(text: str) -> str:
        """Нормалізувати текст опису перед аналізом.

        Виправляє поширені помилки форматування в CRM-описах:
        - «мг» замість «м²» після числа: "212мгДілянка" → "212 м²Ділянка"
        - зрощені слова на межі малої/великої літери → вставляємо «. »:
          "сотокПаркування" → "соток. Паркування"
        """
        # 1) "мг" → "м²" FIRST so "212мгДілянка" → "212 м²Ділянка"
        text = re.sub(r"(\d)\s*мг\b", r"\1 м²", text)
        # 2) Insert ". " at lowercase/digit/²→uppercase boundary ONLY when the uppercase
        #    letter starts a real word (≥2 lowercase follow it).
        #    "сотокПаркування" → "соток. Паркування" ✓
        #    "кВт" (В followed by only 1 lowercase) → unchanged ✓
        #    "м²Ділянка" → "м². Ділянка" ✓ (² in first char class)
        text = re.sub(r"([а-яіїєґ\d²])([А-ЯІЇЄҐ][а-яіїєґ]{2,})", r"\1. \2", text)
        # 3) Normalize whitespace
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()

    def analyze(self, description: str, existing_data: dict) -> dict:
        """Проаналізувати текст опису та вилучити додаткові значення полів.

        Вилучає лише поля, яких ще НЕМАЄ в existing_data.
        """
        if not description:
            return {}

        extracted = {}
        description_lower = self._preprocess_description(description).lower()

        # 1) Match against field options for select/radio/checklist widgets
        option_matches = self._match_field_options(description_lower, existing_data)
        self._merge_into(extracted, option_matches)

        # 2) Contextual patterns (appliances, heating type, condition, etc.)
        context_matches = self._extract_by_context(description_lower, existing_data)
        self._merge_into(extracted, context_matches)

        # 3) Numeric patterns (areas, floor, rooms, price, year, ceiling)
        numeric_matches = self._extract_numeric_fields(description_lower, existing_data)
        self._merge_into(extracted, numeric_matches)

        # 4) Simple keyword patterns (heating boolean, hot water)
        pattern_matches = self._extract_keyword_patterns(description_lower, existing_data)
        self._merge_into(extracted, pattern_matches)

        if self.debug and extracted:
            logger.debug(f"DescriptionAnalyzer вилучив {len(extracted)} полів: {list(extracted.keys())}")

        return extracted

    @staticmethod
    def _merge_into(target: dict, new: dict) -> None:
        """Злити new у target. Мультизначні поля об'єднуються у список (union),
        решта — перезаписуються (останній етап перемагає, як було)."""
        for key, value in new.items():
            if _is_multi_value(key):
                target[key] = merge_multi_value(target.get(key), value)
            else:
                target[key] = value

    @staticmethod
    def _option_in_text(option_lower: str, text: str) -> bool:
        """Перевірити, чи варіант зустрічається як ціле слово в тексті (не як підрядок іншого слова)."""
        escaped = re.escape(option_lower)
        # For purely numeric options (e.g. "1", "2"), require digit boundaries
        # to avoid matching inside larger numbers like "#27274"
        if re.fullmatch(r"\d+", option_lower):
            pattern = r"(?<!\d)" + escaped + r"(?!\d)"
        else:
            pattern = escaped + r"(?![а-яіїєґь])"
        return bool(re.search(pattern, text))

    # Fields whose options are all single short digits — skip generic matching,
    # handled by dedicated numeric extractors in _extract_numeric_fields instead.
    _NUMERIC_ONLY_FIELDS = frozenset(
        {
            "кількість балконів",
            "кількість спален",
            "кількість санвузлів",
            "кількість кімнат",
            "число кімнат",
        }
    )

    def _match_field_options(self, text: str, existing_data: dict) -> dict:
        """Зіставити текст опису з варіантами полів.

        Для полів з варіантами (select, radio, checklist) перевіряє, чи
        зустрічається будь-який варіант у тексті опису.

        Пропускає:
        - Поля лише з Так/Ні: забагато хибних спрацьовувань через українське "так"
          як сполучник ("...так і для...").
        - Лічильні поля з суто числовими варіантами (балкони, спальні): обробляються
          спеціальними вилучувачами, що вимагають явного контексту (напр. "балкон" поряд).
        """
        extracted = {}

        for field in self.schema:
            widget = field.get("widget")
            options = field.get("options", [])

            if not options:
                continue

            key = field.get("label", "").strip()
            if not key or key in existing_data:
                continue

            is_address = field.get("section", "").lower() == "адреса об'єкта"
            if is_address:
                continue

            # Skip binary Так/Ні fields — "так" appears in normal Ukrainian sentences
            options_lower = {o.lower() for o in options}
            if options_lower <= {"так", "ні", "є", "немає"}:
                continue

            # Skip count fields with purely numeric options (digits + maybe "немає")
            if key.lower() in self._NUMERIC_ONLY_FIELDS:
                continue

            if widget in ["select", "radio", "checklist"]:
                # Варіанти-голі-числа (Поверх 1..40, Кількість телефонних ліній 1..5)
                # не можна матчити через "чи є це число в тексті" — це просто хапає
                # найбільшу цифру в описі (напр. "30" з "30 кВт"). Такі поля
                # потребують спеціальних контекстних вилучувачів, тож числові
                # варіанти тут пропускаємо.
                matches = [
                    opt
                    for opt in options
                    if not re.fullmatch(r"\d+", opt.strip()) and self._option_in_text(opt.lower(), text)
                ]
                if matches:
                    # Чеклісти та мультизначні поля → весь список; інакше — одне значення.
                    if widget == "checklist" or _is_multi_value(key):
                        extracted[key] = matches
                    else:
                        extracted[key] = matches[-1]
                    if self.debug:
                        logger.debug(f"Знайдено збіг {key}={extracted[key]} в описі (з {len(matches)} збігів)")

        return extracted

    def _extract_by_context(self, text: str, existing_data: dict) -> dict:
        """Вилучити значення за контекстуальними regex-патернами.

        Зіставляє патерни з CONTEXTUAL_PATTERNS з текстом,
        перетворюючи ключі патернів на реальні мітки схеми.
        """
        extracted = {}

        for pattern_key, patterns in CONTEXTUAL_PATTERNS.items():
            # Resolve pattern key to actual schema label
            field_info = self.label_to_field.get(pattern_key)
            if not field_info:
                continue

            schema_label = field_info["label"]
            if schema_label in existing_data:
                continue

            widget = field_info.get("widget", "")
            options = field_info.get("options", [])

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

            # checklist / multi-value fields → list of values, single select/radio → one value
            if widget == "checklist" or _is_multi_value(schema_label) or len(matches) > 1:
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
                logger.debug(f"Контекстний збіг {schema_label}={extracted[schema_label]}")

        return extracted

    def _extract_numeric_fields(self, text: str, existing_data: dict) -> dict:
        """Вилучити числові значення полів (площі, поверхи, кімнати, ціна, рік, стеля)."""
        extracted = {}

        # --- Areas ---
        area_patterns = [
            (
                # Optional word between "площа" and the number is letters-only
                # ([^\W\d_]+, not \w+) — \w+ greedily eats leading digits, so
                # "загальною площею 180,7 м²" would capture "0,7" instead of "180,7".
                r"загальн\w*\s+площ\w*(?:\s+[^\W\d_]+)?\s*[:\s\-–—]*(\d+[.,]?\d*)\s*(?:м²|м\.?\s*кв\.?|кв\.?\s*м)",
                "Загальна площа, м²",
            ),
            (r"(\d+[.,]?\d*)\s*(?:м²|м\.?\s*кв\.?)\s*загальн", "Загальна площа, м²"),
            # House: "будинок площею 200 м²" / "будинок 200 м²"
            (
                r"будинок(?:\s+\w+){0,3}\s+площ\w*\s*[:\s\-–—]*(\d+[.,]?\d*)\s*(?:м²|кв\.?\s*м)",
                "Загальна площа, м²",
            ),
            (
                r"будинок\s+(\d+[.,]?\d*)\s*(?:м²|кв\.?\s*м)",
                "Загальна площа, м²",
            ),
            # "площа квартири/будинку/приміщення — 85 м²"
            (
                r"площ\w+\s+(?:квартир|будинку|кімнат|приміщенн)\w*\s*[—–\-:]+\s*(\d+[.,]?\d*)\s*(?:м²|м\.?\s*кв\.?|кв\.?\s*м)",
                "Загальна площа, м²",
            ),
            # "таунхаус/дуплекс площею 143 м²" / "таунхаус загальною площею 120 м²"
            (
                r"(?:таунхаус|дуплекс|котедж|вілла)\s+(?:загальною\s+)?площею\s*(\d+[.,]?\d*)\s*(?:м²|м2|м\.?\s*кв\.?|кв\.?\s*м)",
                "Загальна площа, м²",
            ),
            (r"житлов\w*\s+площ\w*[:\s\-–—]*(\d+[.,]?\d*)", "Житлова площа, м²"),
            (r"площ\w*\s+кухн\w*[:\s\-–—]*(\d+[.,]?\d*)", "Площа кухні, м²"),
            # "Кухня-вітальня — 15 м²" or "Кухня — 12 м²"
            (
                r"кухня(?:-\w+)?\s*[—–\-]\s*(\d+[.,]?\d*)\s*(?:м²|м\.?\s*кв\.?|кв\.?\s*м)",
                "Площа кухні, м²",
            ),
            (
                r"кухн\w*[:\s\-–—]*(\d+[.,]?\d*)\s*(?:м²|м\.?\s*кв\.?|кв\.?\s*м)",
                "Площа кухні, м²",
            ),
        ]

        for pattern, label in area_patterns:
            if label not in existing_data and label not in extracted:
                match = re.search(pattern, text)
                if match:
                    extracted[label] = match.group(1).replace(",", ".")

        # Format: "85/50/12 м²" — unit required to avoid false matches in house descriptions
        # (addresses like "5/7", floor fractions "2/2", parking "1/2" must not trigger this)
        area_slash = re.search(
            r"(\d+)[/\\](\d+)[/\\](\d+)\s*(?:м²|м\.?\s*кв\.?|кв\.?\s*м)",
            text,
        )
        if area_slash:
            for label, group in [
                ("Загальна площа, м²", 1),
                ("Житлова площа, м²", 2),
                ("Площа кухні, м²", 3),
            ]:
                if label not in existing_data and label not in extracted:
                    extracted[label] = area_slash.group(group)

        # --- Plot area (соток / га) ---
        _PLOT_AREA_LABELS = ("Площа ділянки, соток", "Загальна площа, соток")
        plot_area_label = next(
            (lbl for lbl in _PLOT_AREA_LABELS if lbl.lower() in self.label_to_field),
            None,
        )
        if plot_area_label and plot_area_label not in existing_data and plot_area_label not in extracted:
            # (pattern, skip_on_do_vid_prefix)
            # Contextual patterns with "землі/ділянки" are reliable even with до/від prefix
            sotky_patterns = [
                (r"площ\w*\s+ділянк\w*\s*[—–\-:]*\s*(\d+[.,]?\d*)\s*соток", False),
                (r"(\d+[.,]?\d*)\s*сотк\w+\s+(?:землі|ділянк\w*)", False),
                (r"(\d+[.,]?\d*)\s*соток\b", True),
                (r"(\d+[.,]?\d*)\s*сотки\b", True),
            ]
            ha_patterns = [
                r"(\d+[.,]?\d*)\s*га\b",
            ]
            plot_value = None
            for pat, check_prefix in sotky_patterns:
                m = re.search(pat, text)
                if m:
                    if check_prefix:
                        # Skip "до N соток" / "від N соток" — vague expansion hints
                        prefix = text[max(0, m.start() - 5) : m.start()]
                        if re.search(r"\bдо\b|\bвід\b", prefix):
                            continue
                    plot_value = m.group(1).replace(",", ".")
                    break
            if plot_value is None:
                for pat in ha_patterns:
                    m = re.search(pat, text)
                    if m:
                        ha = float(m.group(1).replace(",", "."))
                        sotky = ha * 100
                        plot_value = str(int(sotky)) if sotky == int(sotky) else str(round(sotky, 2))
                        break
            if plot_value is not None:
                extracted[plot_area_label] = plot_value
                if self.debug:
                    logger.debug(f"Вилучено {plot_area_label}={plot_value}")

        # --- Floor ---
        if "Поверх" not in existing_data and "Поверх" not in extracted:
            floor = total = None
            # Pattern 0: "N/M поверх" — digit/digit BEFORE the word (the common
            # commercial format, e.g. "3/7 поверх"). Runs first so the reliable
            # description value can override a bad CRM "Поверх" cell.
            m = re.search(r"(\d+)\s*[/\\]\s*(\d+)\s*поверх(?![а-яіїєґ])", text)
            if m:
                floor, total = m.group(1), m.group(2)
            if floor is None:
                # Pattern 1: "N поверх / M" or "N поверх з M" (without ordinal suffix)
                m = re.search(r"(\d+)\s*поверх\s*[/зі]+\s*(\d+)", text)
                if m:
                    floor, total = m.group(1), m.group(2)
            if floor is None:
                # Pattern 2: "12-й поверх із 31" (ordinal suffix before поверх)
                m = re.search(r"(\d+)\s*[-–—]?\s*[а-яіїєґ]{0,3}\s+поверх\s+(?:із?|з)\s+(\d+)", text)
                if m:
                    floor, total = m.group(1), m.group(2)
            if floor is None:
                # Pattern 3: "поверх - 4" / "поверх: 4" — ціле слово "поверх", не
                # "поверху": "площа поверху — 209 м²" дало б поверх=209.
                m = re.search(r"поверх(?![а-яіїєґ])\s*[-–—:]\s*(\d+)", text)
                if m:
                    floor = m.group(1)

            # Sanity: поверх не може бути вищим за поверховість (напр. "40/7" —
            # суперечливе значення). Відкидаємо обидва, а не зберігаємо інверсію.
            if floor is not None and total is not None and int(floor) > int(total):
                floor = total = None

            # Правдоподібність: відкидаємо нереальні номери (це площа/потужність, не поверх).
            if floor is not None and 0 < int(floor) <= _MAX_FLOOR:
                extracted["Поверх"] = floor
                if (
                    total is not None
                    and 0 < int(total) <= _MAX_FLOOR
                    and "Поверховість" not in existing_data
                    and "Поверховість" not in extracted
                ):
                    extracted["Поверховість"] = total

        # --- Room count ---
        if "Число кімнат" not in existing_data and "Число кімнат" not in extracted:
            room_patterns = [
                (r"однокімнатн", "1 кімната"),
                (r"двокімнатн", "2 кімнати"),
                (r"трикімнатн|трьохкімнатн|3[-\s]?кімнатн", "3 кімнати"),
                (r"чотирикімнатн|4[-\s]?кімнатн", "4 кімнати"),
            ]
            for pattern, value in room_patterns:
                if re.search(pattern, text):
                    # Validate against schema options
                    field_info = self.label_to_field.get("число кімнат")
                    if field_info:
                        options = field_info.get("options", [])
                        if options and value not in options:
                            for opt in options:
                                if value.split()[0] in opt:
                                    value = opt
                                    break
                    extracted["Число кімнат"] = value
                    break

            # Generic N-кімнатна
            if "Число кімнат" not in extracted:
                match = re.search(r"(\d+)[-\s]?кімнатн", text)
                if match:
                    num = match.group(1)
                    room_map = {
                        "1": "1 кімната",
                        "2": "2 кімнати",
                        "3": "3 кімнати",
                        "4": "4 кімнати",
                        "5": "5 кімнат",
                        "6": "6 кімнат і більше",
                    }
                    value = room_map.get(num, f"{num} кімнат")
                    field_info = self.label_to_field.get("число кімнат")
                    if field_info:
                        options = field_info.get("options", [])
                        if options and value not in options:
                            for opt in options:
                                if num in opt:
                                    value = opt
                                    break
                    extracted["Число кімнат"] = value

        # --- Price ---
        if "Ціна" not in existing_data and "Ціна" not in extracted:
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
                        extracted["Ціна"] = price
                        if "Валюта" not in existing_data:
                            # Validate currency against schema options
                            field_info = self.label_to_field.get("валюта")
                            if field_info:
                                options = field_info.get("options", [])
                                if options:
                                    for opt in options:
                                        if currency.lower() in opt.lower():
                                            currency = opt
                                            break
                            extracted["Валюта"] = currency
                        break

        # --- Year built ---
        if "Рік будівництва" not in existing_data and "Рік будівництва" not in extracted:
            year_patterns = [
                r"(?:рік\s+)?(?:будівництва|побудови)[:\s]*(\d{4})",
                r"(?:побудован[оаи]?|збудован[оаи]?)\s*(?:в|у)?\s*(\d{4})",
                r"(\d{4})\s*рік[уа]?\s*(?:будівництва|побудови)",
                r"новобудова\s*(\d{4})",
            ]
            for pattern in year_patterns:
                match = re.search(pattern, text)
                if match:
                    year = int(match.group(1))
                    if 1800 <= year <= 2100:
                        extracted["Рік будівництва"] = str(year)
                        if self.debug:
                            logger.debug(f"Вилучено Рік будівництва={year}")
                        break

        # --- Bathroom count ---
        if "Кількість санвузлів" not in existing_data and "Кількість санвузлів" not in extracted:
            _WORD_TO_NUM = {
                "один": 1,
                "одн": 1,
                "два": 2,
                "двох": 2,
                "двома": 2,
                "три": 3,
                "трьох": 3,
                "трьома": 3,
                "чотири": 4,
                "п'ять": 5,
            }
            bathroom_count = 0
            # 1) Explicit number before санвузл: "2 санвузли", "два санвузли", "3 с/в"
            num_match = re.search(
                r"(\d+|один|одн\w*|два|двох|двома|три|трьох|трьома|чотири|п\'ять)"
                r"\s*(?:санвуз|с\.?\s*/\s*в\.?(?:\b|$))",
                text,
            )
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
                bathroom_count = len(re.findall(r"санвузо[лк]|санвузл", text))

            if bathroom_count > 0:
                field_info = self.label_to_field.get("кількість санвузлів")
                if field_info:
                    options = field_info.get("options", [])
                    if bathroom_count >= 3:
                        value = "3 і більше"
                    else:
                        value = str(bathroom_count)
                    # Validate against options
                    if options and value not in options:
                        for opt in options:
                            if str(bathroom_count) in opt:
                                value = opt
                                break
                    extracted["Кількість санвузлів"] = value

        # --- Balcony count ---
        if "Кількість балконів" not in existing_data and "Кількість балконів" not in extracted:
            balcony_match = re.search(
                r"(\d+)\s+(?:балкон|лоджі)",
                text,
            )
            if balcony_match:
                count = balcony_match.group(1)
                field_info = self.label_to_field.get("кількість балконів")
                if field_info:
                    options = field_info.get("options", [])
                    value = count if count in options else next((o for o in options if count in o), count)
                    extracted["Кількість балконів"] = value
            elif re.search(r"балкон|лоджі", text):
                # At least one balcony mentioned without an explicit count
                field_info = self.label_to_field.get("кількість балконів")
                if field_info:
                    options = field_info.get("options", [])
                    value = "1" if "1" in options else options[1] if len(options) > 1 else "1"
                    extracted["Кількість балконів"] = value

        # --- Ceiling height ---
        if "Висота стель" not in existing_data and "Висота стель" not in extracted:
            height_patterns = [
                r"висот\w*\s+стел\w*[:\s-]*(\d+(?:[.,]\d+)?)\s*м?",
                r"стелі\s*[-–—]?\s*(\d+(?:[.,]\d+)?)\s*м",
                r"(\d+(?:[.,]\d+)?)\s*м\s*стелі",
            ]
            for pattern in height_patterns:
                match = re.search(pattern, text)
                if match:
                    height = match.group(1).replace(",", ".")
                    extracted["Висота стель"] = height
                    if self.debug:
                        logger.debug(f"Вилучено Висота стель={height}")
                    break

        # --- Area sanity check ---
        # Living area + kitchen area must not exceed total area.
        # If they do, the sub-areas were likely misextracted from the description.
        _total_lbl = "Загальна площа, м²"
        _living_lbl = "Житлова площа, м²"
        _kitchen_lbl = "Площа кухні, м²"

        def _to_float(lbl: str) -> float | None:
            val = extracted.get(lbl) or existing_data.get(lbl)
            try:
                return float(str(val).replace(",", ".")) if val is not None else None
            except (ValueError, TypeError):
                return None

        # Drop an implausibly small total area (e.g. "0.7" misparsed from "180,7").
        # We only discard the value we extracted — never touch existing_data.
        if _total_lbl in extracted:
            _t = _to_float(_total_lbl)
            if _t is not None and _t < _MIN_TOTAL_AREA_M2:
                logger.warning(
                    "Площа: загальна=%.2f м² < %.1f — неправдоподібно, відкинуто",
                    _t,
                    _MIN_TOTAL_AREA_M2,
                )
                del extracted[_total_lbl]

        _total = _to_float(_total_lbl)
        _living = _to_float(_living_lbl)
        _kitchen = _to_float(_kitchen_lbl)
        if _total and (_living or _kitchen):
            _sum = (_living or 0.0) + (_kitchen or 0.0)
            if _sum > _total:
                # Both sub-areas in extracted → scale proportionally to fit total
                _both_extracted = _living_lbl in extracted and _kitchen_lbl in extracted
                if _both_extracted and _living and _kitchen:
                    _factor = _total / _sum
                    extracted[_living_lbl] = str(round(_living * _factor, 1))
                    extracted[_kitchen_lbl] = str(round(_kitchen * _factor, 1))
                    logger.warning(
                        "Площа: %.1f + %.1f > %.1f — масштабовано (k=%.3f): %.1f + %.1f",
                        _living,
                        _kitchen,
                        _total,
                        _factor,
                        float(extracted[_living_lbl]),
                        float(extracted[_kitchen_lbl]),
                    )
                else:
                    # Only one sub-area is freshly extracted — recalculate it as remainder
                    for _lbl, _val, _other in (
                        (_living_lbl, _living, _kitchen),
                        (_kitchen_lbl, _kitchen, _living),
                    ):
                        if _lbl in extracted and _val is not None:
                            _recalc = max(0.0, _total - (_other or 0.0))
                            logger.warning(
                                "Площа: %s=%.1f перевищує загальну — перераховано як %.1f",
                                _lbl,
                                _val,
                                _recalc,
                            )
                            extracted[_lbl] = str(round(_recalc, 1))

        return extracted

    def _extract_keyword_patterns(self, text: str, existing_data: dict) -> dict:
        """Вилучити прості булеві поля за ключовими словами."""
        extracted = {}

        if "Опалення" not in existing_data:
            if any(word in text for word in ["опалення", "опален", "тепло"]):
                extracted["Опалення"] = True

        if "Гаряча вода" not in existing_data:
            if any(word in text for word in ["гаряча вода", "бойлер", "водонагрівач"]):
                extracted["Гаряча вода"] = True

        return extracted
