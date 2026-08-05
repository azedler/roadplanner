"""Behavioral tests for trip video export orchestration.

A real ffmpeg binary is NOT invoked here (that pipeline was manually
verified separately and is exercised behaviorally in test_trip_video.py and
test_ffmpeg_runner.py). Here we prove the orchestration logic: fail-fast on
missing ffmpeg, the corrected summary.trip payload read, graceful
degradation when Gemini or the bundled music folder have nothing to offer,
and the durable library save/prune/notify flow that replaced the PDF-style
ephemeral ticket (a video render can take minutes; the app may well be
closed by the time it's ready, so the result has to survive on disk with a
Home Assistant notification pointing at it, not just an in-memory ticket
tied to one WebSocket response).
"""
from __future__ import annotations

import re

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import tempfile
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


class _FakeServices:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def async_call(self, domain: str, service: str, data: dict) -> None:
        self.calls.append((domain, service, data))


class _FakeHass:
    def __init__(self) -> None:
        self.services = _FakeServices()

    async def async_add_executor_job(self, func, *args):
        return func(*args)


def _exporter(*, manager=None, experience=None, provider=None, library_dir=None, hass=None):
    return export_module.TripVideoExporter(
        hass=hass if hass is not None else _FakeHass(),
        manager=manager,
        experience=experience,
        provider=provider,
        map_snapshot_provider="openstreetmap",
        google_maps_api_key=None,
        library_dir=library_dir if library_dir is not None else Path(tempfile.mkdtemp()),
    )


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


def verify_save_to_library_writes_a_valid_filename() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        exporter = _exporter(library_dir=Path(tmp))
        filename = exporter._save_to_library(b"fake-mp4-bytes")
        assert export_module.VIDEO_FILENAME_RE.match(filename)
        assert (exporter.library_dir / filename).read_bytes() == b"fake-mp4-bytes"


def verify_library_prunes_beyond_the_retention_limit() -> None:
    import os

    with tempfile.TemporaryDirectory() as tmp:
        exporter = _exporter(library_dir=Path(tmp))
        filenames = []
        for i in range(export_module.MAX_STORED_TRIP_VIDEOS + 3):
            filename = exporter._save_to_library(f"video-{i}".encode())
            # Force a strictly increasing mtime per file - writes in a tight
            # loop can otherwise land within the filesystem's mtime
            # granularity and make "oldest first" ordering non-deterministic.
            os.utime(exporter.library_dir / filename, (i, i))
            filenames.append(filename)
        remaining = list(Path(tmp).glob("*.mp4"))
        assert len(remaining) == export_module.MAX_STORED_TRIP_VIDEOS
        # The earliest-written files must be the ones pruned away.
        remaining_names = {path.name for path in remaining}
        assert filenames[0] not in remaining_names
        assert filenames[-1] in remaining_names


def verify_notify_ready_calls_persistent_notification_with_the_link() -> None:
    async def scenario() -> None:
        hass = _FakeHass()
        exporter = _exporter(hass=hass)
        await exporter._async_notify_ready(
            "Finnland / Baltikum 2026", "/api/roadplanner/trip_video_library/abc.mp4"
        )
        assert len(hass.services.calls) == 1
        domain, service, call_data = hass.services.calls[0]
        assert domain == "persistent_notification"
        assert service == "create"
        assert "/api/roadplanner/trip_video_library/abc.mp4" in call_data["message"]
        assert "Finnland / Baltikum 2026" in call_data["message"]

    asyncio.run(scenario())


def verify_notify_ready_failure_does_not_raise() -> None:
    """A failed notification (e.g. the service isn't available) must not
    undo an already-successful export - the file is safely on disk either
    way."""

    class _FailingServices:
        async def async_call(self, domain, service, data):
            raise RuntimeError("boom")

    async def scenario() -> None:
        hass = _FakeHass()
        hass.services = _FailingServices()
        exporter = _exporter(hass=hass)
        await exporter._async_notify_ready("Reise", "/api/roadplanner/trip_video_library/x.mp4")

    asyncio.run(scenario())


def verify_async_generate_and_publish_saves_and_notifies() -> None:
    async def scenario() -> None:
        payload = {
            "selected_trip_id": "trip-1",
            "summary": {"trip": {"title": "Finnland / Baltikum 2026"}},
            "days": {"days": []},
        }
        with tempfile.TemporaryDirectory() as tmp:
            hass = _FakeHass()
            exporter = _exporter(
                manager=_FakeManager(payload),
                experience=_FakeExperience(),
                hass=hass,
                library_dir=Path(tmp),
            )
            original_available = export_module.ffmpeg_available
            original_prepare = export_module.prepare_chapter_assets
            original_filter = export_module.build_ffmpeg_filter_graph
            original_run = export_module.async_run_ffmpeg
            export_module.ffmpeg_available = lambda: True

            def fake_prepare(data, workdir):
                # Simulate ffmpeg having produced an output file at the
                # expected path, without needing a real ffmpeg subprocess.
                (workdir / "trip_video.mp4").write_bytes(b"fake-rendered-video")
                return [workdir / "frame_0000.jpg"]

            def fake_filter_graph(data, frame_paths):
                return [], "", "[n0]"

            async def fake_run_ffmpeg(args, *, timeout_seconds):
                return None

            export_module.prepare_chapter_assets = fake_prepare
            export_module.build_ffmpeg_filter_graph = fake_filter_graph
            export_module.async_run_ffmpeg = fake_run_ffmpeg
            try:
                download_url = await exporter.async_generate_and_publish("trip-1")
            finally:
                export_module.ffmpeg_available = original_available
                export_module.prepare_chapter_assets = original_prepare
                export_module.build_ffmpeg_filter_graph = original_filter
                export_module.async_run_ffmpeg = original_run

            assert download_url.startswith("/api/roadplanner/trip_video_library/")
            filename = download_url.rsplit("/", 1)[-1]
            assert (Path(tmp) / filename).read_bytes() == b"fake-rendered-video"
            assert len(hass.services.calls) == 1
            assert download_url in hass.services.calls[0][2]["message"]

    asyncio.run(scenario())


def verify_a_video_always_has_an_audio_track() -> None:
    """Live question: "War da Musik enthalten?" - there was no audio at all.

    The bundled music folder ships empty, and without music the export
    produced a file with a single video stream. Several players and photo
    libraries treat that as broken, so a silent track is added instead.
    """
    source = (PACKAGE_ROOT / "trip_video_export.py").read_text(encoding="utf-8")
    assert "anullsrc=r=44100:cl=stereo" in source
    assert "audio_input_index: int | None = None" not in source, (
        "there is no audio-less branch any more"
    )


def verify_a_highlight_reel_is_not_two_stills() -> None:
    source = (PACKAGE_ROOT / "trip_video_export.py").read_text(encoding="utf-8")
    assert '_MAX_PHOTOS_PER_CHAPTER = {"highlight": 3, "full": 6}' in source
    assert '_MAX_CHAPTERS = {"highlight": 12, "full": 40}' in source


verify_missing_ffmpeg_fails_fast_before_any_other_work()
verify_async_generate_reads_trip_from_the_real_nested_payload_shape()
verify_narrative_generation_failure_degrades_to_an_empty_string()
verify_narrative_is_used_verbatim_on_success()
verify_no_provider_means_no_narrative_attempt()
verify_empty_music_folder_returns_none_gracefully()
verify_music_pick_is_deterministic_per_trip()
verify_save_to_library_writes_a_valid_filename()
verify_library_prunes_beyond_the_retention_limit()
verify_notify_ready_calls_persistent_notification_with_the_link()
verify_notify_ready_failure_does_not_raise()
verify_async_generate_and_publish_saves_and_notifies()
verify_a_video_always_has_an_audio_track()
verify_a_highlight_reel_is_not_two_stills()

print("Trip video export tests passed.")
