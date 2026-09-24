"""Коли фаза закриває добу: мітка має ставитись лише за повним проходом.

Саме тут сидів головний дефект: мітку писали за фактом «оновлено > 0», тож
частковий прогін закривав добу, а прогін, де все вже свіже, — ніколи.
"""

from __future__ import annotations

import pytest

import main as main_module
from rieltor_handler.actuality_refresher import RefreshOutcome


class _FakeDB:
    def __init__(self) -> None:
        self.skipped: list[tuple[int, str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_posted(self, max_count=None):
        return []

    def mark_skipped(self, estate_id, reason):
        self.skipped.append((estate_id, reason))


class _FakeSession:
    page = object()

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self):
        pass


@pytest.fixture
def phase(monkeypatch):
    """Фаза з підміненими БД, сесією сайту та оновлювачем дати."""
    import offer_db
    import rieltor_handler
    import rieltor_handler.rieltor_session as rs

    monkeypatch.setenv("PHONE", "0000000000")
    monkeypatch.setenv("PASSWORD", "pass")
    monkeypatch.setattr(offer_db, "OfferDB", _FakeDB)
    monkeypatch.setattr(rs, "RieltorSession", _FakeSession)

    marks: list[str] = []
    monkeypatch.setattr(main_module, "write_last_actuality_refresh", lambda: marks.append("день закрито"))

    def _with_outcome(outcome: RefreshOutcome):
        class _FakeRefresher:
            def __init__(self, page, request_delay_sec=None):
                pass

            def refresh_in_priority_order(self, max_count=None, dry_run=False, progress_cb=None):
                return outcome

        monkeypatch.setattr(rieltor_handler, "ActualityRefresher", _FakeRefresher)
        return marks

    return _with_outcome


def test_complete_pass_closes_the_day(phase):
    marks = phase(RefreshOutcome(done=1700, completed=True))
    main_module.phase_prune_stale(refresh=True, skip_crm=True)
    assert marks == ["день закрито"]


def test_pass_with_nothing_to_refresh_still_closes_the_day(phase):
    """Другий запуск за добу: оновлювати нічого, але день чесно опрацьовано."""
    marks = phase(RefreshOutcome(done=0, completed=True))
    main_module.phase_prune_stale(refresh=True, skip_crm=True)
    assert marks == ["день закрито"]


def test_interrupted_pass_does_not_close_the_day(phase):
    """Обрив (ліміт, помилка сторінки, серія невдач) лишає добу відкритою."""
    marks = phase(RefreshOutcome(done=42, completed=False))
    main_module.phase_prune_stale(refresh=True, skip_crm=True)
    assert marks == []


def test_site_limit_leaves_the_day_open(phase):
    """Ліміт тимчасовий (перевірено наживо: вночі відмова, вранці знову працює),
    тож добу лишаємо відкритою — дашборд повторить і добере наступну порцію."""
    marks = phase(RefreshOutcome(done=200, completed=False, limit_reached=True))
    main_module.phase_prune_stale(refresh=True, skip_crm=True)
    assert marks == []


def test_dry_run_never_closes_the_day(phase):
    marks = phase(RefreshOutcome(done=1700, completed=True))
    main_module.phase_prune_stale(refresh=True, skip_crm=True, dry_run=True)
    assert marks == []


def test_skip_crm_works_without_crm_credentials(phase, monkeypatch):
    """Головний сценарій для ПК поза мережею компанії — креди CRM не потрібні."""
    monkeypatch.setenv("CRM_EMAIL", "")
    monkeypatch.setenv("CRM_PASSWORD", "")
    marks = phase(RefreshOutcome(done=5, completed=True))
    result = main_module.phase_prune_stale(refresh=True, skip_crm=True)
    assert result == {"moved": 0, "refreshed": 5}
    assert marks == ["день закрито"]


def test_refresh_without_rieltor_credentials_stops_early(phase, monkeypatch):
    monkeypatch.setenv("PHONE", "")
    marks = phase(RefreshOutcome(done=5, completed=True))
    assert main_module.phase_prune_stale(refresh=True, skip_crm=True) == {"moved": 0, "refreshed": 0}
    assert marks == []


def test_skip_crm_without_refresh_is_rejected(phase):
    """Пропустити звірку й нічого не піднімати — безглуздо, краще сказати прямо."""
    marks = phase(RefreshOutcome(done=0, completed=True))
    assert main_module.phase_prune_stale(refresh=False, skip_crm=True) == {"moved": 0, "refreshed": 0}
    assert marks == []
