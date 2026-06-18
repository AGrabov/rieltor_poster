"""Tests for RU→UA address normalization helpers."""

from __future__ import annotations

from crm_data_parser import address_normalize as an


# ── normalize_city ────────────────────────────────────────────────────────
def test_normalize_city_russian_to_ukrainian():
    assert an.normalize_city("Киев") == "Київ"
    assert an.normalize_city("Харьков") == "Харків"
    assert an.normalize_city("Одесса") == "Одеса"
    assert an.normalize_city("Львов") == "Львів"


def test_normalize_city_strips_city_prefix():
    assert an.normalize_city("г. Киев") == "Київ"
    assert an.normalize_city("город Харьков") == "Харків"
    assert an.normalize_city("м. Київ") == "Київ"


def test_normalize_city_already_ukrainian_unchanged():
    assert an.normalize_city("Київ") == "Київ"
    assert an.normalize_city("Бровари") == "Бровари"


def test_normalize_city_case_insensitive_lookup():
    assert an.normalize_city("КИЕВ") == "Київ"
    assert an.normalize_city("киев") == "Київ"


def test_normalize_city_unknown_returned_as_is():
    assert an.normalize_city("Сміла") == "Сміла"


def test_normalize_city_kherson():
    assert an.normalize_city("Херсон") == "Херсон"


# ── strip_street_type ─────────────────────────────────────────────────────
def test_strip_street_type_russian_forms():
    assert an.strip_street_type("ул. Лугова") == "Лугова"
    assert an.strip_street_type("пер. Садовий") == "Садовий"
    assert an.strip_street_type("переулок Садовый") == "Садовый"


def test_strip_street_type_ukrainian_forms():
    assert an.strip_street_type("вулиця Львівська") == "Львівська"
    assert an.strip_street_type("пров. Садовий") == "Садовий"
    assert an.strip_street_type("просп. Свободи") == "Свободи"
    assert an.strip_street_type("б-р Шевченка") == "Шевченка"


def test_strip_street_type_extra_types():
    assert an.strip_street_type("дорога Київська") == "Київська"
    assert an.strip_street_type("дор. Київська") == "Київська"
    assert an.strip_street_type("алея Героїв") == "Героїв"
    assert an.strip_street_type("ал. Героїв") == "Героїв"


def test_strip_street_type_no_prefix_unchanged():
    assert an.strip_street_type("Лугова") == "Лугова"


def test_strip_street_type_does_not_eat_name_starting_like_prefix():
    # "Шевченка" must not lose "Ше" because of "шосе"/"ш."
    assert an.strip_street_type("Шевченка") == "Шевченка"


# ── street_type_canon ─────────────────────────────────────────────────────
def test_street_type_canon_ukrainian():
    assert an.street_type_canon("вул. Шевченка") == "вул"
    assert an.street_type_canon("провулок Шевченка") == "пров"
    assert an.street_type_canon("пл. Шевченка") == "пл"
    assert an.street_type_canon("просп. Свободи") == "просп"


def test_street_type_canon_russian_maps_to_ukrainian():
    assert an.street_type_canon("ул. Шевченка") == "вул"
    assert an.street_type_canon("пер. Шевченка") == "пров"
    assert an.street_type_canon("площадь Шевченка") == "пл"


def test_street_type_canon_no_type_returns_empty():
    assert an.street_type_canon("Шевченка") == ""
    assert an.street_type_canon("Лесі Українки") == ""


def test_street_type_canon_finds_type_inside_address():
    # Works on a full registry address, not just a leading prefix.
    assert an.street_type_canon("м.Київ, Голосіївський р-н, вулиця Львівська, 19") == "вул"


# ── normalize_house ───────────────────────────────────────────────────────
def test_normalize_house_unifies_letter_variants():
    # Mirrors the site's normHouse: "20а" = "20-а" = "20 а" = "20А".
    assert (
        an.normalize_house("20а")
        == an.normalize_house("20-а")
        == an.normalize_house("20 а")
        == an.normalize_house("20А")
    )


def test_normalize_house_keeps_fraction():
    assert an.normalize_house("1-3/5") == "13/5"


def test_normalize_house_strips_budynok_prefix():
    assert an.normalize_house("Будинок 6") == "6"


def test_normalize_house_strips_korpus():
    # "корпус" both as a leading prefix and as a trailing qualifier → keep main number.
    assert an.normalize_house("корпус 5") == "5"
    assert an.normalize_house("5 корпус 2") == "5"
    assert an.normalize_house("буд. 5, корпус 2") == "5"
    assert an.normalize_house("корп. 3") == "3"


def test_normalize_house_keeps_letter_before_korpus():
    assert an.normalize_house("5а корпус 2") == "5а"


def test_normalize_house_strips_numero_sign():
    assert an.normalize_house("№5") == "5"
    assert an.normalize_house("№ 5") == "5"


