"""Тести DealsMover: цикл переносу та парсер ціни з діалогу (без реального браузера)."""

from __future__ import annotations

import pytest

from rieltor_handler.deals_mover import DealsMover


class _FakeMover(DealsMover):
    """Підміняє браузерний move_to_deals записом викликів."""

    def __init__(self, fail_ids: tuple[str, ...] = ()) -> None:
        self.calls: list[str] = []
        self._fail = set(fail_ids)

    def move_to_deals(self, rieltor_offer_id: str) -> bool:
        self.calls.append(rieltor_offer_id)
        return rieltor_offer_id not in self._fail


def test_move_offers_all_success():
    m = _FakeMover()
    done = m.move_offers_to_deals(["1", "2", "3"])
    assert done == ["1", "2", "3"]
    assert m.calls == ["1", "2", "3"]


def test_move_offers_skips_failures():
    m = _FakeMover(fail_ids=("2",))
    done = m.move_offers_to_deals(["1", "2", "3"])
    assert done == ["1", "3"]
    assert m.calls == ["1", "2", "3"]


def test_move_offers_dry_run_does_nothing():
    m = _FakeMover()
    done = m.move_offers_to_deals(["1", "2"], dry_run=True)
    assert done == ["1", "2"]
    assert m.calls == []


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Записати оголошення за ціною 1166 $ в Мої угоди", 1166),
        ("Записати оголошення за ціною 400000 $ в Мої угоди", 400000),
        ("Записати оголошення за ціною 1 600 000 грн в Мої угоди", 1600000),
        ("Записати оголошення за ціною 1 600 000 ₴ в Мої угоди", 1600000),
        ("Записати оголошення в Мої угоди\nЦіна угоди", None),
        ("Записати оголошення за ціною 0 $ в Мої угоди", None),
    ],
)
def test_parse_dialog_price(text, expected):
    assert DealsMover._parse_dialog_price(text) == expected
