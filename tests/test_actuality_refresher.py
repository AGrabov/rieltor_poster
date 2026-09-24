"""Тести ActualityRefresher: лічильник, свіжість дати й цикл оновлення (без браузера)."""

from __future__ import annotations

import pytest

from rieltor_handler.actuality_refresher import (
    ActualityPageError,
    ActualityRefresher,
    RefreshOutcome,
)


class _FakeRefresher(ActualityRefresher):
    """Емулює сайт із сортуванням «найстаріші зверху».

    `_page_keys` віддає верхівку черги ще не оновлених рядків — саме так
    поводиться перша сторінка з `sort=updatedAt`: оновлений рядок стає «Вчора»
    і їде вниз, а на його місце підтягується наступний старий.
    """

    PAGE_LIMIT = 3  # маленька «сторінка», щоб бачити перечитування

    def __init__(
        self,
        keys: list[str],
        total: int | None = None,
        fail: tuple[str, ...] = (),
        page_error_after: int | None = None,
    ) -> None:
        self._keys = list(keys)
        self._total = len(keys) if total is None else total
        self._fail = set(fail)
        self._page_error_after = page_error_after
        self.refreshed: list[str] = []
        self.page_reads: list[int] = []

    def count(self, oper_type: int | None = None) -> int:
        return self._total

    MAX_PAGE_READS = 50  # запобіжник: регресія має впасти, а не зависнути

    def _page_keys(self, page_no: int = 1, oper_type: int | None = None) -> list[str]:
        if self._page_error_after is not None and len(self.page_reads) >= self._page_error_after:
            raise ActualityPageError("таблиця не відрендерилась")
        if len(self.page_reads) >= self.MAX_PAGE_READS:
            raise AssertionError("цикл перечитує сторінку без кінця — схоже на зациклення")
        self.page_reads.append(page_no)
        pending = [k for k in self._keys if k not in self.refreshed]
        start = (page_no - 1) * self.PAGE_LIMIT
        return pending[start : start + self.PAGE_LIMIT]

    def _refresh_row(self, key: str) -> bool:
        if key in self._fail:
            return False
        self.refreshed.append(key)
        return True


def _plain() -> ActualityRefresher:
    # обходимо __init__ (він чекає Playwright Page) для чистої логіки
    return ActualityRefresher.__new__(ActualityRefresher)


@pytest.mark.parametrize(
    "total,expected",
    [
        (0, []),
        (1, [1]),
        (200, [1]),
        (201, [1, 2]),
        (1744, [1, 2, 3, 4, 5, 6, 7, 8, 9]),
    ],
)
def test_page_plan(total, expected):
    assert _plain()._page_plan(total, limit=200) == expected


def test_default_pause_is_gentle_enough():
    """Сайт обмежує темп, тож пауза між запитами — не декоративна."""
    assert ActualityRefresher.REQUEST_DELAY_SEC >= 2.0


def test_pause_can_be_tuned_per_run():
    r = ActualityRefresher.__new__(ActualityRefresher)
    r.__init__(page=None, request_delay_sec=5.0)
    assert r.request_delay_sec == 5.0
    r2 = ActualityRefresher.__new__(ActualityRefresher)
    r2.__init__(page=None)
    assert r2.request_delay_sec == ActualityRefresher.REQUEST_DELAY_SEC


def test_page_limit_matches_site_maximum():
    """Сайт не віддає більше 200 рядків на сторінку — план сторінок на цьому тримається."""
    assert ActualityRefresher.PAGE_LIMIT == 200


