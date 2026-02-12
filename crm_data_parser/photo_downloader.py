"""Photo download and cleanup utilities.

Downloads estate photos from CRM using the authenticated Playwright session
and saves them locally for upload to Rieltor.
"""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from typing import List

from playwright.sync_api import Page

from setup_logger import setup_logger

logger = setup_logger(__name__)

PICS_DIR = Path(__file__).parent / "offers" / "pics"
CRM_BASE = "https://crm-primes.realtsoft.net"


def download_estate_photos(
    page: Page,
    photo_urls: List[str],
    article: str,
) -> List[str]:
    """Download photos from CRM using the authenticated Playwright session.

    Args:
        page: Playwright Page with active CRM session (for cookies).
        photo_urls: List of CRM photo URLs (relative or absolute).
        article: Estate article number, used as subfolder name.

    Returns:
        List of local file paths for successfully downloaded photos.
    """
    if not photo_urls:
        return []

    dest_dir = PICS_DIR / str(article)
    dest_dir.mkdir(parents=True, exist_ok=True)

    local_paths: List[str] = []
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
                logger.debug("Downloaded photo %d/%d: %s", i + 1, len(photo_urls), filename)
            else:
                logger.warning("Failed to download %s: HTTP %d", full_url, response.status)
        except Exception:
            logger.exception("Error downloading photo: %s", full_url)

    logger.info("Downloaded %d/%d photos for article %s", len(local_paths), len(photo_urls), article)
    return local_paths


def cleanup_photos(article: str) -> None:
    """Remove the photo directory for a given article after successful upload."""
    dest_dir = PICS_DIR / str(article)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
        logger.info("Cleaned up photos for article %s", article)


def _guess_extension(url: str, content_type: str) -> str:
    """Guess file extension from URL path or Content-Type header."""
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
