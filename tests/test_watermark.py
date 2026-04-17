"""Test that watermark is applied to a real photo from crm_data_parser/offers/pics."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

SAMPLE_PHOTO = "crm_data_parser/offers/pics/A28526/photo_000.jpg"


@pytest.fixture(autouse=True)
def reset_watermark_cache():
    """Clear the in-memory watermark cache between tests."""
    import rieltor_handler.new_offer_poster.photo_processing as pp
    pp._watermark_cache = None
    yield
    pp._watermark_cache = None


def test_watermark_applied_changes_pixels():
    """Pixels in the centre of the image change after watermark is applied."""
    from rieltor_handler.new_offer_poster.photo_processing import _apply_watermark

    with Image.open(SAMPLE_PHOTO) as img:
        original = img.convert("RGB").copy()

    result = _apply_watermark(original.copy())

    assert result.size == original.size, "Image dimensions must not change"
    assert result.mode == "RGB"

    orig_arr = np.array(original)
    result_arr = np.array(result)

    # Compare a central crop (where the watermark lands)
    h, w = orig_arr.shape[:2]
    cy, cx = h // 2, w // 2
    crop_size = min(h, w) // 4
    orig_crop = orig_arr[cy - crop_size:cy + crop_size, cx - crop_size:cx + crop_size]
    result_crop = result_arr[cy - crop_size:cy + crop_size, cx - crop_size:cx + crop_size]

    changed_pixels = np.any(orig_crop != result_crop, axis=-1).sum()
    total_pixels = orig_crop.shape[0] * orig_crop.shape[1]

    assert changed_pixels > total_pixels * 0.01, (
        f"Expected >1% of central pixels to change after watermark, "
        f"got {changed_pixels}/{total_pixels}"
    )


def test_watermark_returns_rgb():
    """Result is always RGB regardless of input mode."""
    from rieltor_handler.new_offer_poster.photo_processing import _apply_watermark

    with Image.open(SAMPLE_PHOTO) as img:
        rgba_img = img.convert("RGBA")

    result = _apply_watermark(rgba_img)
    assert result.mode == "RGB"


def test_watermark_cache_populated():
    """Watermark is cached after first call so SVG is only rendered once."""
    import rieltor_handler.new_offer_poster.photo_processing as pp
    from rieltor_handler.new_offer_poster.photo_processing import _get_watermark

    assert pp._watermark_cache is None
    wm = _get_watermark()
    assert wm is not None, "Watermark must load successfully"
    assert pp._watermark_cache is wm, "Watermark must be cached after first call"
    # Second call returns the same object (no re-render)
    assert _get_watermark() is wm


def test_prepare_photos_produces_watermarked_file(monkeypatch):
    """prepare_photos writes a JPEG that differs from the original (watermark present)."""
    import os
    import rieltor_handler.new_offer_poster.photo_processing as pp
    monkeypatch.setattr(pp, "ADD_WATERMARK", True)

    result_paths = pp.prepare_photos([SAMPLE_PHOTO])
    assert result_paths, "prepare_photos must return at least one path"

    out_path = result_paths[0]
    assert os.path.exists(out_path)
    assert out_path.endswith(".jpg")

    with Image.open(SAMPLE_PHOTO) as orig, Image.open(out_path) as out:
        orig_arr = np.array(orig.convert("RGB"))
        out_arr = np.array(out.convert("RGB"))

    # Images may differ in size if resized, compare overlapping region
    h = min(orig_arr.shape[0], out_arr.shape[0])
    w = min(orig_arr.shape[1], out_arr.shape[1])
    diff = np.any(orig_arr[:h, :w] != out_arr[:h, :w], axis=-1).sum()
    assert diff > 0, "Output photo must differ from input (watermark expected)"


def test_watermark_applied_to_already_downloaded_photos(monkeypatch):
    """Phase 2 posting applies watermark to photos already on disk (ADD_WATERMARK=true).

    Simulates the exact scenario: photos were downloaded in Phase 1 and saved to
    crm_data_parser/offers/pics/. Phase 2 reads the paths from offer_data and
    calls prepare_photos — watermark must be applied at that point.
    """
    import os
    import rieltor_handler.new_offer_poster.photo_processing as pp
    monkeypatch.setattr(pp, "ADD_WATERMARK", True)

    # Simulate offer_data["apartment"]["photos"] as stored in DB after Phase 1
    already_downloaded_paths = [SAMPLE_PHOTO]

    # This is exactly what _upload_photos_in_photo_section calls (photos.py:288)
    prepared = pp.prepare_photos(already_downloaded_paths)

    assert prepared, "Must produce at least one prepared file"
    for path in prepared:
        assert os.path.exists(path), f"Prepared file must exist: {path}"

    with Image.open(SAMPLE_PHOTO) as orig, Image.open(prepared[0]) as out:
        orig_arr = np.array(orig.convert("RGB"))
        out_arr = np.array(out.convert("RGB"))

    h = min(orig_arr.shape[0], out_arr.shape[0])
    w = min(orig_arr.shape[1], out_arr.shape[1])
    cy, cx = h // 2, w // 2
    crop = slice(cy - h // 8, cy + h // 8), slice(cx - w // 8, cx + w // 8)
    changed = np.any(orig_arr[crop] != out_arr[crop], axis=-1).sum()
    total = orig_arr[crop].shape[0] * orig_arr[crop].shape[1]

    assert changed > total * 0.01, (
        f"Watermark must be visible in centre of already-downloaded photo "
        f"({changed}/{total} pixels changed, expected >1%)"
    )


def test_watermark_renders_inside_asyncio_loop():
    """_get_watermark must succeed even when called from within a running asyncio loop.

    Reproduces the production failure: Phase 2 Playwright posting session keeps an
    asyncio loop alive, and sync_playwright() raised an error when called from it.
    """
    import asyncio
    import rieltor_handler.new_offer_poster.photo_processing as pp
    pp._watermark_cache = None

    async def _inner():
        return pp._get_watermark()

    result = asyncio.run(_inner())
    assert result is not None, "_get_watermark must succeed inside asyncio loop"


def test_no_watermark_when_disabled(monkeypatch):
    """prepare_photos must NOT apply watermark when ADD_WATERMARK=false."""
    import rieltor_handler.new_offer_poster.photo_processing as pp
    monkeypatch.setattr(pp, "ADD_WATERMARK", False)

    prepared = pp.prepare_photos([SAMPLE_PHOTO])
    assert prepared, "Must still produce a prepared file"

    with Image.open(SAMPLE_PHOTO) as orig, Image.open(prepared[0]) as out:
        orig_arr = np.array(orig.convert("RGB"))
        out_arr = np.array(out.convert("RGB"))

    h = min(orig_arr.shape[0], out_arr.shape[0])
    w = min(orig_arr.shape[1], out_arr.shape[1])
    cy, cx = h // 2, w // 2
    crop = slice(cy - h // 8, cy + h // 8), slice(cx - w // 8, cx + w // 8)
    # With no watermark, centre pixels must be identical (only JPEG compression differs)
    orig_crop = orig_arr[crop].astype(np.int16)
    out_crop = out_arr[crop].astype(np.int16)
    max_diff = np.abs(orig_crop - out_crop).max()

    # JPEG re-encode introduces small rounding errors (≤ ~5 per channel) — not watermark
    assert max_diff <= 10, (
        f"Centre pixels changed by {max_diff} without watermark — "
        "expected only JPEG compression noise (≤10)"
    )
