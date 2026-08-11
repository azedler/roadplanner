"""The paid path, driven end to end with nothing that can charge anybody.

A fake provider, a fake media source and a real store. What is checked is
the part that costs money and the part that must never cost money:

- the offer is free and says the price BEFORE the run,
- a stored answer means no second call, ever,
- a render reads segments and calls nothing,
- one unreadable recording loses one recording, not the run,
- the original is deleted whatever happens.

The fake provider counts its calls, because "no provider call" is the
kind of claim that is only worth making if something fails when it stops
being true.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"
sys.dont_write_bytecode = True

if "homeassistant" not in sys.modules:
    _ha = types.ModuleType("homeassistant")
    _core = types.ModuleType("homeassistant.core")

    class HomeAssistant:  # noqa: D401 - stand-in
        """Stand-in for the real class."""

    _core.HomeAssistant = HomeAssistant
    sys.modules.update(
        {
            "homeassistant": _ha,
            "homeassistant.core": _core,
            "homeassistant.helpers": types.ModuleType("homeassistant.helpers"),
        }
    )

_PACKAGE = "roadplanner_video_service_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(INTEGRATION)]
sys.modules[_PACKAGE] = _root

service_module = importlib.import_module(f"{_PACKAGE}.video_curation_service")
store_module = importlib.import_module(f"{_PACKAGE}.experience_store")
analysis = importlib.import_module(f"{_PACKAGE}.video_analysis")

SERVICE_SOURCE = (INTEGRATION / "video_curation_service.py").read_text(encoding="utf-8")


class FakeHass:
    """Enough Home Assistant to run executor jobs inline."""

    async def async_add_executor_job(self, target, *args):
        return target(*args)


class FakeResult:
    """The REAL result shape: `value`, not `data`.

    A fake with the field its author assumed is how the same bug reached
    production twice. This one carries what `AssistantJsonResult` carries.
    """

    def __init__(self, value):
        self.value = value


class FakeProvider:
    """Counts every call, because that is the claim under test.

    It deliberately carries NO `video_analysis_enabled`. The service used
    to read the opt-in off the provider with `getattr(..., False)`, and
    this fake - the only object in the repository that ever had that
    attribute - made the arrangement look correct while the real
    assistant client answered "off" for every configuration.
    """

    video_model = "fake-video-model"

    def __init__(self, answer=None, fail=False):
        self.calls = 0
        self._answer = answer
        self._fail = fail

    async def async_analyze_video(self, **kwargs):
        self.calls += 1
        if self._fail:
            raise RuntimeError("provider down")
        return FakeResult(
            self._answer
            if self._answer is not None
            else {
                "usable": True,
                "start_seconds": 2.0,
                "end_seconds": 6.0,
                "role": analysis.ROLE_HERO,
                "story_value": 5,
                "visual_quality": 4,
                "original_sound_interesting": True,
            }
        )


class FakeMediaSource:
    """Writes a file instead of downloading one. Counts fetches."""

    def __init__(self, *, broken=False):
        self.fetches = 0
        self._broken = broken

    async def async_download_to(self, window, target: Path):
        self.fetches += 1
        if self._broken:
            raise OSError("Datei nicht lesbar")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"not really a video, and never decoded here")


def _video(index=1, *, seconds=120.0):
    return {
        "id": f"vid-{index}",
        # The store normalises what it writes and needs a trip; a record
        # without one is silently dropped, which is exactly the kind of
        # invented shape a test must not be built on.
        "trip_id": "trip-1",
        "media_type": "video",
        "provider_item_id": f"item-{index}",
        "file_hash": f"hash-{index}",
        "duration_seconds": seconds,
        "width": 1920,
        "height": 1080,
        "linked_day_id": "day-1",
        "name": f"clip{index}.mp4",
    }


def _build(tmp: Path, videos, *, provider=None, media=None, cut_ok=True, enabled=True):
    store = store_module.ExperienceStore(tmp / "store")
    state = store.load("trip-1")
    state["media"] = list(videos)
    store.write(state)
    provider = provider or FakeProvider()
    service = service_module.VideoCurationService(
        FakeHass(),
        store,
        provider,
        share_root=tmp / "share",
        media_source=media or FakeMediaSource(),
        video_analysis_enabled=enabled,
    )

    # ffmpeg is not run here. The proxy arguments have their own test; what
    # matters in this file is the ORDER of the steps around it.
    async def _cut(source, target, *, start, end):
        if not cut_ok:
            raise service_module.VideoProxyError("ffmpeg fehlt")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"proxy-bytes")
        return len(b"proxy-bytes")

    service_module.async_cut_analysis_proxy = _cut
    return store, service, provider


def verify_the_offer_is_free_and_prices_the_run_first() -> None:
    """Nobody should find out the price after paying it."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        _store, service, provider = _build(tmp, [_video(1), _video(2)])
        offer = service.offer("trip-1")
        assert provider.calls == 0, "das Angebot hat den Provider aufgerufen"
        assert offer["videos_found"] == 2
        assert offer["windows_new"] > 0
        assert offer["windows_cached"] == 0
        assert offer["estimated_eur"] >= 0.0
        assert "estimated_tokens" in offer


