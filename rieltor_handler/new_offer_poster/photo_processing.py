# new_offer/photo_processing.py

from __future__ import annotations

import io
import logging
import os
import tempfile
from pathlib import Path
from typing import List

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# Требования сайта
MAX_MB = 10
MAX_BYTES = MAX_MB * 1024 * 1024
MIN_WIDTH = 1000
MIN_HEIGHT = 750
ALLOWED_EXTENSIONS = {".jpg", ".jpeg"}

# Watermark
ADD_WATERMARK = os.getenv("ADD_WATERMARK", "true").lower() == "true"
WATERMARK_SVG = Path(__file__).parent.parent.parent / "thecapital_logo.svg"
WATERMARK_OPACITY = 0.5  # 0.0–1.0
WATERMARK_WIDTH_RATIO = 0.4  # ширина логотипа = 40% ширины фото

_watermark_cache: Image.Image | None = None


def _get_watermark() -> Image.Image | None:
    """
    Рендерить SVG-логотип у білий колір з прозорим фоном.
    Результат кешується у пам'яті на час сесії.
    """
    global _watermark_cache
    if _watermark_cache is not None:
        return _watermark_cache

    if not WATERMARK_SVG.exists():
        logger.warning("Watermark SVG не знайдено: %s", WATERMARK_SVG)
        return None

    try:
        from playwright.sync_api import sync_playwright

        with open(WATERMARK_SVG, encoding="utf-8") as f:
            svg = f.read()

        # Замінюємо градієнт на білий колір
        svg_white = svg.replace(
            "fill: url(#_Безымянный_градиент_5)", "fill: white"
        )
        html = (
            "<!DOCTYPE html><html style='margin:0;padding:0'>"
            "<body style='margin:0;padding:0;background:transparent;'>"
            + svg_white
            + "</body></html>"
        )

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 300, "height": 92})
            page.set_content(html)
            png_bytes = page.screenshot(omit_background=True, full_page=True)
            browser.close()

        _watermark_cache = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        logger.debug("Watermark завантажено: %s", _watermark_cache.size)
        return _watermark_cache

    except Exception:
        logger.exception("Не вдалось відрендерити watermark")
        return None


def _apply_watermark(img: Image.Image) -> Image.Image:
    """Накладає логотип по центру зображення з WATERMARK_OPACITY прозорістю."""
    wm = _get_watermark()
    if wm is None:
        return img

    # Масштабуємо watermark
    wm_w = int(img.width * WATERMARK_WIDTH_RATIO)
    wm_h = int(wm_w * wm.height / wm.width)
    wm_scaled = wm.resize((wm_w, wm_h), Image.LANCZOS)

    # Застосовуємо прозорість
    arr = np.array(wm_scaled, dtype=np.float32)
    arr[:, :, 3] *= WATERMARK_OPACITY
    wm_final = Image.fromarray(arr.astype(np.uint8), "RGBA")

    # Центруємо
    x = (img.width - wm_w) // 2
    y = (img.height - wm_h) // 2

    result = img.convert("RGBA")
    result.paste(wm_final, (x, y), wm_final)
    return result.convert("RGB")


def prepare_photos(paths: list[str]) -> list[str]:
    """
    Підготовлює фотографії відповідно до вимог сайту:
    - jpg / jpeg
    - <= 10 MB
    - >= 1000x750

    Повертає список ШЛЯХІВ до тимчасових файлів,
    які можна безпечно передавати у input[type=file].
    """
    prepared: list[str] = []

    for src in paths:
        if not src:
            continue

        if not os.path.exists(src):
            logger.warning("Фото не знайдено: %s", src)
            continue

        try:
            out = _prepare_single_photo(src)
            if out:
                prepared.append(out)
        except Exception as e:
            logger.exception("Не вдалось підготувати фото %s: %s", src, e)

    return prepared


def _prepare_single_photo(src: str) -> str | None:
    # ext = os.path.splitext(src)[1].lower()

    # Открываем изображение
    with Image.open(src) as img:
        img = img.convert("RGB")  # всегда RGB для JPEG
        width, height = img.size

        # Проверка минимального размера
        if width < MIN_WIDTH or height < MIN_HEIGHT:
            logger.info(
                "Масштабування фото %s з %sx%s до мінімуму %sx%s",
                src,
                width,
                height,
                MIN_WIDTH,
                MIN_HEIGHT,
            )
            img = _resize_to_minimum(img)

        if ADD_WATERMARK:
            img = _apply_watermark(img)

        # Готовим временный файл
        tmp_dir = tempfile.mkdtemp(prefix="rieltor_photo_")
        out_path = os.path.join(tmp_dir, _output_name(src))

        # Сохраняем с подбором качества
        _save_with_size_limit(img, out_path)

        size = os.path.getsize(out_path)
        logger.info("Фото підготовлено: %s (%.2f МБ)", out_path, size / 1024 / 1024)

        return out_path


def _resize_to_minimum(img: Image.Image) -> Image.Image:
    """
    Масштабує зображення так, щоб
    ОБИДВі сторони були >= мінімальних.
    """
    w, h = img.size
    scale = max(MIN_WIDTH / w, MIN_HEIGHT / h)
    new_size = (int(w * scale), int(h * scale))
    return img.resize(new_size, Image.LANCZOS)


def _save_with_size_limit(img: Image.Image, path: str) -> None:
    """
    Зберігає JPEG, підбираючи якість так,
    щоб файл був <= MAX_BYTES.
    """
    quality = 95

    while quality >= 60:
        img.save(path, format="JPEG", quality=quality, optimize=True)

        if os.path.getsize(path) <= MAX_BYTES:
            return

        quality -= 5

    # Последняя попытка — сохраняем как есть
    img.save(path, format="JPEG", quality=60, optimize=True)
    logger.warning("Фото збережено з мінімальною якістю, але розмір все ще великий: %s", path)


def _output_name(src: str) -> str:
    base = os.path.basename(src)
    name, _ = os.path.splitext(base)
    return f"{name}.jpg"
