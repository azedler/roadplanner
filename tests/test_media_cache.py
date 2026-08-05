"""Local cache for already-downloaded personal photo previews.

Live question: "Wenn wir die Bilder von OneDrive jetzt auch für PDF und
Video herunterladen - wollen wir die dann nicht auch lokal speichern? Die
werden an so vielen Stellen verwendet." Yes: every export otherwise costs
two Microsoft Graph round trips per photo, again and again. The cache is
bounded, least-recently-used, and fails open everywhere.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import sys
import tempfile
import time
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_media_cache_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

spec = spec_from_file_location(
    f"{PACKAGE_NAME}.media_cache", PACKAGE_ROOT / "media_cache.py"
)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

# A real JPEG header: the cache stores only images a renderer can open.
PHOTO = b"\xff\xd8\xff\xe0" + b"x" * 4092


def verify_round_trip_and_key_identity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache = module.MediaCache(Path(tmp) / "media_cache")
        key = module.cache_key("media-1", "drive-item-1", "c1920x1440")
        assert cache.read(key) is None, "an empty cache simply misses"
        cache.write(key, PHOTO)
        assert cache.read(key) == PHOTO
        # Same photo in another size is a different entry.
        assert module.cache_key("media-1", "drive-item-1", "large") != key
        # A re-imported photo reusing the media id can never serve stale
        # bytes - the provider item id is part of the key.
        assert module.cache_key("media-1", "drive-item-2", "c1920x1440") != key
        # ... and so is the REQUEST KIND. Live report: the rendered
        # thumbnail and the untouched original were both requested under
        # size "large", so an undecodable HEIC original cached earlier was
        # served to the thumbnail request that exists precisely to avoid
        # HEIC - and every export stayed empty even after that fix.
        assert module.cache_key(
            "media-1", "drive-item-1", "large", "thumbnail"
        ) != module.cache_key("media-1", "drive-item-1", "large", "original")


def verify_undecodable_images_are_never_stored() -> None:
    # HEIC ("....ftypheic"): downloads fine, opens nowhere. Caching it would
    # turn one bad download into a permanently failing export.
    heic = b"\x00\x00\x00\x18ftypheic" + b"x" * 4096
    assert module.is_decodable_image(heic) is False
    assert module.is_decodable_image(PHOTO) is True
    assert module.is_decodable_image(b"\x89PNG\r\n\x1a\n" + b"x" * 100) is True
    assert module.is_decodable_image(b"RIFF" + b"1234" + b"WEBP" + b"x" * 100) is True
    assert module.is_decodable_image(b"kurz") is False
    with tempfile.TemporaryDirectory() as tmp:
        cache = module.MediaCache(Path(tmp))
        key = module.cache_key("m", "i", "large", "original")
        cache.write(key, heic)
        assert cache.read(key) is None, "an unopenable image is not cached"


def verify_lru_pruning_keeps_the_cache_bounded() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache = module.MediaCache(Path(tmp), max_bytes=3 * len(PHOTO))
        keys = [module.cache_key(f"m{i}", "item", "large") for i in range(5)]
        for index, key in enumerate(keys):
            cache.write(key, PHOTO)
            # Deterministic LRU order regardless of filesystem timestamp
            # granularity.
            os.utime(Path(tmp) / key, (1_700_000_000 + index, 1_700_000_000 + index))
            cache.prune()
        stored = [key for key in keys if cache.read(key) is not None]
        assert len(stored) <= 3, stored
        assert keys[-1] in stored, "the newest photo survives"
        assert keys[0] not in stored, "the oldest photo is evicted first"
        assert cache.stats()["bytes"] <= 3 * len(PHOTO)


def verify_cache_fails_open() -> None:
    disabled = module.MediaCache(Path("/nonexistent"), max_bytes=0)
    assert disabled.enabled is False
    key = module.cache_key("m", "i", "large")
    disabled.write(key, PHOTO)
    assert disabled.read(key) is None
    # An unwritable root never raises.
    unwritable = module.MediaCache(Path("/proc/roadplanner-cache"))
    unwritable.write(key, PHOTO)
    assert unwritable.read(key) is None
    assert unwritable.stats()["files"] == 0
    # Truncated/empty payloads are never served as a photo.
    with tempfile.TemporaryDirectory() as tmp:
        cache = module.MediaCache(Path(tmp))
        cache.write(key, b"tiny")
        assert cache.read(key) is None


def verify_exports_use_the_cache() -> None:
    photos_source = (PACKAGE_ROOT / "trip_export_photos.py").read_text(encoding="utf-8")
    assert "cache_key(media_id, provider_item_id, size, kind)" in photos_source, (
        "the request kind belongs in the key - see the collision above"
    )
    assert "if not is_decodable_image(photo):" in photos_source, (
        "a HEIC download must be skipped, not handed on to a renderer that "
        "cannot open it"
    )
    assert "cached = await hass.async_add_executor_job(cache.read, key)" in photos_source
    assert "await hass.async_add_executor_job(cache.write, key, photo)" in photos_source
    assert photos_source.index("cached = await hass.async_add_executor_job") < photos_source.index(
        "url = await experience.async_media_redirect_url"
    ), "a cache hit must short-circuit the Graph round trip"
    # Stock/provider imagery is deliberately NOT cached - only own photos.
    stock = photos_source.split("async def async_fetch_stock_stop_photo")[1]
    assert "cache" not in stock.split("async def")[0]
    init_source = (PACKAGE_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert 'MediaCache(archive_root / "media_cache")' in init_source
    # ONE cache for every consumer that downloads the user's own photos:
    # the PDF export, the video export and the summary generator all fetch
    # the same pictures ("da ist ein permanentes Runterladen eigentlich
    # übertrieben").
    assert init_source.count("media_cache=media_cache,") >= 3, (
        "the PDF, video and summary paths must share one cache"
    )
    for consumer in ("TripPdfExporter", "TripVideoExporter", "TripSummaryService"):
        constructed = init_source.split(f"{consumer}(", 1)
        assert len(constructed) == 2, consumer
        assert "media_cache=media_cache," in constructed[1].split(")\n", 1)[0], (
            f"{consumer} must be given the shared media cache"
        )


if __name__ == "__main__":
    verify_round_trip_and_key_identity()
    verify_undecodable_images_are_never_stored()
    verify_lru_pruning_keeps_the_cache_bounded()
    verify_cache_fails_open()
    verify_exports_use_the_cache()
    print("Media cache tests passed.")