@pytest.mark.parametrize(
    "text,expected",
    [
        ("На сторінці 25 1 - 25 з 1744", 1744),
        ("Рядків: 200  1–200 of 201", 201),
        ("1 - 25 з понад 1744", 1744),
        ("На сторінці", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_total(text, expected):
    assert ActualityRefresher._parse_total(text) == expected


@pytest.mark.parametrize(
    "cell_text,fresh",
    [
        ("Сьог.", True),
        ("сьогодні", True),
        # Підтвердження ставить саме «Вчора» (перевірено наживо), тож такі рядки
        # вже оброблені — повторний клік сайт приймає, але дату не рухає.
        ("Вчора", True),
        ("вчора", True),
        ("22.09", False),
        ("12.09.26", False),
        ("", False),
        (None, False),
    ],
)
def test_is_fresh(cell_text, fresh):
    """Свіжими вважаємо «Сьог.» і «Вчора» — їх оновлювати нема сенсу."""
    assert ActualityRefresher._is_fresh(cell_text) is fresh


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (200, '{"data":{"13065703":"ok"},"status":"OK"}', True),
        (200, '{"data":{"13065703":"error"},"status":"OK"}', False),
        (200, '{"data":{"99999":"ok"},"status":"OK"}', False),  # відповідь про інше оголошення
        (200, "не json", False),
        (403, '{"data":{"13065703":"ok"},"status":"OK"}', False),
        (500, "", False),
    ],
)
def test_api_ok(status, body, expected):
    """Успіхом вважаємо лише «ok» саме для запитаного ID."""
    assert ActualityRefresher._api_ok(status, body, "13065703") is expected


def test_api_ok_compares_id_as_text():
    """ID із таблиці — рядок, у JSON ключ теж рядок: порівняння має бути стійким."""
    assert ActualityRefresher._api_ok(200, '{"data":{"123":"ok"}}', 123) is True


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (200, '{"data":{"13065703":"ok"},"status":"OK"}', "ok"),
        # Сайт має денний ліміт на підняття дати (перевірено наживо: після ~200
        # оновлень усі запити повертають саме це).
        (200, '{"error":"refresh_update_limit","status":"ERROR"}', "limit"),
        (200, '{"data":{"13065703":"error"},"status":"OK"}', "fail"),
        (500, "", "fail"),
    ],
)
def test_api_result(status, body, expected):
    assert ActualityRefresher._api_result(status, body, "13065703") == expected


class _LimitedRefresher(ActualityRefresher):
    """Сайт віддає «ok» перші `allowed` разів, далі — ліміт."""

    def __init__(self, keys: list[str], allowed: int) -> None:
        self._keys = keys
        self._allowed = allowed
        self.refreshed: list[str] = []
        self.clicks: list[str] = []

    def count(self, oper_type: int | None = None) -> int:
        return len(self._keys)

    def _page_keys(self, page_no: int = 1, oper_type: int | None = None) -> list[str]:
        return [k for k in self._keys if k not in self.refreshed]

    def _refresh_via_api(self, key: str) -> str:
        if len(self.refreshed) >= self._allowed:
            return "limit"
        self.refreshed.append(key)
        return "ok"

    def _refresh_via_click(self, key: str) -> bool:
        self.clicks.append(key)
        return True


def test_refresh_all_stops_on_the_site_limit():
    """Далі сайт усе одно відмовить — немає сенсу обходити решту бази."""
    r = _LimitedRefresher(["a", "b", "c", "d", "e"], allowed=2)
    outcome = r.refresh_all()
    assert outcome.done == 2
    assert outcome.limit_reached is True
    assert outcome.completed is False


def test_limit_never_falls_back_to_clicking():
    """Клік при ліміті лише вдає успіх: діалог підтверджується, дата не рухається."""
    r = _LimitedRefresher(["a", "b"], allowed=0)
    r.refresh_all()
    assert r.clicks == []


# ── черга за строками зняття: спершу оренда ──────────────────────────


class _PriorityRefresher(ActualityRefresher):
    """Записує, якими фільтрами й з яким бюджетом ходив `refresh_all`."""

    def __init__(self, outcomes: list[RefreshOutcome]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[int | None, int | None]] = []

    def refresh_all(self, max_count=None, dry_run=False, progress_cb=None, oper_type=None):
        self.calls.append((oper_type, max_count))
        return self._outcomes.pop(0)