def verify_a_run_analyses_and_stores_and_a_second_run_pays_nothing() -> None:
    """The cache is the whole reason this is affordable."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        store, service, provider = _build(tmp, [_video(1, seconds=25.0)])
        first = asyncio.run(service.async_analyze("trip-1"))
        assert first["analysed"] == 1, first
        assert first["segments"] == 1, first
        assert provider.calls == 1

        stored = store.video_analyses("trip-1")
        assert len(stored) == 1
        record = next(iter(stored.values()))
        segment = record["segments"][0]
        # Placed on the recording's timeline and carrying the real fields.
        assert segment["start_seconds"] == 2.0 and segment["end_seconds"] == 6.0
        assert segment["role"] == analysis.ROLE_HERO
        assert segment["original_sound_interesting"] is True

        second = asyncio.run(service.async_analyze("trip-1"))
        assert second["analysed"] == 0, second
        assert second["cached"] == 1, second
        assert provider.calls == 1, "eine zwischengespeicherte Antwort wurde neu bezahlt"


def verify_reading_segments_for_the_film_calls_nothing() -> None:
    """A render must never be able to produce a bill."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        _store, service, provider = _build(tmp, [_video(1, seconds=25.0)])
        asyncio.run(service.async_analyze("trip-1"))
        calls_before = provider.calls
        found = service.segments_by_day("trip-1")
        assert provider.calls == calls_before
        assert "day-1" in found and found["day-1"], found
        assert found["day-1"][0]["media_id"] == "vid-1"


def verify_a_trip_without_analyses_has_no_clips_and_that_is_normal() -> None:
    """No clips is a film, not an error."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        _store, service, provider = _build(tmp, [_video(1)])
        assert service.segments_by_day("trip-1") == {}
        assert provider.calls == 0


def verify_one_unreadable_recording_does_not_lose_the_run() -> None:
    """Ten holidays should not be lost to the eleventh file."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        _store, service, provider = _build(
            tmp, [_video(1, seconds=25.0)], media=FakeMediaSource(broken=True)
        )
        result = asyncio.run(service.async_analyze("trip-1"))
        assert result["analysed"] == 0
        assert len(result["failed"]) == 1, result
        assert result["failed"][0]["media_id"] == "vid-1"


def verify_a_refused_answer_is_stored_as_no_segment_rather_than_repaired() -> None:
    """A segment past the window is evidence, not a range to clamp."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        provider = FakeProvider(
            answer={
                "usable": True,
                "start_seconds": 2.0,
                "end_seconds": 900.0,
                "role": analysis.ROLE_HERO,
                "story_value": 5,
                "visual_quality": 5,
            }
        )
        store, service, _ = _build(tmp, [_video(1, seconds=25.0)], provider=provider)
        result = asyncio.run(service.async_analyze("trip-1"))
        assert result["segments"] == 0, result
        record = next(iter(store.video_analyses("trip-1").values()))
        assert record["segments"] == []
        # And it counts as analysed, so it is not asked again for free.
        assert result["analysed"] == 1


def verify_the_original_copy_is_always_deleted() -> None:
    """A family's footage must not survive in a working directory."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        _store, service, _ = _build(tmp, [_video(1, seconds=25.0)])
        asyncio.run(service.async_analyze("trip-1"))
        work = tmp / "share" / service_module.WORK_DIRNAME
        assert not work.exists(), "das heruntergeladene Original liegt noch da"


