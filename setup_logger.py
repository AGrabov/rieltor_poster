# setup_logger.py
import logging
from logging import Logger
from logging.handlers import RotatingFileHandler
from pathlib import Path
import colorlog
import os

APP_NAME = os.environ.get("APP_NAME", "")

class FlushFileHandler(RotatingFileHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

def _has_file_handler(logger: Logger, filename: str) -> bool:
    target = str(Path(filename))
    for h in logger.handlers:
        if isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", None) == target:
            return True
    return False

def _has_console_handler(logger: Logger) -> bool:
    # StreamHandler, но не FileHandler
    for h in logger.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            return True
    return False

def init_logging(level=os.environ.get("LOG_LEVEL", "INFO"), filename: str | None = None) -> Logger:
    """
    Вызывай один раз в entrypoint. Повторный вызов безопасен.
    """
    base = logging.getLogger(APP_NAME)
    log_lvl = getattr(logging, str(level).upper(), logging.INFO)
    base.setLevel(log_lvl)
    base.propagate = False  # чтобы не дублилось через root

    # ---- file handler (один общий) ----
    if filename and not _has_file_handler(base, filename):
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)

        fh = FlushFileHandler(str(path), mode="a", maxBytes=5*1024*1024, backupCount=2, encoding="utf-8")
        fh.setLevel(log_lvl)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] -%(levelname)s- %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        base.addHandler(fh)

    # ---- console handler (один общий) ----
    if not _has_console_handler(base):
        sh = colorlog.StreamHandler()
        sh.setLevel(log_lvl)
        formatter = colorlog.ColoredFormatter(
            "%(asctime_log_color)s%(asctime)s%(reset)s  "
            "%(log_color)s[%(name)s]  -%(level_log_color)s%(levelname)s-  "
            "%(reset)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
            secondary_log_colors={
                "level": {
                    "DEBUG": "bold_cyan",
                    "INFO": "bold_green",
                    "WARNING": "bold_yellow",
                    "ERROR": "bold_red",
                    "CRITICAL": "bold_red",
                },
                "message": {
                    "WARNING": "yellow",
                    "ERROR": "red",
                    "CRITICAL": "red",
                },
                "asctime": {
                    "DEBUG": "thin_white",
                    "INFO": "thin_white",
                    "WARNING": "white",
                    "ERROR": "thin_red",
                    "CRITICAL": "red",
                },
            },
        )
        sh.setFormatter(formatter)
        base.addHandler(sh)

    return base

def setup_logger(name=__name__) -> Logger:
    """
    Используй везде: logger = setup_logger(__name__)
    Не конфигурит handlers, только возвращает нужный логгер.
    """
    # Приводим к иерархии schema_collector.*
    if not name.startswith(APP_NAME):
        full_name = f"{APP_NAME}.{name}"
    else:
        full_name = name

    logger = logging.getLogger(full_name)

    # ВАЖНО: дети должны поднимать записи к APP_NAME
    logger.propagate = True

    # Уровень не ставим — пусть наследуется от APP_NAME (если у ребёнка NOTSET)
    # handlers не добавляем
    return logger