def test_priority_order_takes_rent_first():
    """Оренду знімають через 30 днів, продаж живе довше — тож оренда йде першою."""
    r = _PriorityRefresher([RefreshOutcome(20, True), RefreshOutcome(30, True)])
    outcome = r.refresh_in_priority_order()
    assert [c[0] for c in r.calls] == [ActualityRefresher.OPER_RENT, None]
    assert outcome.done == 50
    assert outcome.completed is True


def test_priority_order_shares_one_budget():
    """--max-count — це спільний бюджет на обидва проходи, а не на кожен."""
    r = _PriorityRefresher([RefreshOutcome(4, False), RefreshOutcome(6, False)])
    r.refresh_in_priority_order(max_count=10)
    assert r.calls == [(ActualityRefresher.OPER_RENT, 10), (None, 6)]


def test_priority_order_stops_when_the_site_limit_hits():
    """Ліміт вичерпано на оренді — продаж сьогодні вже не візьмеш."""
    r = _PriorityRefresher([RefreshOutcome(200, False, limit_reached=True)])
    outcome = r.refresh_in_priority_order()
    assert len(r.calls) == 1
    assert outcome.limit_reached is True
    assert outcome.done == 200


def test_priority_order_is_incomplete_if_any_pass_is():
    r = _PriorityRefresher([RefreshOutcome(5, True), RefreshOutcome(5, False)])
    assert r.refresh_in_priority_order().completed is False


def test_priority_order_skips_second_pass_when_budget_is_spent():
    r = _PriorityRefresher([RefreshOutcome(10, False)])
    outcome = r.refresh_in_priority_order(max_count=10)
    assert len(r.calls) == 1
    assert outcome.done == 10


# ── розбір рядків сторінки ───────────────────────────────────────────


def test_keys_from_pairs_picks_only_stale_rows():
    pairs = [
        ("/offers/edit/111", "22.09"),
        ("/offers/edit/222", "Сьог."),
        ("/offers/edit/333", "Вчора"),
        ("/offers/edit/444", "04.09"),
    ]
    keys, fresh, unknown = _plain()._keys_from_pairs(pairs)
    assert keys == ["111", "444"]
    assert (fresh, unknown) == (2, 0)


def test_keys_from_pairs_counts_rows_without_icon_cell():
    """Порожній текст комірки = іконку не знайдено (протух svg-шлях) — це треба бачити."""
    pairs = [("/offers/edit/111", ""), ("/offers/edit/222", "22.09")]
    keys, fresh, unknown = _plain()._keys_from_pairs(pairs)
    assert keys == ["222"]
    assert (fresh, unknown) == (0, 1)


def test_keys_from_pairs_skips_rows_without_offer_id():
    pairs = [("", "22.09"), (None, "22.09"), ("/offers/edit/333", "22.09")]
    keys, _, _ = _plain()._keys_from_pairs(pairs)
    assert keys == ["333"]


# ── шлях оновлення: запит, фолбек на клік ────────────────────────────


class _ApiOrClickRefresher(ActualityRefresher):
    """Рахує, яким шляхом пішло оновлення кожного рядка."""

    def __init__(self, api_fails: tuple[str, ...] = (), click_fails: tuple[str, ...] = ()) -> None:
        self._api_fails = set(api_fails)
        self._click_fails = set(click_fails)
        self.api_calls: list[str] = []
        self.click_calls: list[str] = []

    def _refresh_via_api(self, key: str) -> str:
        self.api_calls.append(key)
        return "fail" if key in self._api_fails else "ok"

    def _refresh_via_click(self, key: str) -> bool:
        self.click_calls.append(key)
        return key not in self._click_fails


def test_refresh_row_uses_api_and_skips_the_browser_click():
    r = _ApiOrClickRefresher()
    assert r._refresh_row("a") is True
    assert r.api_calls == ["a"]
    assert r.click_calls == []


def test_refresh_row_falls_back_to_click_when_api_fails():
    r = _ApiOrClickRefresher(api_fails=("a",))
    assert r._refresh_row("a") is True
    assert r.click_calls == ["a"]


def test_refresh_row_reports_failure_when_both_paths_fail():
    r = _ApiOrClickRefresher(api_fails=("a",), click_fails=("a",))
    assert r._refresh_row("a") is False


