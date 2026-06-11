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
