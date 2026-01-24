from __future__ import annotations

import time
from typing import List

from playwright.sync_api import Locator

from setup_logger import setup_logger

from models.choice_labels import PHOTO_BLOCK_LABELS
from models.rieltor_dataclasses import Offer, PhotoBlock

from .photo_processing import prepare_photos

logger = setup_logger(__name__)


class PhotosMixin:
    """Заполняет PhotoBlock-секции: описание, видео (только блок 1) и фото."""

    page: any  # Playwright Page

    PHOTO_BLOCK_KEYS = ("apartment", "interior", "layout", "yard", "infrastructure")

    def _fill_photos(self, root: Locator, offer: Offer) -> None:
        """Заполнить все фото-блоки, которые имеют данные в offer."""
        if offer is None:
            return

        label_description = PHOTO_BLOCK_LABELS.get("description", "Опис")
        label_video = PHOTO_BLOCK_LABELS.get("video_url", "Посилання на відеотур")

        for key in self.PHOTO_BLOCK_KEYS:
            pb = getattr(offer, key, None)
            if not isinstance(pb, PhotoBlock):
                continue

            desc = (pb.description or "").strip()
            video = (pb.video_url or "").strip()
            photos = list(pb.photos or [])

            if not (desc or video or photos):
                continue

            section_title = self._expected_label(key) or key
            sec = self._section(root, section_title)

            self._ensure_photo_block_open(sec)

            if desc:
                self._fill_text_in_photo_section(sec, label_description, desc)

            # video_url существует только в первом блоке "Блок 1 з 5: Про квартиру"
            if key == "apartment" and video:
                self._fill_text_in_photo_section(sec, label_video, video)
            elif video and key != "apartment":
                logger.debug(
                    "PhotoBlock '%s': video_url задан, но в UI есть только в первом блоке — пропускаю",
                    key,
                )

            if photos:
                self._upload_photos_in_photo_section(sec, photos)

    # ---------- internals ----------
    def _ensure_photo_block_open(self, sec: Locator) -> None:
        """Раскрывает секцию фото-блока, если она свернута."""
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
        """Проверяем по наличию кнопки загрузки/поля описания, что секция развернута."""
        upload_text = (PHOTO_BLOCK_LABELS.get("photos") or "").strip() or "Завантажити фото"
        desc_label = (PHOTO_BLOCK_LABELS.get("description") or "").strip() or "Опис"

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

    def _fill_text_in_photo_section(self, sec: Locator, ui_label: str, value: str) -> None:
        value = (value or "").strip()
        if not value:
            return

        ctrl = self._find_control_by_label(sec, ui_label)
        if not ctrl:
            logger.debug("PhotoBlock: control not found for label='%s' (skip)", ui_label)
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
                logger.exception("PhotoBlock: failed to fill label='%s'", ui_label)
                return

        try:
            self._mark_touched(ctrl)
        except Exception:
            pass

    def _count_photo_previews(self, sec: Locator) -> int:
        """Пробуем посчитать превьюшки загруженных фото внутри секции."""
        # Часто превью — это <img src=...>, иногда background-image.
        try:
            imgs = sec.locator("css=img[src]").count()
        except Exception:
            imgs = 0

        if imgs > 0:
            return imgs

        try:
            bg = sec.locator("xpath=.//*[@style and contains(@style,'background-image')]").count()
        except Exception:
            bg = 0

        return bg

    def _progress_visible(self, sec: Locator) -> bool:
        """Есть ли индикаторы прогресса/загрузки в пределах секции."""
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
        """Ждём, пока:
        - количество превью увеличится (желательно на expected_added)
        - и исчезнут индикаторы прогресса
        - и состояние будет стабильным stable_ms

        Не падаем по таймауту — только логируем warning.
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
            logger.debug("PhotoBlock: uploaded %s photos", after)
        except Exception:
            after = -1

        logger.warning(
            "PhotoBlock: upload wait timeout (before=%s, after=%s, expected_added=%s)",
            before_count,
            after,
            expected_added,
        )

    def _upload_photos_in_photo_section(self, sec: Locator, photo_paths: List[str]) -> None:
        paths = [str(p).strip() for p in (photo_paths or []) if str(p).strip()]
        if not paths:
            return

        prepared = prepare_photos(paths)
        if not prepared:
            logger.warning("PhotoBlock: no valid photos after preparation")
            return

        file_input = sec.locator("css=input[type='file']").first
        if file_input.count() == 0:
            file_input = sec.locator("xpath=.//input[@type='file']").first

        if file_input.count() == 0:
            logger.error("PhotoBlock: file input not found")
            return

        before = self._count_photo_previews(sec)

        logger.info("PhotoBlock: uploading %d photos (before=%d)", len(prepared), before)
        try:
            file_input.set_input_files(prepared)

            try:
                self._mark_touched(file_input)
            except Exception:
                pass

            # ждём, пока появятся превью и пропадут прогрессы
            self._wait_photos_uploaded(sec, before_count=before, expected_added=len(prepared))

        except Exception:
            logger.exception("PhotoBlock: failed uploading photos")
