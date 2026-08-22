"""A finished film can still be scored after the exchange folder forgot it.

Live finding (RP-420 F2): the mux reads the SOURCE JOB'S video file out
of the renderer's result folder, and that folder is cleaned within the
hour - earlier when the disk runs low. So `story_film_add_music` answered
"Zu diesem Auftrag gibt es kein Ergebnis" on a film that was sitting
right there in the panel, and the only remedy was rendering it again:
almost two hours, for a soundtrack that takes seconds to lay on.

The library copy the player keeps IS that film. Asking the record where
it is - and putting it back where the mux looks - is what turns a
two-hour re-render into a few seconds of work. And when there is genuinely
nothing left, the message says so instead of naming an internal fact.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "roadplanner_mcp"
PACKAGE_NAME = "rp_music_recovery"

if PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(PACKAGE_ROOT)]
    sys.modules[PACKAGE_NAME] = package

for name, attributes in (
    ("homeassistant", {}),
    ("homeassistant.core", {
        "HomeAssistant": type("HomeAssistant", (), {}),
        "callback": lambda fn: fn,
    }),
    ("homeassistant.helpers", {}),
    ("homeassistant.helpers.event", {"async_call_later": None, "async_track_time_interval": None}),
    ("homeassistant.helpers.aiohttp_client", {"async_get_clientsession": lambda _hass: None}),
    ("homeassistant.util", {}),
    ("aiohttp", {
        "ClientError": type("ClientError", (Exception,), {}),
        "ClientTimeout": type("ClientTimeout", (), {"__init__": lambda self, **kw: None}),
    }),
):
    if name not in sys.modules:
        module = types.ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        sys.modules[name] = module


def load(name: str):
    full = f"{PACKAGE_NAME}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, PACKAGE_ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


player_film = load("player_film")
trip_film_export = load("trip_film_export")
roadplanner = load("roadplanner")

JOB = "11111111-2222-4333-8444-555555555555"
OTHER_JOB = "99999999-2222-4333-8444-555555555555"


class FakeHass:
    async def async_add_executor_job(self, fn, *args):
        return fn(*args)


class FakeRendererApp:
    """The exchange folder, as far as this module can see it."""

    def __init__(self, *, result=None, restorable=True) -> None:
        self._result = result
        self._restorable = restorable
        self.restored: list[tuple[str, Path]] = []

    async def async_result(self, job_id: str):
        return self._result

    async def async_restore_film_video(self, job_id: str, source: Path) -> bool:
        self.restored.append((job_id, source))
        return self._restorable and source.is_file()


def exporter(renderer_app, resolver=None):
    made = trip_film_export.TripFilmExporter(
        FakeHass(), None, None, None, renderer_app
    )
    if resolver is not None:
        made.set_film_source_resolver(resolver)
    return made


def run(coroutine):
    return asyncio.run(coroutine)


def verify_the_record_knows_the_film_after_the_exchange_forgot_it() -> None:
    with tempfile.TemporaryDirectory() as base:
        store = player_film.PlayerFilmStore(Path(base))
        store.save({
            "trip-a": {
                "latest": {
                    "job_id": JOB,
                    "url": "/api/roadplanner/trip_video_library/abc.mp4",
                    "duration_seconds": 734.5,
                    "has_music": False,
                }
            }
        })
        again = player_film.PlayerFilmStore(Path(base))
        assert again.recorded_film("trip-a")["job_id"] == JOB
        assert again.recorded_film("trip-a")["duration_seconds"] == 734.5
        assert again.recorded_film("trip-b") == {}, "one trip's film is not another's"


def verify_a_present_result_is_used_and_nothing_is_copied() -> None:
    app = FakeRendererApp(result={"video": {"duration_seconds": 612.0}})
    seconds = run(exporter(app, lambda trip_id: {}) ._async_source_film_seconds("trip-a", JOB))
    assert seconds == 612.0
    assert app.restored == [], "a film that is still there must not be copied over"


def verify_a_forgotten_result_is_restored_from_the_record() -> None:
    with tempfile.TemporaryDirectory() as base:
        film = Path(base) / "abc.mp4"
        film.write_bytes(b"film")
        app = FakeRendererApp(result=None)
        record = {"job_id": JOB, "duration_seconds": 734.5, "path": film}
        seconds = run(
            exporter(app, lambda trip_id: dict(record))._async_source_film_seconds("trip-a", JOB)
        )
        assert seconds == 734.5, "the measured length comes from the record"
        assert app.restored == [(JOB, film)], "the film is put back where the mux reads it"


def verify_a_record_for_a_different_job_is_not_used() -> None:
    with tempfile.TemporaryDirectory() as base:
        film = Path(base) / "abc.mp4"
        film.write_bytes(b"film")
        app = FakeRendererApp(result=None)
        record = {"job_id": OTHER_JOB, "duration_seconds": 154.0, "path": film}
        try:
            run(exporter(app, lambda trip_id: dict(record))._async_source_film_seconds("trip-a", JOB))
        except roadplanner.ValidationError as err:
            assert "neu gerendert" in str(err), str(err)
        else:  # pragma: no cover - the failure is the point
            raise AssertionError("another film's length was used for this job")
        assert app.restored == [], "nothing is copied for a job the record does not describe"


def verify_a_missing_file_is_not_reported_as_a_restored_film() -> None:
    with tempfile.TemporaryDirectory() as base:
        gone = Path(base) / "gone.mp4"
        app = FakeRendererApp(result=None)
        record = {"job_id": JOB, "duration_seconds": 734.5, "path": gone}
        try:
            run(exporter(app, lambda trip_id: dict(record))._async_source_film_seconds("trip-a", JOB))
        except roadplanner.ValidationError as err:
            assert "neu gerendert" in str(err), str(err)
        else:  # pragma: no cover
            raise AssertionError("a mux was started on a film that is not there")


def verify_without_a_resolver_the_old_answer_stands() -> None:
    app = FakeRendererApp(result=None)
    try:
        run(exporter(app)._async_source_film_seconds("trip-a", JOB))
    except roadplanner.ValidationError as err:
        assert "neu gerendert" in str(err), str(err)
    else:  # pragma: no cover
        raise AssertionError("a missing film must never pass silently")


def verify_a_result_without_a_measured_length_falls_back_to_the_record() -> None:
    with tempfile.TemporaryDirectory() as base:
        film = Path(base) / "abc.mp4"
        film.write_bytes(b"film")
        app = FakeRendererApp(result={"video": {}})
        record = {"job_id": JOB, "duration_seconds": 734.5, "path": film}
        seconds = run(
            exporter(app, lambda trip_id: dict(record))._async_source_film_seconds("trip-a", JOB)
        )
        assert seconds == 734.5


def verify_no_length_anywhere_is_named_rather_than_guessed() -> None:
    with tempfile.TemporaryDirectory() as base:
        film = Path(base) / "abc.mp4"
        film.write_bytes(b"film")
        app = FakeRendererApp(result={"video": {}})
        record = {"job_id": JOB, "duration_seconds": 0, "path": film}
        try:
            run(
                exporter(app, lambda trip_id: dict(record))._async_source_film_seconds("trip-a", JOB)
            )
        except roadplanner.ValidationError as err:
            assert "gemessene Filmlänge" in str(err), str(err)
        else:  # pragma: no cover
            raise AssertionError("the score was fitted to a length nobody measured")


def verify_the_mux_still_refuses_a_job_from_another_trip() -> None:
    """The recovery must not reopen the door the separation audit closed."""
    source = (PACKAGE_ROOT / "trip_film_export.py").read_text(encoding="utf-8")
    body = source.split("async def async_add_music(", 1)[1].split("\n    async def ", 1)[0]
    owns = body.index("await self._async_assert_job_belongs_to(")
    measures = body.index("await self._async_source_film_seconds(")
    assert owns < measures, "ownership is proved before the film is touched"
    restore = source.split("async def _async_restore_film_source(", 1)[1]
    restore = restore.split("\n    async def ", 1)[0]
    assert 'record.get("job_id")' in restore, "the record has to be about THIS job"


CHECKS = [
    verify_the_record_knows_the_film_after_the_exchange_forgot_it,
    verify_a_present_result_is_used_and_nothing_is_copied,
    verify_a_forgotten_result_is_restored_from_the_record,
    verify_a_record_for_a_different_job_is_not_used,
    verify_a_missing_file_is_not_reported_as_a_restored_film,
    verify_without_a_resolver_the_old_answer_stands,
    verify_a_result_without_a_measured_length_falls_back_to_the_record,
    verify_no_length_anywhere_is_named_rather_than_guessed,
    verify_the_mux_still_refuses_a_job_from_another_trip,
]


def verify_every_check_in_this_module_is_registered() -> None:
    declared = {
        name
        for name, value in globals().items()
        if name.startswith("verify_") and callable(value)
        and name != "verify_every_check_in_this_module_is_registered"
    }
    registered = {check.__name__ for check in CHECKS}
    assert declared == registered, f"not run: {sorted(declared - registered)}"


if __name__ == "__main__":
    verify_every_check_in_this_module_is_registered()
    for check in CHECKS:
        check()
        print(f"ok - {check.__name__}")
    print(f"\n{len(CHECKS)} checks passed")
