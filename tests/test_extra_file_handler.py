"""Тест контекстного файлового хендлера extra_file_handler."""

from __future__ import annotations

import logging

from setup_logger import APP_NAME, extra_file_handler, setup_logger


def test_extra_file_handler_writes_then_cleans_up(tmp_path):
    log_path = tmp_path / "drafts_publish.log"
    base = logging.getLogger(APP_NAME)
    handlers_before = len(base.handlers)
    logger = setup_logger("test_extra_handler")

    with extra_file_handler(log_path):
        assert len(base.handlers) == handlers_before + 1
        logger.warning("повідомлення-в-окремий-лог")

    # Хендлер прибрано на виході
    assert len(base.handlers) == handlers_before
    # Повідомлення потрапило у файл
    assert "повідомлення-в-окремий-лог" in log_path.read_text(encoding="utf-8")


def test_extra_file_handler_creates_parent_dir(tmp_path):
    log_path = tmp_path / "nested" / "dir" / "drafts.log"
    with extra_file_handler(log_path):
        pass
    assert log_path.parent.is_dir()
