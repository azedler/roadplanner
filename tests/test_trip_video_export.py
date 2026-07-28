"""Behavioral tests for trip video export orchestration.

The ticket lifecycle is pure asyncio, exercised directly like the PDF
exporter's. The full async_generate data-gathering path is exercised with
fake manager/experience/provider objects and a real ffmpeg binary is NOT
invoked here (that pipeline was manually verified separately - see the
trip_video.py/ffmpeg_runner.py commit history - and is exercised behaviorally
in test_trip_video.py and test_ffmpeg_runner.py). Here we only need to prove
the orchestration logic itself: fail-fast on missing ffmpeg, the corrected
summary.trip payload read, and graceful degradation when Gemini or the
bundled music folder have nothing to offer.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_trip_video_export_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
aiohttp_stub.ClientTimeout = lambda *args, **kwargs: None
sys.modules["aiohttp"] = aiohttp_stub

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object
sys.modules["homeassistant.core"] = ha_core
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers.__path__ = []
sys.modules["homeassistant.helpers"] = ha_helpers
ha_aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
ha_aiohttp_client.async_get_clientsession = lambda *a, **k: None
sys.modules["homeassistant.helpers.aiohttp_client"] = ha_aiohttp_client


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


for module_name in ("bounded_json", "json_io", "identifiers", "json_tree_validation"):
    try:
        load(module_name)
    except FileNotFoundError:
        pass

load("canonical_day")
export_module = load("trip_video_export")


def _exporter(*, manager=None, experience=None, provider=None):
    return export_module.TripVideoExporter(
        hass=None,
        manager=manager,
        experience=experience,
        provider=provider,
        map_snapshot_provider="openstreetmap",
        google_maps_api_key=None,
    )


def verify_ticket_round_trip() -> None:
    async def scenario() -> None:
        exporter = _exporter()
        token = await exporter.async_create_ticket(b"video-bytes", user_id="user-1")
        resolved = await exporter.async_resolve_ticket(token)
        assert resolved == b"video-bytes"

    asyncio.run(scenario())


def verify_ticket_is_single_use_limited() -> None:
    async def scenario() -> None:
        exporter = _exporter()
        token = await exporter.async_create_ticket(b"video-bytes", user_id="user-1")
        for _ in range(export_module._MAX_TICKET_USES):
            await exporter.async_resolve_ticket(token)
        try:
            await exporter.async_resolve_ticket(token)
        except export_module.ValidationError:
            return
        raise AssertionError("expected the exhausted ticket to be rejected")

    asyncio.run(scenario())


def verify_unknown_token_is_rejected() -> None:
    async def scenario() -> None:
        exporter = _exporter()
        try:
            await exporter.async_resolve_ticket("does-not-exist")
        except export_module.ValidationError:
            return
        raise AssertionError("expected an unknown token to be rejected")

    asyncio.run(scenario())


def verify_expired_ticket_is_purged() -> None:
    async def scenario() -> None:
        exporter = _exporter()
        token = await exporter.async_create_ticket(b"video-bytes", user_id="user-1")
        exporter._tickets[token].expires_monotonic = 0.0
        try:
            await exporter.async_resolve_ticket(token)
        except export_module.ValidationError:
            return
        raise AssertionError("expected an expired ticket to be purged and rejected")

    asyncio.run(scenario())


verify_ticket_round_trip()
verify_ticket_is_single_use_limited()
verify_unknown_token_is_rejected()
verify_expired_ticket_is_purged()


class _RaisesIfCalled:
    """A stand-in that fails the test if any of its methods are ever awaited."""

    async def async_get_assistant_payload(self, trip_id):
        raise AssertionError("manager must not be touched once ffmpeg is unavailable")

    async def async_panel_payload(self, trip_id, *, days=None):
        raise AssertionError("experience must not be touched once ffmpeg is unavailable")


def verify_missing_ffmpeg_fails_fast_before_any_other_work() -> None:
    async def scenario() -> None:
        exporter = _exporter(manager=_RaisesIfCalled(), experience=_RaisesIfCalled())
        original = export_module.ffmpeg_available
        export_module.ffmpeg_available = lambda: False
        try:
            await exporter.async_generate("trip-1")
        except export_module.RoadplannerError:
            return
        finally:
            export_module.ffmpeg_available = original
        raise AssertionError("expected a missing ffmpeg to raise before any other work")

    asyncio.run(scenario())


class _FakeManager:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def async_get_assistant_payload(self, trip_id: str) -> dict:
        return self._payload


class _FakeExperience:
    async def async_panel_payload(self, trip_id: str, *, days=None) -> dict:
        return {"destination_galleries": {}, "media": []}


class _FakeHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


def verify_async_generate_reads_trip_from_the_real_nested_payload_shape() -> None:
    """Regression guard mirroring the PDF exporter's fix: the assistant
    payload has no top-level "trip" key - the real shape nests it under
    payload["summary"]["trip"]."""

    async def scenario() -> None:
        payload = {
            "selected_trip_id": "trip-1",
            "summary": {"trip": {"title": "Finnland / Baltikum 2026"}},
            "days": {"days": []},
        }
        exporter = _exporter(manager=_FakeManager(payload), experience=_FakeExperience())
        exporter.hass = _FakeHass()
        original_available = export_module.ffmpeg_available
        export_module.ffmpeg_available = lambda: True
        captured = {}
        original_prepare = export_module.prepare_chapter_assets

        def fake_prepare(data, workdir):
            captured["data"] = data
            return []  # no frames -> async_generate raises before ever invoking ffmpeg

        export_module.prepare_chapter_assets = fake_prepare
        try:
            try:
                await exporter.async_generate("trip-1")
            except export_module.ValidationError:
                pass
        finally:
            export_module.ffmpeg_available = original_available
            export_module.prepare_chapter_assets = original_prepare
        assert captured["data"].title == "Finnland / Baltikum 2026"

    asyncio.run(scenario())


def verify_narrative_generation_failure_degrades_to_an_empty_string() -> None:
    class _RaisingProvider:
        async def async_generate_text(self, **kwargs):
            raise export_module.RoadplannerError("Gemini nicht verfügbar")

    async def scenario() -> None:
        exporter = _exporter(provider=_RaisingProvider())
        narrative = await exporter._async_generate_narrative(
            {"title": "Tag 1", "date": "2026-07-17"}, []
        )
        assert narrative == ""

    asyncio.run(scenario())


def verify_narrative_is_used_verbatim_on_success() -> None:
    class _Result:
        text = "  Ein sonniger Tag am Meer.  "

    class _WorkingProvider:
        async def async_generate_text(self, **kwargs):
            return _Result()

    async def scenario() -> None:
        exporter = _exporter(provider=_WorkingProvider())
        narrative = await exporter._async_generate_narrative(
            {"title": "Tag 1", "date": "2026-07-17"}, []
        )
        assert narrative == "Ein sonniger Tag am Meer."

    asyncio.run(scenario())


def verify_no_provider_means_no_narrative_attempt() -> None:
    async def scenario() -> None:
        exporter = _exporter(provider=None)
        narrative = await exporter._async_generate_narrative(
            {"title": "Tag 1", "date": "2026-07-17"}, []
        )
        assert narrative == ""

    asyncio.run(scenario())


def verify_empty_music_folder_returns_none_gracefully() -> None:
    # assets/music/ ships empty in this pass - see its README.md.
    assert export_module.pick_music_track("any-trip-id") is None


def verify_music_pick_is_deterministic_per_trip() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "one.mp3").write_bytes(b"fake-mp3-a")
        (tmp_path / "two.mp3").write_bytes(b"fake-mp3-b")
        original_dir = export_module._music_directory
        export_module._music_directory = lambda: tmp_path
        try:
            first = export_module.pick_music_track("trip-alpha")
            second = export_module.pick_music_track("trip-alpha")
            assert first == second
            assert first is not None and first.parent == tmp_path
        finally:
            export_module._music_directory = original_dir


verify_missing_ffmpeg_fails_fast_before_any_other_work()
verify_async_generate_reads_trip_from_the_real_nested_payload_shape()
verify_narrative_generation_failure_degrades_to_an_empty_string()
verify_narrative_is_used_verbatim_on_success()
verify_no_provider_means_no_narrative_attempt()
verify_empty_music_folder_returns_none_gracefully()
verify_music_pick_is_deterministic_per_trip()

print("Trip video export tests passed.")