def test_normalize_house_distinct_houses_differ():
    assert an.normalize_house("19") != an.normalize_house("19а")


def test_normalize_house_empty():
    assert an.normalize_house("") == ""


# ── fold_cyrillic ─────────────────────────────────────────────────────────
def test_fold_cyrillic_unifies_i_and_yi():
    # Russian и and Ukrainian і fold to the same form
    assert an.fold_cyrillic("Пушкинська") == an.fold_cyrillic("Пушкінська")


def test_fold_cyrillic_russian_only_letters():
    # э→е, ё→е fold so RU spelling compares equal to UA-ish spelling
    assert an.fold_cyrillic("эё") == an.fold_cyrillic("ее")


def test_fold_cyrillic_strips_apostrophes_and_lowercases():
    assert an.fold_cyrillic("Сом'я") == "сомя"


# ── RU→UA transliteration ─────────────────────────────────────────────────
def test_transliterate_adjective_endings():
    # -цкая → -цька; -ская → -ська.
    assert an.transliterate_ru_to_ua("Зверинецкая") == "Зверинецька"
    assert an.transliterate_ru_to_ua("Пушкинская") == "Пушкинська"


def test_ru_to_ua_variants_covers_ambiguous_ov_ending():
    # "-овская" is ambiguous: patronymic (Якубенків-ська) vs stem+ська
    # (Москов-ська). Both must be offered so verification can pick the real one.
    yak = an.ru_to_ua_variants("Якубенковская")
    assert "Якубенківська" in yak and "Якубенковська" in yak
    msk = an.ru_to_ua_variants("Московская")
    assert "Московська" in msk and "Москівська" in msk
    deg = an.ru_to_ua_variants("Дегтяревская")
    assert "Дегтярівська" in deg


def test_transliterate_masculine_and_genitive_endings():
    # -овский is ambiguous → both offered; -ой → -ий.
    assert "Кловський" in an.ru_to_ua_variants("Кловский")
    assert an.transliterate_ru_to_ua("Полевой") == "Полевий"


def test_transliterate_russian_letters_and_leading_i():
    assert an.transliterate_ru_to_ua("Ирпенская") == "Ірпенська"
    assert "Обездна" in an.ru_to_ua_variants("Объездная")  # ъ dropped, -ая→-а


def test_transliterate_multiword_preserves_order():
    assert an.transliterate_ru_to_ua("Эрнста Федора") == "Ернста Федора"


def test_looks_russian_detects_ru_streets():
    assert an.looks_russian("Якубенковская") is True
    assert an.looks_russian("Дегтяревская") is True
    assert an.looks_russian("Эрнста") is True


def test_looks_russian_skips_ukrainian():
    # Already-Ukrainian names (і/ї/є/ґ present) must not be flagged.
    assert an.looks_russian("Калинівський") is False
    assert an.looks_russian("Львівська") is False
    assert an.looks_russian("Садова") is False
    assert an.looks_russian("Лук'янівська") is False


# ── address_value_matches ─────────────────────────────────────────────────
# Guard used by the autocomplete to decide whether the value the site committed
# is actually the one we wanted — instead of accepting any value just because
# the dropdown closed (which silently published "Поділ" as "Печерський").
def test_address_value_matches_exact():
    assert an.address_value_matches("Печерський", "Печерський") is True


def test_address_value_matches_accepts_site_spelling_variants():
    # Site shows a slightly different spelling / adds the street-type word.
    assert an.address_value_matches("Болсунівська", "Болсуновська вул.") is True
    assert an.address_value_matches("Воскресенська", "Воскресенська вул.") is True


def test_address_value_matches_accepts_word_reorder():
    # CRM "Шота Руставелі" vs registry/site "Руставелі Шота вул." — same street.
    assert an.address_value_matches("Шота Руставелі", "Руставелі Шота вул.") is True


def test_address_value_matches_rejects_different_street_same_first_word():
    # The bug: "Велика Васильківська" was silently accepted as "Велика Кільцева".
    assert an.address_value_matches("Велика Васильківська", "Велика Кільцева вул.") is False


def test_address_value_matches_rejects_different_street():
    assert an.address_value_matches("Шовковична", "Шовкуненка вул.") is False


def test_address_value_matches_rejects_different_district():
    assert an.address_value_matches("Поділ", "Печерський") is False
    assert an.address_value_matches("Святошинський", "Печерський") is False


def test_address_value_matches_rejects_city_instead_of_street():
    assert an.address_value_matches("Петропавлівська Борщагівка", "м. Київ") is False


def test_address_value_matches_rejects_empty_current():
    assert an.address_value_matches("Печерський", "") is False
    assert an.address_value_matches("Печерський", "   ") is False


def test_address_value_matches_empty_desired_cannot_validate():
    assert an.address_value_matches("", "Печерський") is False
