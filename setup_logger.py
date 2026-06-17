# setup_logger.py
import contextlib
import logging
import os
from logging import Logger
from logging.handlers import RotatingFileHandler
from pathlib import Path

import colorlog

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


def init_logging(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    filename: str | None = None,
    clear_on_start: bool = False,
) -> Logger:
    """
    Викликати один раз у точці входу. Повторний виклик безпечний.

    Args:
        clear_on_start: якщо True — очищає лог-файл та бекапи перед запуском.
    """
    base = logging.getLogger(APP_NAME)
    log_lvl = getattr(logging, str(level).upper(), logging.INFO)
    base.setLevel(log_lvl)
    base.propagate = False  # чтобы не дублилось через root

    # ---- file handler (один общий) ----
    if filename and not _has_file_handler(base, filename):
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)

        if clear_on_start:
            # Truncate the main log and remove backup files (.1, .2, ...)
            try:
                path.write_text("", encoding="utf-8")
            except OSError:
                pass
            for i in range(1, 10):
                backup = path.with_suffix(path.suffix + f".{i}")
                try:
                    backup.unlink()
                except FileNotFoundError:
                    break
                except OSError:
                    pass

        fh = FlushFileHandler(
            str(path),
            mode="a",
            maxBytes=5 * 1024 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
        fh.setLevel(log_lvl)
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(name)s] -%(levelname)s- %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
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


@contextlib.contextmanager
def extra_file_handler(filename, level: str | None = None):
    """Тимчасово дублювати логи у окремий файл на час контексту.

    Усе, що логується в межах ``with`` (будь-яким логером ієрархії APP_NAME),
    додатково пишеться у ``filename`` — на додачу до загального лог-файлу.
    Хендлер прибирається на виході. Зручно для збереження логів окремої
    команди (напр. publish-drafts) у власний файл.

    Args:
        filename: шлях до окремого лог-файлу (батьківські теки створюються).
        level:    рівень для цього файлу (за замовчуванням — рівень базового логера).
    """
    base = logging.getLogger(APP_NAME)
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    log_lvl = getattr(logging, str(level).upper(), base.level) if level else base.level

    fh = FlushFileHandler(
        str(path),
        mode="a",
        maxBytes=5 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    fh.setLevel(log_lvl)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(name)s] -%(levelname)s- %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    base.addHandler(fh)
    try:
        yield path
    finally:
        base.removeHandler(fh)
        fh.close()


def setup_logger(name=__name__) -> Logger:
    """
    Використовуй скрізь: logger = setup_logger(__name__)
    Не конфігурує handlers, лише повертає потрібний логер.
    """
    # Приводим к иерархии APP_NAME.* (напр. rieltor.*), щоб усі логери проєкту
    # піднімали записи до базового логера, де висять file/console-хендлери.
    #
    # ВАЖЛИВО: перевіряємо межу по крапці, а не голий префікс. Інакше пакет
    # на кшталт ``rieltor_handler`` (починається з рядка "rieltor", але це
    # ОКРЕМА гілка логерів) лишився б поза ієрархією ``rieltor`` і його логи
    # ніколи не потрапляли б у файл/консоль.
    if APP_NAME and name != APP_NAME and not name.startswith(f"{APP_NAME}."):
        full_name = f"{APP_NAME}.{name}"
    else:
        full_name = name

    logger = logging.getLogger(full_name)

    # ВАЖНО: дети должны поднимать записи к APP_NAME
    logger.propagate = True

    # Уровень не ставим — пусть наследуется от APP_NAME (если у ребёнка NOTSET)
    # handlers не добавляем
    return logger
