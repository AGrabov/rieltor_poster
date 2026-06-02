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


# ── fold_cyrillic ─────────────────────────────────────────────────────────
def test_fold_cyrillic_unifies_i_and_yi():
    # Russian и and Ukrainian і fold to the same form
    assert an.fold_cyrillic("Пушкинська") == an.fold_cyrillic("Пушкінська")


def test_fold_cyrillic_russian_only_letters():
    # э→е, ё→е fold so RU spelling compares equal to UA-ish spelling
    assert an.fold_cyrillic("эё") == an.fold_cyrillic("ее")


def test_fold_cyrillic_strips_apostrophes_and_lowercases():
    assert an.fold_cyrillic("Сом'я") == "сомя"
