from __future__ import annotations

import logging
from typing import List

from playwright.sync_api import Locator

from new_offer.photo_processing import prepare_photos

logger = logging.getLogger(__name__)


class PhotosMixin:
    page: any  # Playwright Page

    def _fill_photos(self, section: Locator, photo_paths: List[str]) -> None:
        """
        Загружает фотографии в блоке "Фото"
        """
        if not photo_paths:
            logger.info("No photos to upload")
            return

        logger.info("Fill photo block: %d photos", len(photo_paths))

        prepared = prepare_photos(photo_paths)

        if not prepared:
            logger.warning("No valid photos after preparation")
            return

        # input[type=file] скрыт, но Playwright умеет set_input_files
        file_input = section.locator("css=input[type='file']").first

        if file_input.count() == 0:
            logger.error("Photo file input not found")
            return

        logger.info("Uploading %d prepared photos", len(prepared))
        file_input.set_input_files(prepared)

        self.page.wait_for_timeout(500)
