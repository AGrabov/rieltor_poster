import logging
from logging import Logger
import colorlog
import colorlog.escape_codes

def setup_logger(name=__name__, level='INFO', **kwargs) -> Logger:
    logger = colorlog.getLogger(name)
    log_lvl = getattr(logging, level.upper())
    logger.setLevel(log_lvl)
    handler = colorlog.StreamHandler()

    # Define colors for each log level
    log_colors = {
        'DEBUG': 'cyan',
        'INFO': 'green',
        'WARNING': 'yellow',
        'ERROR': 'red',
        'CRITICAL': 'bold_red',
    }

    secondary_log_colors = {
        'name': {
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'bold_red',
        },
        'level': {
            'DEBUG': 'bold_cyan',
            'INFO': 'bold_green',
            'WARNING': 'bold_yellow',
            'ERROR': 'bold_red',
            'CRITICAL': 'bold_red',
        },
        'message': {
            'WARNING': 'yellow',
            'ERROR':    'red',
            'CRITICAL': 'red'
        },
        'asctime': {
            'DEBUG': 'thin_white',
            'INFO': 'thin_white',
            'WARNING': 'white',
            'ERROR': 'thin_red',
            'CRITICAL': 'red',
        }
    }

    formatter = colorlog.ColoredFormatter(
        "%(asctime_log_color)s%(asctime)s%(reset)s  %(log_color)s[%(name)s]  -%(level_log_color)s%(levelname)s-  %(reset)s %(message)s",
        datefmt='%Y-%m-%d %H:%M:%S',
        log_colors=log_colors, secondary_log_colors=secondary_log_colors,
        **kwargs
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger