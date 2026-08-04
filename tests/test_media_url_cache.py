"""Panel thumbnails must not cost a Graph call each time they are shown.

Live question: what happens on the panel display side? Every image the
panel renders resolves a short-lived provider URL through Microsoft
Graph - scrolling a trip with hundreds of memories meant hundreds of
calls. The resolved URL is now memoized for a few minutes, while the
bytes keep flowing straight from the provider CDN (Home Assistant never
relays them - the view still answers with a redirect).
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_media_url_cache_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object
sys.modules.setdefault("homeassistant.core", ha_core)


class ValidationError(RuntimeError):
    pass


roadplanner = types.ModuleType(f"{PACKAGE_NAME}.roadplanner")
roadplanner.ValidationError = ValidationError
sys.modules[roadplanner.__name__] = roadplanner
experience_store = types.ModuleType(f"{PACKAGE_NAME}.experience_store")
experience_store.ExperienceStore = object
sys.modules[experience_store.__name__] = experience_store
onedrive_media = types.ModuleType(f"{PACKAGE_NAME}.onedrive_media")
onedrive_media.OneDrivePersonalClient = object
sys.modules[onedrive_media.__name__] = onedrive_media

spec = spec_from_file_location(
    f"{PACKAGE_NAME}.media_token_service", PACKAGE_ROOT / "media_token_service.py"
)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class FakeHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


class FakeStore:
    def __init__(self) -> None:
        self.loads = 0

    def load(self, trip_id: str) -> dict:
        self.loads += 1
        return {"media": [{"id": "m1", "provider_item_id": "drive-1"}]}


class FakeOneDrive:
    def __init__(self) -> None:
        self.thumbnail_calls: list[tuple[str, str]] = []
        self.download_calls: list[str] = []

    async def async_thumbnail_url(self, item_id: str, size: str = "large") -> str:
        self.thumbnail_calls.append((item_id, size))
        return f"https://cdn.example/{item_id}/{size}?sig={len(self.thumbnail_calls)}"

    async def async_download_url(self, item_id: str) -> str:
        self.download_calls.append(item_id)
        return f"https://cdn.example/{item_id}/original"


def _service() -> tuple:
    onedrive = FakeOneDrive()
    store = FakeStore()
    return module.MediaTokenService(FakeHass(), store, onedrive), store, onedrive


def verify_repeat_views_reuse_the_resolved_url() -> None:
    service, store, onedrive = _service()
    first = asyncio.run(service.async_media_redirect_url("trip-1", "m1", "thumbnail"))
    for _ in range(20):
        again = asyncio.run(service.async_media_redirect_url("trip-1", "m1", "thumbnail"))
        assert again == first
    assert len(onedrive.thumbnail_calls) == 1, (
        f"{len(onedrive.thumbnail_calls)} Graph calls - scrolling a gallery "
        "must not resolve the same photo again and again"
    )
    assert store.loads == 1, "a cache hit does not even touch the store"


def verify_sizes_and_kinds_stay_separate() -> None:
    service, _store, onedrive = _service()
    small = asyncio.run(service.async_media_redirect_url("trip-1", "m1", "thumbnail"))
    large = asyncio.run(
        service.async_media_redirect_url("trip-1", "m1", "thumbnail", size="c1920x1440")
    )
    original = asyncio.run(service.async_media_redirect_url("trip-1", "m1", "original"))
    assert small != large != original
    assert onedrive.thumbnail_calls == [("drive-1", "large"), ("drive-1", "c1920x1440")]
    assert onedrive.download_calls == ["drive-1"]


def verify_expired_entries_are_resolved_again() -> None:
    service, _store, onedrive = _service()
    asyncio.run(service.async_media_redirect_url("trip-1", "m1", "thumbnail"))
    # Force expiry without waiting five minutes.
    key = ("m1", "thumbnail", "large")
    url, _expires = service._url_cache[key]
    service._url_cache[key] = (url, 0.0)
    asyncio.run(service.async_media_redirect_url("trip-1", "m1", "thumbnail"))
    assert len(onedrive.thumbnail_calls) == 2, "a stale URL is never served"


def verify_cache_stays_bounded() -> None:
    service, _store, _onedrive = _service()
    for index in range(module._URL_CACHE_MAX_ENTRIES + 50):
        service._remember_url((f"m{index}", "thumbnail", "large"), "https://x")
    assert len(service._url_cache) <= module._URL_CACHE_MAX_ENTRIES


def verify_bytes_still_bypass_home_assistant() -> None:
    view = (PACKAGE_ROOT / "experience_http.py").read_text(encoding="utf-8")
    assert "web.HTTPFound(target)" in view, (
        "the panel view must keep redirecting - relaying image bytes through "
        "Home Assistant would add load instead of removing it"
    )


if __name__ == "__main__":
    verify_repeat_views_reuse_the_resolved_url()
    verify_sizes_and_kinds_stay_separate()
    verify_expired_entries_are_resolved_again()
    verify_cache_stays_bounded()
    verify_bytes_still_bypass_home_assistant()
    print("Media URL cache tests passed.")
