"""Мітка останнього оновлення дати актуальності + розбір CLI-команди refresh-actuality."""

from __future__ import annotations

import datetime as dt

import pytest

from main import (
    actuality_autostart_due,
    actuality_refresh_due,
    build_parser,
    read_actuality_autostart,
    read_actuality_heartbeat,
    read_last_actuality_attempt,
    read_last_actuality_refresh,
    write_actuality_autostart,
    write_actuality_heartbeat,
    write_last_actuality_attempt,
    write_last_actuality_refresh,
)


def test_marker_roundtrip(tmp_path):
    f = tmp_path / "last_actuality_refresh.json"
    write_last_actuality_refresh(dt.date(2026, 9, 23), path=f)
    assert read_last_actuality_refresh(path=f) == dt.date(2026, 9, 23)


def test_marker_defaults_to_today(tmp_path):
    f = tmp_path / "last_actuality_refresh.json"
    write_last_actuality_refresh(path=f)
    assert read_last_actuality_refresh(path=f) == dt.date.today()


def test_marker_missing_file_returns_none(tmp_path):
    assert read_last_actuality_refresh(path=tmp_path / "nope.json") is None


def test_marker_broken_file_returns_none(tmp_path):
    f = tmp_path / "broken.json"
    f.write_text("не json", encoding="utf-8")
    assert read_last_actuality_refresh(path=f) is None


def test_refresh_due_when_never_run():
    assert actuality_refresh_due(None, today=dt.date(2026, 9, 23)) is True


def test_refresh_due_when_last_run_was_yesterday():
    assert actuality_refresh_due(dt.date(2026, 9, 22), today=dt.date(2026, 9, 23)) is True


def test_refresh_not_due_when_already_run_today():
    assert actuality_refresh_due(dt.date(2026, 9, 23), today=dt.date(2026, 9, 23)) is False


def test_state_file_keeps_all_fields(tmp_path):
    """Запис однієї частини стану не має затирати решту (файл спільний)."""
    f = tmp_path / "actuality.json"
    write_last_actuality_refresh(dt.date(2026, 9, 23), path=f)
    write_actuality_autostart(False, path=f)
    write_last_actuality_attempt(dt.datetime(2026, 9, 23, 10, 30), path=f)

    assert read_last_actuality_refresh(path=f) == dt.date(2026, 9, 23)
    assert read_actuality_autostart(path=f) is False
    assert read_last_actuality_attempt(path=f) == dt.datetime(2026, 9, 23, 10, 30)


def test_autostart_enabled_by_default(tmp_path):
    assert read_actuality_autostart(path=tmp_path / "nope.json") is True


def test_attempt_missing_returns_none(tmp_path):
    assert read_last_actuality_attempt(path=tmp_path / "nope.json") is None


def test_autostart_due_when_never_run_and_never_attempted():
    assert actuality_autostart_due(None, None, now=dt.datetime(2026, 9, 23, 9, 0)) is True


def test_autostart_not_due_right_after_attempt():
    """Перезавантаження сторінки не повинно піднімати другий браузер."""
    assert (
        actuality_autostart_due(
            None,
            dt.datetime(2026, 9, 23, 8, 55),
            now=dt.datetime(2026, 9, 23, 9, 0),
        )
        is False
    )


def test_autostart_due_again_when_attempt_is_stale():
    """Попередній запуск міг впасти — через годину пробуємо ще раз."""
    assert (
        actuality_autostart_due(
            None,
            dt.datetime(2026, 9, 23, 7, 0),
            now=dt.datetime(2026, 9, 23, 9, 0),
        )
        is True
    )


def test_autostart_not_due_when_already_refreshed_today():
    assert (
        actuality_autostart_due(
            dt.date(2026, 9, 23),
            dt.datetime(2026, 9, 23, 7, 0),
            now=dt.datetime(2026, 9, 23, 9, 0),
        )
        is False
    )


@pytest.mark.parametrize("junk", ["[1, 2]", '"рядок"', "null", "12"])
def test_state_file_with_non_dict_json_is_ignored(tmp_path, junk):
    """Не-об'єкт у файлі не має валити дашборд при старті."""
    f = tmp_path / "actuality.json"
    f.write_text(junk, encoding="utf-8")
    assert read_last_actuality_refresh(path=f) is None
    assert read_actuality_autostart(path=f) is True
    write_actuality_autostart(False, path=f)  # не має кинути виняток
    assert read_actuality_autostart(path=f) is False