# ── цикл ─────────────────────────────────────────────────────────────


def test_refresh_all_processes_every_stale_row_from_the_first_page():
    """Сторінку перечитуємо, доки сортування підтягує нові старі рядки."""
    r = _FakeRefresher(["a", "b", "c", "d", "e"])
    outcome = r.refresh_all()
    assert outcome.done == 5
    assert outcome.completed is True
    assert r.refreshed == ["a", "b", "c", "d", "e"]
    assert set(r.page_reads) == {1}  # завжди перша сторінка
    assert len(r.page_reads) >= 2


def test_refresh_all_returns_zero_when_nothing_published():
    r = _FakeRefresher([], total=0)
    outcome = r.refresh_all()
    assert (outcome.done, outcome.completed) == (0, True)
    assert r.page_reads == []


def test_refresh_all_is_complete_when_every_row_is_already_fresh():
    """Найчастіший стан другого запуску за добу: оновлювати нічого, але день закрито."""
    r = _FakeRefresher([], total=50)
    outcome = r.refresh_all()
    assert (outcome.done, outcome.completed) == (0, True)
    assert r.page_reads == [1]


def test_refresh_all_does_not_loop_on_rows_it_failed_to_refresh():
    r = _FakeRefresher(["a", "b"], fail=("b",))
    outcome = r.refresh_all()
    assert outcome.done == 1
    assert r.refreshed == ["a"]


def test_refresh_all_gives_up_after_a_streak_of_failures():
    """Протухла сесія: не можна годину лупити таймаутами по всій базі."""
    keys = [str(i) for i in range(50)]
    r = _FakeRefresher(keys, fail=tuple(keys))
    r.PAGE_LIMIT = 50  # ціла сторінка рядків, як на сайті
    outcome = r.refresh_all()
    assert outcome.completed is False
    assert len(r.refreshed) == 0
    assert r.page_reads  # спробував, але зупинився


def test_refresh_all_reports_incomplete_when_page_fails_to_render():
    """Помилка рендеру не має виглядати як «усе зроблено»."""
    r = _FakeRefresher(["a", "b", "c", "d"], page_error_after=1)
    outcome = r.refresh_all()
    assert outcome.completed is False
    assert outcome.done == 3  # перша сторінка встигла


def test_refresh_all_respects_max_count():
    r = _FakeRefresher(["a", "b", "c", "d"])
    outcome = r.refresh_all(max_count=2)
    assert outcome.done == 2
    assert r.refreshed == ["a", "b"]


def test_refresh_all_with_max_count_is_never_a_complete_pass():
    """Частковий прогін не має закривати добу в дашборді."""
    r = _FakeRefresher(["a", "b", "c", "d"])
    assert r.refresh_all(max_count=2).completed is False


def test_refresh_all_max_count_zero_does_nothing():
    """0 — це «нічого не роби», а не «без ліміту»."""
    r = _FakeRefresher(["a", "b"])
    outcome = r.refresh_all(max_count=0)
    assert outcome.done == 0
    assert r.refreshed == []
    assert r.page_reads == []


def test_refresh_all_reports_progress():
    """Дашборд тримає лок, доки прогін живий, — для цього потрібен зворотний виклик."""
    seen: list[int] = []
    r = _FakeRefresher(["a", "b", "c"])
    r.refresh_all(progress_cb=seen.append)
    assert seen == [1, 2, 3]


def test_refresh_all_dry_run_counts_stale_rows_across_pages():
    """Без кліків рядки не «їдуть» униз, тож рахуємо їх посторінково."""
    r = _FakeRefresher(["a", "b", "c", "d"], total=7)
    outcome = r.refresh_all(dry_run=True)
    assert outcome.done == 4
    assert r.refreshed == []
    assert r.page_reads == [1, 2, 3]


def test_refresh_all_dry_run_respects_max_count():
    r = _FakeRefresher(["a", "b", "c", "d"], total=400)
    outcome = r.refresh_all(max_count=2, dry_run=True)
    assert outcome.done == 2
    assert r.refreshed == []
