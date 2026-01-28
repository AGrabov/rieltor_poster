from __future__ import annotations

from typing import Any, Dict, List
import logging

from setup_logger import setup_logger
logger = setup_logger(__name__)

from playwright.sync_api import Page



class _CollectorBase:
    CREATE_URL = "https://my.rieltor.ua/offers/create"

    # Sections to exclude from navigation list output
    _NAV_EXCLUDE_FROM_LIST = {"Тип угоди"}

    # Sections to exclude from field collection (but still show in navigation):
    # - "Тип угоди" - deal type selector, not a form section
    # - "Тип нерухомості" - property type selector, not a form section
    # - "Фото, відео" - media upload section, not form fields (also causes scope issues)
    _NAV_EXCLUDE_FIELDS = {"Тип угоди", "Тип нерухомості", "Фото, відео"}

    def __init__(
        self,
        page: Page,
        *,
        ui_delay_ms: int = 350,
        radio_follow_window: int = 4,
        enable_radio_probe: bool = True,
        debug: bool = False,
    ) -> None:
        self.page = page
        self.ui_delay_ms = int(ui_delay_ms)
        self.radio_follow_window = int(radio_follow_window)
        self.enable_radio_probe = bool(enable_radio_probe)

        # Cache select options to avoid reopening listboxes repeatedly.
        # Reset per property type in select_property_type().
        self._select_options_cache: Dict[str, List[str]] = {}

        self._epoch = 0

        # self.debug = bool(debug)
        # if self.debug:
        #     lvl = logging.DEBUG

        #     # поднимаем уровень всем логгерам schema_collector.*
        #     for name, obj in logging.root.manager.loggerDict.items():
        #         if not isinstance(obj, logging.Logger):
        #             continue
        #         if name == "schema_collector" or name.startswith("schema_collector."):
        #             obj.setLevel(lvl)
        #             for h in obj.handlers:
        #                 h.setLevel(lvl)

        #     # и текущему (на всякий)
        #     logger.setLevel(lvl)
        #     for h in logger.handlers:
        #         h.setLevel(lvl)