def verify_the_run_refuses_when_the_feature_is_switched_off() -> None:
    """Sending private footage to a cloud is switched on deliberately."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        provider = FakeProvider()
        _store, service, _ = _build(tmp, [_video(1)], provider=provider, enabled=False)
        try:
            asyncio.run(service.async_analyze("trip-1"))
        except Exception as err:  # noqa: BLE001 - the type is the module's own
            assert "ausgeschaltet" in str(err), err
            assert provider.calls == 0
            return
        raise AssertionError("die Analyse lief trotz ausgeschalteter Funktion")


def verify_no_directory_is_created_with_a_boolean_as_its_mode() -> None:
    """`Path.mkdir` takes (mode, parents, exist_ok), and that order bites.

    `mkdir(True, True)` reads like "parents, exist_ok" and means mode
    0o1 - a directory nobody may write to. It passed every local run
    because the local run was root, which ignores permission bits, and
    failed the instant CI executed it as an ordinary user.

    Checked across the integration rather than in one file: the trap is
    the signature, not the call site.
    """
    offenders = []
    for path in sorted(INTEGRATION.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for bad in (".mkdir(True", ".mkdir(False", "mkdir, True", "mkdir, False"):
            if bad in source:
                offenders.append(f"{path.name}: {bad}")
    assert not offenders, offenders


def verify_the_service_never_reaches_a_render_path() -> None:
    """Stated in code, so it cannot drift into being untrue."""
    for forbidden in ("build_scene_plan", "build_film_package", "async_submit"):
        assert forbidden not in SERVICE_SOURCE, forbidden




def verify_a_failed_window_is_still_readable_after_the_page_is_reloaded() -> None:
    """The whole point: the reason survives the connection that asked.

    Driven through the real store and read back through the free offer,
    because the bug was that the summary existed only in the return value
    of the click - and a reload has no return value.
    """
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        # A media source that cannot deliver: one recording, one failure,
        # one reason.
        class Broken:
            def __init__(self):
                self.attempts = 0

            async def async_download_to(self, record, target):
                self.attempts += 1
                raise service_module.ValidationError("Der Download blieb stehen")

        broken = Broken()
        _store, service, provider = _build(tmp, [_video(1)], media=broken)
        result = asyncio.run(service.async_analyze("trip-1"))
        assert result["analysed"] == 0, result
        assert provider.calls == 0

        # Nothing of the above is passed on: the offer reads the store.
        offer = service.offer("trip-1")
        report = offer["last_run"]
        assert report["analysed"] == 0, report
        assert report["planned"] >= 1, report
        failed = report["failed"]
        # A two-minute recording is three windows, so three windows are
        # lost - but the recording is fetched ONCE. Only successes used to
        # be remembered, so an unreachable file was fetched again for each
        # of its windows: with a download that stalls rather than refuses,
        # that is the stall timeout three times over for one bad file, and
        # a run that looks stuck while it patiently repeats itself.
        assert broken.attempts == 1, broken.attempts
        assert len(failed) >= 1, failed
        assert all("stehen" in entry["reason"] for entry in failed), failed
        assert all(entry["media_id"] for entry in failed), failed


def verify_a_second_click_does_not_pay_for_the_same_run_twice() -> None:
    """A run with no sign of life gets clicked again - so it is refused.

    The panel showed nothing at all while a run of twenty windows was
    going ("Nach einem Klick passiert ich denke nichts"), and the
    reasonable reaction to a button that appears dead is to press it
    again. Two runs would have downloaded every recording a second time
    and paid Gemini a second time for the same answers.
    """
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        _store, service, provider = _build(tmp, [_video(1), _video(2)])

        started = asyncio.Event()
        release = asyncio.Event()
        original = service._async_analyze_window

        async def _slow(*args, **kwargs):
            started.set()
            await release.wait()
            return await original(*args, **kwargs)

        service._async_analyze_window = _slow

        async def _drive():
            first = asyncio.create_task(service.async_analyze("trip-1"))
            await started.wait()
            # While that one is in flight, the trip reports itself as busy
            # - to the free offer, which is what the card reads.
            assert service.is_running("trip-1") is True
            assert service.offer("trip-1")["running"] is True
            try:
                # With a timeout, because without the guard the second run
                # would queue behind the first and this check would HANG
                # rather than fail - and a test that hangs reports nothing.
                await asyncio.wait_for(service.async_analyze("trip-1"), timeout=2)
            except asyncio.TimeoutError:
                release.set()
                raise AssertionError(
                    "ein zweiter Lauf wurde zugelassen und lief mit"
                ) from None
            except Exception as err:  # noqa: BLE001 - the module's own type
                assert "läuft bereits" in str(err), err
            else:
                raise AssertionError("ein zweiter Lauf wurde zugelassen")
            release.set()
            await first
            # And afterwards it is free again, or one failed run would
            # lock the trip out forever.
            assert service.is_running("trip-1") is False
            assert service.offer("trip-1")["running"] is False

        asyncio.run(_drive())


def verify_a_failed_run_releases_the_trip() -> None:
    """The guard is a lock, and a lock that is never released is a bug."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        _store, service, _provider = _build(tmp, [_video(1)])

        async def _boom(*args, **kwargs):
            raise RuntimeError("etwas ganz anderes")

        service._async_analyze = _boom
        try:
            asyncio.run(service.async_analyze("trip-1"))
        except RuntimeError:
            pass
        assert service.is_running("trip-1") is False


for check in (
    verify_the_offer_is_free_and_prices_the_run_first,
    verify_a_run_analyses_and_stores_and_a_second_run_pays_nothing,
    verify_reading_segments_for_the_film_calls_nothing,
    verify_a_trip_without_analyses_has_no_clips_and_that_is_normal,
    verify_one_unreadable_recording_does_not_lose_the_run,
    verify_a_refused_answer_is_stored_as_no_segment_rather_than_repaired,
    verify_the_original_copy_is_always_deleted,
    verify_the_run_refuses_when_the_feature_is_switched_off,
    verify_no_directory_is_created_with_a_boolean_as_its_mode,
    verify_the_service_never_reaches_a_render_path,
    verify_a_second_click_does_not_pay_for_the_same_run_twice,
    verify_a_failed_run_releases_the_trip,
    verify_a_failed_window_is_still_readable_after_the_page_is_reloaded,
):
    check()

print("Video curation service tests passed.")
