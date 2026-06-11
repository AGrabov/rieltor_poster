"""Pure-logic tests for DraftsPublisher (no real browser)."""

from __future__ import annotations

import datetime as dt

from rieltor_handler.drafts_publisher import DraftRow, DraftsPublisher


def _pub() -> DraftsPublisher:
    # bypass __init__ (it expects a Playwright Page) for pure-logic tests
    return DraftsPublisher.__new__(DraftsPublisher)


def test_in_date_range_no_filter_accepts_all():
    p = _pub()
    assert p._in_date_range(None, None, None) is True
    assert p._in_date_range(dt.date(2026, 1, 1), None, None) is True


def test_in_date_range_unknown_date_rejected_when_filter_set():
    p = _pub()
    assert p._in_date_range(None, dt.date(2026, 1, 1), None) is False


def test_in_date_range_bounds_inclusive():
    p = _pub()
    d_from, d_to = dt.date(2026, 1, 10), dt.date(2026, 1, 20)
    assert p._in_date_range(dt.date(2026, 1, 10), d_from, d_to) is True
    assert p._in_date_range(dt.date(2026, 1, 20), d_from, d_to) is True
    assert p._in_date_range(dt.date(2026, 1, 9), d_from, d_to) is False
    assert p._in_date_range(dt.date(2026, 1, 21), d_from, d_to) is False


def test_select_next_skips_processed_and_out_of_range():
    p = _pub()
    rows = [
        DraftRow(key="a", date=dt.date(2026, 1, 1)),   # out of range
        DraftRow(key="b", date=dt.date(2026, 1, 15)),  # processed
        DraftRow(key="c", date=dt.date(2026, 1, 16)),  # <-- expected
    ]
    target = p._select_next(rows, processed={"b"},
                            date_from=dt.date(2026, 1, 10), date_to=None)
    assert target is not None and target.key == "c"


def test_select_next_returns_none_when_nothing_matches():
    p = _pub()
    rows = [DraftRow(key="a", date=dt.date(2026, 1, 1))]
    assert p._select_next(rows, processed={"a"}, date_from=None, date_to=None) is None


class _FakePublisher(DraftsPublisher):
    """Симулює список чернеток: публікація прибирає рядок зі списку.

    fail_keys — ключі, що "не публікуються" (валідація): лишаються в списку,
    але мають бути пропущені (не зациклитись).
    """

    def __init__(self, rows: list[DraftRow], fail_keys: set[str] | None = None):
        self._rows = list(rows)
        self._fail = fail_keys or set()
        self.published: list[str] = []
        self.sleeps: list[float] = []

    def count(self) -> int:
        return len(self._rows)

    def _collect_rows(self) -> list[DraftRow]:
        return list(self._rows)

    def _publish_row(self, key: str) -> bool:
        if key in self._fail:
            return False
        self._rows = [r for r in self._rows if r.key != key]
        self.published.append(key)
        return True

    def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def _rows(n: int) -> list[DraftRow]:
    base = dt.date(2026, 1, 1)
    return [DraftRow(key=str(i), date=base + dt.timedelta(days=i)) for i in range(n)]


def test_publish_drafts_publishes_all():
    p = _FakePublisher(_rows(4))
    assert p.publish_drafts(delay_sec=2.0) == 4
    assert p.published == ["0", "1", "2", "3"]
    assert p.sleeps == [2.0, 2.0, 2.0, 2.0]


def test_publish_drafts_respects_max_count():
    p = _FakePublisher(_rows(10))
    assert p.publish_drafts(max_count=3) == 3
    assert p.published == ["0", "1", "2"]


def test_publish_drafts_skips_failed_without_looping():
    p = _FakePublisher(_rows(3), fail_keys={"1"})
    assert p.publish_drafts() == 2          # 0 and 2 succeed, 1 is skipped
    assert p.published == ["0", "2"]


def test_publish_drafts_filters_by_date():
    p = _FakePublisher(_rows(5))            # dates 2026-01-01 .. 2026-01-05
    published = p.publish_drafts(date_from=dt.date(2026, 1, 3), date_to=dt.date(2026, 1, 4))
    assert published == 2
    assert p.published == ["2", "3"]        # keys for 01-03 and 01-04


def test_publish_drafts_dry_run_publishes_nothing():
    p = _FakePublisher(_rows(4))
    would = p.publish_drafts(dry_run=True)
    assert would == 4
    assert p.published == []
    assert p.sleeps == []
