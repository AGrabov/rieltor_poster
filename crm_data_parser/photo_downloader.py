"""Утиліти для завантаження та очищення фотографій.

Завантажує фотографії об'єктів з CRM через автентифіковану Playwright-сесію
та зберігає їх локально для подальшого завантаження на Rieltor.
"""

from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import List

from playwright.sync_api import Page

from setup_logger import setup_logger

logger = setup_logger(__name__)

PICS_DIR = Path(__file__).parent / "offers" / "pics"
CRM_BASE = "https://crm-primes.realtsoft.net"


def download_estate_photos(
    page: Page,
    photo_urls: list[str],
    article: str,
) -> list[str]:
    """Завантажити фотографії з CRM через автентифіковану Playwright-сесію.

    Args:
        page: Playwright Page з активною CRM-сесією (для cookies).
        photo_urls: Список URL фотографій CRM (відносних або абсолютних).
        article: Номер артикула об'єкта, використовується як назва підпапки.

    Returns:
        Список локальних шляхів до успішно завантажених фотографій.
    """
    if not photo_urls:
        return []

    dest_dir = PICS_DIR / str(article)
    dest_dir.mkdir(parents=True, exist_ok=True)

    local_paths: list[str] = []
    for i, url in enumerate(photo_urls):
        full_url = url if url.startswith("http") else f"{CRM_BASE}{url}"
        try:
            response = page.context.request.get(full_url)
            if response.ok:
                ext = _guess_extension(url, response.headers.get("content-type", ""))
                filename = f"photo_{i:03d}{ext}"
                filepath = dest_dir / filename
                filepath.write_bytes(response.body())
                local_paths.append(str(filepath))
                logger.debug("Завантажено фото %d/%d: %s", i + 1, len(photo_urls), filename)
            else:
                logger.warning("Не вдалося завантажити %s: HTTP %d", full_url, response.status)
        except Exception:
            logger.exception("Помилка завантаження фото: %s", full_url)

    logger.info(
        "Завантажено %d/%d фотографій для артикула %s",
        len(local_paths),
        len(photo_urls),
        article,
    )
    return local_paths


def download_watermark_zip(
    page: Page,
    watermark_url: str,
    article: str,
) -> list[str]:
    """Завантажити всі фотографії з водяним знаком через ZIP-архів CRM.

    Args:
        page: Playwright Page з активною CRM-сесією (для cookies).
        watermark_url: Відносний або абсолютний URL вигляду
                       "/estate/12345/download-all-watermark-images".
        article: Номер артикула об'єкта, використовується як назва підпапки.

    Returns:
        Список локальних шляхів до успішно розпакованих фотографій,
        або порожній список у разі помилки.
    """
    full_url = watermark_url if watermark_url.startswith("http") else f"{CRM_BASE}{watermark_url}"
    dest_dir = PICS_DIR / str(article)

    try:
        response = page.context.request.get(full_url)
        if not response.ok:
            logger.warning(
                "Не вдалося завантажити watermark ZIP для %s: HTTP %d",
                article,
                response.status,
            )
            return []

        dest_dir.mkdir(parents=True, exist_ok=True)
        local_paths: list[str] = []

        with zipfile.ZipFile(io.BytesIO(response.body())) as zf:
            image_names = sorted(
                n for n in zf.namelist()
                if not n.endswith("/") and Path(n).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
            )
            for i, name in enumerate(image_names):
                ext = Path(name).suffix.lower() or ".jpg"
                filename = f"photo_{i:03d}{ext}"
                filepath = dest_dir / filename
                filepath.write_bytes(zf.read(name))
                local_paths.append(str(filepath))

        logger.info(
            "Завантажено %d фото (watermark ZIP) для артикула %s",
            len(local_paths),
            article,
        )
        return local_paths

    except zipfile.BadZipFile:
        logger.warning("Відповідь не є ZIP-архівом для %s, URL: %s", article, full_url)
        return []
    except Exception:
        logger.exception("Помилка завантаження watermark ZIP для %s", article)
        return []


def cleanup_photos(article: str) -> None:
    """Видалити папку з фотографіями для вказаного артикула після успішного завантаження."""
    dest_dir = PICS_DIR / str(article)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
        logger.info("Фотографії для артикула %s видалено", article)


def _guess_extension(url: str, content_type: str) -> str:
    """Визначити розширення файлу за шляхом URL або заголовком Content-Type."""
    suffix = PurePosixPath(url.split("?")[0]).suffix.lower()
    if suffix in (".jpg", ".jpeg", ".png", ".webp"):
        return suffix

    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    return ".jpg"