def test_state_file_created_when_directory_is_missing(tmp_path):
    f = tmp_path / "nema" / "actuality.json"
    write_last_actuality_refresh(dt.date(2026, 9, 24), path=f)
    assert read_last_actuality_refresh(path=f) == dt.date(2026, 9, 24)


@pytest.mark.parametrize("value", [{"date": "не дата"}, {"date": None}, {"date": 20260924}])
def test_marker_ignores_broken_date_values(tmp_path, value):
    import json

    f = tmp_path / "actuality.json"
    f.write_text(json.dumps(value), encoding="utf-8")
    assert read_last_actuality_refresh(path=f) is None


# ── heartbeat: лок на час прогону ────────────────────────────────────


def test_heartbeat_roundtrip(tmp_path):
    f = tmp_path / "actuality.json"
    write_actuality_heartbeat(dt.datetime(2026, 9, 24, 10, 0), path=f)
    assert read_actuality_heartbeat(path=f) == dt.datetime(2026, 9, 24, 10, 0)


def test_heartbeat_missing_returns_none(tmp_path):
    assert read_actuality_heartbeat(path=tmp_path / "nope.json") is None


def test_autostart_blocked_while_a_run_is_alive():
    """Прогін триває годинами: свіжий heartbeat має тримати другий браузер.

    Саме тут ламався старий захист — вікно спроби (година) коротше за прогін.
    """
    assert (
        actuality_autostart_due(
            None,
            dt.datetime(2026, 9, 24, 8, 0),  # спроба була давно
            heartbeat=dt.datetime(2026, 9, 24, 10, 55),  # але прогін живий
            now=dt.datetime(2026, 9, 24, 11, 0),
        )
        is False
    )


def test_autostart_allowed_when_heartbeat_is_stale():
    """Процес упав, heartbeat застиг — можна пробувати знову."""
    assert (
        actuality_autostart_due(
            None,
            dt.datetime(2026, 9, 24, 8, 0),
            heartbeat=dt.datetime(2026, 9, 24, 9, 0),
            now=dt.datetime(2026, 9, 24, 11, 0),
        )
        is True
    )


def test_cli_refresh_actuality_defaults():
    args = build_parser().parse_args(["refresh-actuality"])
    assert args.command == "refresh-actuality"
    assert args.dry_run is False
    assert args.skip_crm is False
    assert args.max_count is None


def test_cli_refresh_actuality_flags():
    args = build_parser().parse_args(["refresh-actuality", "--dry-run", "--skip-crm", "--max-count", "5"])
    assert args.dry_run is True
    assert args.skip_crm is True
    assert args.max_count == 5


def test_cli_prune_stale_has_refresh_flag():
    assert build_parser().parse_args(["prune-stale"]).refresh is False
    assert build_parser().parse_args(["prune-stale", "--refresh"]).refresh is True


def _run_cli(monkeypatch, argv: list[str]) -> dict:
    """Запустити main() із підміненою фазою і повернути її аргументи."""
    import main as main_module

    captured: dict = {}

    def _fake_phase(**kwargs):
        captured.update(kwargs)
        return {"moved": 0, "refreshed": 0}

    monkeypatch.setattr(main_module, "phase_prune_stale", _fake_phase)
    monkeypatch.setattr(main_module.sys, "argv", ["main.py", *argv])
    main_module.main()
    return captured


def test_cli_refresh_actuality_asks_for_refresh(monkeypatch):
    """Найтонша нитка: без refresh=True команда мовчки не робила б нічого."""
    got = _run_cli(monkeypatch, ["refresh-actuality", "--skip-crm", "--max-count", "5"])
    assert got["refresh"] is True
    assert got["skip_crm"] is True
    assert got["max_refresh"] == 5


def test_cli_prune_stale_max_count_does_not_limit_the_refresh(monkeypatch):
    """--max-count у prune-stale обмежує угоди, а не підняття дати."""
    got = _run_cli(monkeypatch, ["prune-stale", "--refresh", "--max-count", "10"])
    assert got["max_count"] == 10
    assert got.get("max_refresh") is None
    assert got["refresh"] is True
