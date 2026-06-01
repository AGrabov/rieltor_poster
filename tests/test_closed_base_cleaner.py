"""Loop control-flow tests for ClosedBaseCleaner (no real browser)."""

from __future__ import annotations

from rieltor_handler.closed_base_cleaner import ClosedBaseCleaner


class _FakeCleaner(ClosedBaseCleaner):
    """Override browser-bound methods to simulate a shrinking list."""

    def __init__(self, initial_count: int):
        self._remaining = initial_count
        self.delete_calls = 0

    def count(self) -> int:
        return self._remaining

    def _delete_first(self) -> bool:
        if self._remaining <= 0:
            return False
        self._remaining -= 1
        self.delete_calls += 1
        return True


def test_clean_deletes_all_until_empty():
    c = _FakeCleaner(initial_count=5)
    deleted = c.clean()
    assert deleted == 5
    assert c.delete_calls == 5
    assert c.count() == 0


def test_clean_respects_max_count():
    c = _FakeCleaner(initial_count=10)
    deleted = c.clean(max_count=3)
    assert deleted == 3
    assert c.count() == 7


def test_clean_dry_run_deletes_nothing():
    c = _FakeCleaner(initial_count=4)
    would = c.clean(dry_run=True)
    assert would == 4
    assert c.delete_calls == 0
    assert c.count() == 4


class _FakePurger(ClosedBaseCleaner):
    """Simulate the «Видалені» (stage 2) permanent-purge list shrinking."""

    def __init__(self, initial_count: int):
        self._remaining = initial_count
        self.purge_calls = 0

    def count_deleted(self) -> int:
        return self._remaining

    def _purge_first(self) -> bool:
        if self._remaining <= 0:
            return False
        self._remaining -= 1
        self.purge_calls += 1
        return True


def test_purge_deleted_removes_all_until_empty():
    c = _FakePurger(initial_count=6)
    purged = c.purge_deleted()
    assert purged == 6
    assert c.purge_calls == 6
    assert c.count_deleted() == 0


def test_purge_deleted_respects_max_count():
    c = _FakePurger(initial_count=10)
    purged = c.purge_deleted(max_count=2)
    assert purged == 2
    assert c.count_deleted() == 8


def test_purge_deleted_dry_run_purges_nothing():
    c = _FakePurger(initial_count=3)
    would = c.purge_deleted(dry_run=True)
    assert would == 3
    assert c.purge_calls == 0
    assert c.count_deleted() == 3
