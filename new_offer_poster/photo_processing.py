# new_offer/photo_processing.py

from __future__ import annotations

import os
import tempfile
import shutil
import logging
from typing import List

from PIL import Image

logger = logging.getLogger(__name__)

# Требования сайта
MAX_MB = 10
MAX_BYTES = MAX_MB * 1024 * 1024
MIN_WIDTH = 1000
MIN_HEIGHT = 750
ALLOWED_EXTENSIONS = {".jpg", ".jpeg"}


def prepare_photos(paths: List[str]) -> List[str]:
    """
    Подготавливает фотографии под требования сайта:
    - jpg / jpeg
    - <= 10 MB
    - >= 1000x750

    Возвращает список ПУТЕЙ к временным файлам,
    которые можно безопасно передавать в input[type=file].
    """
    prepared: List[str] = []

    for src in paths:
        if not src:
            continue

        if not os.path.exists(src):
            logger.warning("Photo not found: %s", src)
            continue

        try:
            out = _prepare_single_photo(src)
            if out:
                prepared.append(out)
        except Exception as e:
            logger.exception("Failed to prepare photo %s: %s", src, e)

    return prepared


def _prepare_single_photo(src: str) -> str | None:
    ext = os.path.splitext(src)[1].lower()

    # Открываем изображение
    with Image.open(src) as img:
        img = img.convert("RGB")  # всегда RGB для JPEG
        width, height = img.size

        # Проверка минимального размера
        if width < MIN_WIDTH or height < MIN_HEIGHT:
            logger.info(
                "Resizing photo %s from %sx%s to minimum %sx%s",
                src, width, height, MIN_WIDTH, MIN_HEIGHT
            )
            img = _resize_to_minimum(img)

        # Готовим временный файл
        tmp_dir = tempfile.mkdtemp(prefix="rieltor_photo_")
        out_path = os.path.join(tmp_dir, _output_name(src))

        # Сохраняем с подбором качества
        _save_with_size_limit(img, out_path)

        size = os.path.getsize(out_path)
        logger.info(
            "Prepared photo: %s (%.2f MB)",
            out_path,
            size / 1024 / 1024
        )

        return out_path


def _resize_to_minimum(img: Image.Image) -> Image.Image:
    """
    Масштабирует изображение так, чтобы
    ОБЕ стороны были >= минимальных.
    """
    w, h = img.size
    scale = max(MIN_WIDTH / w, MIN_HEIGHT / h)
    new_size = (int(w * scale), int(h * scale))
    return img.resize(new_size, Image.LANCZOS)


def _save_with_size_limit(img: Image.Image, path: str) -> None:
    """
    Сохраняет JPEG, подбирая качество так,
    чтобы файл был <= MAX_BYTES.
    """
    quality = 95

    while quality >= 60:
        img.save(path, format="JPEG", quality=quality, optimize=True)

        if os.path.getsize(path) <= MAX_BYTES:
            return

        quality -= 5

    # Последняя попытка — сохраняем как есть
    img.save(path, format="JPEG", quality=60, optimize=True)
    logger.warning(
        "Photo saved with minimal quality but still large: %s",
        path
    )


def _output_name(src: str) -> str:
    base = os.path.basename(src)
    name, _ = os.path.splitext(base)
    return f"{name}.jpg"

