from __future__ import annotations

import time
from typing import List

from playwright.sync_api import Locator

from setup_logger import setup_logger

from .photo_processing import prepare_photos

logger = setup_logger(__name__)

# UI labels for photo block controls (hardcoded — same across all property types)
_LABEL_DESCRIPTION = "Опис"
_LABEL_VIDEO_URL = "Посилання на відеотур"
_LABEL_UPLOAD_PHOTOS = "Завантажити фото"


class PhotosMixin:
    """Заповнює PhotoBlock-секції: опис, відео (тільки блок 1) та фото."""

    page: any  # Playwright Page

    # ---------- internals ----------
    def _ensure_photo_block_open(self, sec: Locator) -> None:
        """Розкриває секцію фото-блока, якщо вона згорнута."""
        if self._photo_block_content_visible(sec):
            return

        try:
            h6 = sec.locator("css=h6").first
            if h6.count():
                h6.click()
            else:
                sec.click()
        except Exception:
            try:
                sec.click()
            except Exception:
                pass

        try:
            self.page.wait_for_timeout(250)
        except Exception:
            pass

    def _photo_block_content_visible(self, sec: Locator) -> bool:
        """Перевіряє наявність кнопки завантаження/поля опису, щоб визначити, чи секцію розгорнуто."""
        upload_text = _LABEL_UPLOAD_PHOTOS
        desc_label = _LABEL_DESCRIPTION

        # 1) кнопка загрузки
        try:
            btn = sec.locator(
                f"xpath=.//button[.//span[contains(normalize-space(.), {self._xpath_literal(upload_text)})]]"
            ).first
            if btn.count() and btn.is_visible():
                return True
        except Exception:
            pass

        # 2) поле описания
        try:
            ctrl = self._find_control_by_label(sec, desc_label)
            if ctrl and ctrl.count():
                try:
                    return ctrl.is_visible()
                except Exception:
                    return True
        except Exception:
            pass

        return False

    def _fill_text_in_photo_section(
        self, sec: Locator, ui_label: str, value: str
    ) -> None:
        value = (value or "").strip()
        if not value:
            return

        ctrl = self._find_control_by_label(sec, ui_label)
        if not ctrl:
            logger.debug(
                "PhotoBlock: елемент керування не знайдено для label='%s' (пропуск)", ui_label
            )
            return

        # если это wrapper — берём input/textarea
        try:
            tag = ctrl.evaluate("el => el.tagName.toLowerCase()")
        except Exception:
            tag = ""

        if tag not in ("input", "textarea"):
            inner = ctrl.locator("css=input, textarea").first
            if inner.count():
                ctrl = inner

        # skip если уже так
        try:
            cur = (ctrl.input_value() or "").strip()
        except Exception:
            cur = ""

        if cur == value:
            try:
                self._mark_touched(ctrl)
            except Exception:
                pass
            return

        try:
            ctrl.click()
        except Exception:
            pass

        try:
            ctrl.fill(value)
        except Exception:
            try:
                ctrl.press("Control+A")
                ctrl.press("Backspace")
                ctrl.type(value, delay=15)
            except Exception:
                logger.exception("PhotoBlock: не вдалось заповнити label='%s'", ui_label)
                return

        try:
            self._mark_touched(ctrl)
        except Exception:
            pass

    def _count_photo_previews(self, sec: Locator) -> int:
        """Підраховує прев'ю завантажених фото всередині секції."""
        # Часто прев'ю — це <img src=...>, іноді background-image.
        try:
            imgs = sec.locator("css=img[src]").count()
        except Exception:
            imgs = 0

        if imgs > 0:
            return imgs

        try:
            bg = sec.locator(
                "xpath=.//*[@style and contains(@style,'background-image')]"
            ).count()
        except Exception:
            bg = 0

        return bg

    def _progress_visible(self, sec: Locator) -> bool:
        """Перевіряє наявність індикаторів прогресу/завантаження у межах секції."""
        try:
            loc = sec.locator(
                "css=[role='progressbar'], .MuiCircularProgress-root, .MuiLinearProgress-root, [aria-busy='true']"
            )
            n = loc.count()
            for i in range(n):
                try:
                    if loc.nth(i).is_visible():
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _wait_photos_uploaded(
        self,
        sec: Locator,
        before_count: int,
        expected_added: int,
        timeout_ms: int = 90000,
        stable_ms: int = 1000,
    ) -> None:
        """Чекає, поки:
        - кількість прев'ю збільшиться (бажано на expected_added)
        - зникнуть індикатори прогресу
        - стан буде стабільним протягом stable_ms

        Не падає по таймауту — лише логує warning.
        """
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        stable_since: float | None = None

        # минимально ожидаем хотя бы 1 добавленную, если expected_added неадекватен
        min_target = before_count + max(1, int(expected_added or 0))

        while time.monotonic() < deadline:
            try:
                current = self._count_photo_previews(sec)
            except Exception:
                current = before_count

            try:
                progress = self._progress_visible(sec)
            except Exception:
                progress = False

            # строгое условие: все добавились и нет прогресса
            ok_strict = (current >= min_target) and (not progress)

            # мягкое условие: есть хоть какой-то прирост и нет прогресса
            ok_soft = (current > before_count) and (not progress)

            if ok_strict or ok_soft:
                if stable_since is None:
                    stable_since = time.monotonic()
                if (time.monotonic() - stable_since) * 1000.0 >= stable_ms:
                    return
            else:
                stable_since = None

            try:
                self.page.wait_for_timeout(250)
            except Exception:
                time.sleep(0.25)

        try:
            after = self._count_photo_previews(sec)
            logger.debug("PhotoBlock: завантажено %s фото", after)
        except Exception:
            after = -1

        logger.warning(
            "PhotoBlock: таймаут очікування завантаження (before=%s, after=%s, expected_added=%s)",
            before_count,
            after,
            expected_added,
        )

    def _upload_photos_in_photo_section(
        self, sec: Locator, photo_paths: List[str]
    ) -> None:
        paths = [str(p).strip() for p in (photo_paths or []) if str(p).strip()]
        if not paths:
            return

        prepared = prepare_photos(paths)
        if not prepared:
            logger.warning("PhotoBlock: немає придатних фото після підготовки")
            return

        file_input = sec.locator("css=input[type='file']").first
        if file_input.count() == 0:
            file_input = sec.locator("xpath=.//input[@type='file']").first

        if file_input.count() == 0:
            logger.error("PhotoBlock: поле завантаження файлу не знайдено")
            return

        before = self._count_photo_previews(sec)

        logger.info(
            "PhotoBlock: завантаження %d фото (before=%d)", len(prepared), before
        )
        try:
            file_input.set_input_files(prepared)

            try:
                self._mark_touched(file_input)
            except Exception:
                pass

            # ждём, пока появятся превью и пропадут прогрессы
            self._wait_photos_uploaded(
                sec, before_count=before, expected_added=len(prepared)
            )

        except Exception:
            logger.exception("PhotoBlock: помилка завантаження фото")
