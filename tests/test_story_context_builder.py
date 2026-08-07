"""The builder reads a real trip and must not do anything else.

Two things are easy to lose here and both are load-bearing. The first is
that this stays a *reader*: the moment it generates a summary or fetches a
photo, the manifest stops being deterministic and starts costing money.
The second is that its output must not depend on the order a store happens
to return records in - which is exactly the kind of thing that works for a
year and then changes with a storage backend.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types

# Loading integration modules by path would otherwise leave a
# __pycache__ inside the shipped integration directory, which the
# repository validator rightly refuses.
sys.dont_write_bytecode = True

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")


def _install_homeassistant_stub() -> None:
    """The builder imports HomeAssistant for typing only; stub it."""
    if "homeassistant" in sys.modules:
        return
    homeassistant = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:  # noqa: D401 - stand-in
        """Stand-in for the real class."""

    core.HomeAssistant = HomeAssistant
    helpers = types.ModuleType("homeassistant.helpers")
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.core": core,
            "homeassistant.helpers": helpers,
        }
    )


_install_homeassistant_stub()

# The builder imports its siblings relatively, so it needs a package to
# live in. Pointing __path__ at the real directory loads the real modules -
# the point is to test against them, not against stand-ins.
package = types.ModuleType("story_pkg")
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules["story_pkg"] = package


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"story_pkg.{name}", PACKAGE_ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder_module = load("story_context_builder")
story = load("travel_story_manifest")
roadplanner = sys.modules["story_pkg.roadplanner"]


class FakeHass:
    pass


def _day(number: int, **overrides):
    day = {
        "id": f"day-{number}",
        "date": f"2026-07-{16 + number:02d}",
        "title": f"Etappe {number}",
        "distance_km": 100.0 + number,
        "drive_minutes": 60 + number,
        "details": {},
        "stops": [
            {"id": f"stop-{number}a", "name": f"Ort {number}A", "type": "city", "arrival_time": "09:00"},
            {"id": f"stop-{number}b", "name": f"Ort {number}B", "type": "camp"},
        ],
    }
    day.update(overrides)
    return day


# Photos taken within four seconds of each other count as one burst and
# only the strongest survives the curation - correct behaviour, and a trap
# for fixtures. Every fake photo therefore gets its own minute.
_MINUTE = iter(range(10, 59))


def _media(identifier: str, **overrides):
    item = {
        "id": identifier,
        "taken_at": f"2026-07-17T10:{next(_MINUTE):02d}:00Z",
        "linked_day_id": "day-1",
        "linked_stop_id": None,
        "caption": "",
        "media_type": "image",
        "is_cover": False,
        "is_day_cover": False,
        "is_trip_cover": False,
    }
    item.update(overrides)
    return item


class FakeManager:
    def __init__(self, days, revision=7):
        self.days = days
        self.revision = revision
        self.calls = 0

    async def async_get_assistant_payload(self, trip_id):
        self.calls += 1
        return {
            "selected_trip_id": trip_id,
            "summary": {
                "revision": self.revision,
                "trip": {
                    "id": trip_id,
                    "title": "Finnland 2026",
                    "start_date": "2026-07-17",
                    "end_date": "2026-08-06",
                    "total_distance_km": 2140.5,
                },
            },
            "days": {"days": self.days},
        }


class FakeExperience:
    def __init__(self, media=None, fail=False):
        self.media = media or []
        self.fail = fail
        self.calls = 0

    async def async_panel_payload(self, trip_id, days=None):
        self.calls += 1
        if self.fail:
            raise roadplanner.RoadplannerError("Sidecar nicht verfügbar")
        return {"media": self.media, "destination_galleries": {}}


def make(days=None, media=None, fail=False, revision=7):
    manager = FakeManager(days if days is not None else [_day(1), _day(2)], revision)
    experience = FakeExperience(media, fail)
    return builder_module.StoryContextBuilder(FakeHass(), manager, experience), manager, experience


def run(coroutine):
    return asyncio.run(coroutine)


def verify_a_trip_becomes_a_valid_manifest() -> None:
    builder, _manager, _experience = make(media=[_media("media-1"), _media("media-2")])
    manifest = run(builder.async_manifest("trip-1"))
    story.validate_manifest(manifest)
    assert manifest["trip"]["trip_id"] == "trip-1"
    assert manifest["trip"]["title"] == "Finnland 2026"
    assert manifest["source_revision"] == 7
    assert [chapter["chapter_id"] for chapter in manifest["chapters"]] == ["day-1", "day-2"]
    assert manifest["facts"]["chapter_count"] == 2
    assert manifest["facts"]["distance_km"] == 2140.5


def verify_the_media_order_from_the_store_does_not_change_the_result() -> None:
    """A storage backend that returns records differently must not matter."""
    items = [
        _media("media-3"),
        _media("media-1"),
        _media("media-2"),
    ]
    forwards, _m, _e = make(media=list(items))
    backwards, _m2, _e2 = make(media=list(reversed(items)))
    assert (
        run(forwards.async_manifest("trip-1"))["content_hash"]
        == run(backwards.async_manifest("trip-1"))["content_hash"]
    )


def verify_the_manifest_is_cached_until_the_trip_moves() -> None:
    builder, manager, _experience = make()
    first = run(builder.async_manifest("trip-1"))
    second = run(builder.async_manifest("trip-1"))
    assert manager.calls == 2, "die Revision wird jedes Mal gelesen"
    assert first is second, "bei gleicher Revision wird nicht neu gebaut"

    manager.revision = 8
    third = run(builder.async_manifest("trip-1"))
    assert third is not first
    assert third["source_revision"] == 8


def verify_a_forced_rebuild_and_an_invalidation_both_work() -> None:
    builder, _manager, _experience = make()
    first = run(builder.async_manifest("trip-1"))
    assert run(builder.async_manifest("trip-1", force=True)) is not first
    builder.invalidate("trip-1")
    assert run(builder.async_manifest("trip-1")) is not first
    # Same trip, unchanged: a rebuild has to produce the same description.
    assert run(builder.async_manifest("trip-1"))["content_hash"] == first["content_hash"]


def verify_overrides_on_a_day_reach_the_chapter() -> None:
    day = _day(1, details={
        builder_module.OVERRIDE_TITLE_KEY: "Der Tag der Elche",
        builder_module.OVERRIDE_STORY_KEY: "So war es wirklich.",
    })
    builder, _manager, _experience = make(days=[day])
    chapter = run(builder.async_manifest("trip-1"))["chapters"][0]
    assert chapter["title"] == "Der Tag der Elche"
    assert chapter["title_overridden"] is True
    assert chapter["story"]["text"] == "So war es wirklich."
    assert chapter["story"]["source"] == story.STORY_FROM_OVERRIDE


def verify_a_stored_summary_is_used_before_anything_is_composed() -> None:
    day = _day(1, details={"ai_summary": "Ein langer Fahrtag am See entlang."})
    builder, _manager, _experience = make(days=[day])
    chapter = run(builder.async_manifest("trip-1"))["chapters"][0]
    assert chapter["story"]["source"] == story.STORY_FROM_STORED
    assert chapter["story"]["text"] == "Ein langer Fahrtag am See entlang."


def verify_a_trip_without_the_media_sidecar_still_has_a_story() -> None:
    """The description must not depend on an optional subsystem."""
    builder, _manager, experience = make(fail=True)
    manifest = run(builder.async_manifest("trip-1"))
    story.validate_manifest(manifest)
    assert experience.calls == 1
    assert manifest["facts"]["media_count"] == 0
    assert manifest["chapters"][0]["story"]["source"] == story.STORY_FROM_COMPOSED


def verify_cover_flags_become_roles() -> None:
    builder, _manager, _experience = make(
        media=[
            _media("media-1", is_trip_cover=True),
            _media("media-2", is_day_cover=True),
            _media("media-3", is_cover=True, linked_stop_id="stop-1a"),
            _media("media-4"),
        ]
    )
    chapter = run(builder.async_manifest("trip-1"))["chapters"][0]
    roles = {item["media_id"]: item["role"] for item in chapter["media"]}
    assert roles["media-1"] == story.MEDIA_ROLE_TRIP_COVER
    assert roles["media-2"] == story.MEDIA_ROLE_DAY_COVER
    assert roles["media-3"] == story.MEDIA_ROLE_STOP_COVER
    assert roles["media-4"] == story.MEDIA_ROLE_HIGHLIGHT


def verify_the_trip_cover_is_named_at_the_top() -> None:
    builder, _manager, _experience = make(media=[_media("media-9", is_trip_cover=True)])
    manifest = run(builder.async_manifest("trip-1"))
    assert manifest["trip"]["cover_media_id"] == "media-9"


def verify_the_photo_count_is_the_days_own_not_the_chapters() -> None:
    """A renderer needs both numbers and they are different facts."""
    many = [_media(f"media-{number}") for number in range(12)]
    builder, _manager, _experience = make(media=many)
    chapter = run(builder.async_manifest("trip-1"))["chapters"][0]
    assert chapter["facts"]["photo_count"] == 12
    assert len(chapter["media"]) <= builder_module.MEDIA_PER_CHAPTER


def verify_captions_come_from_media_that_have_one() -> None:
    builder, _manager, _experience = make(
        media=[_media("media-1", caption="Die Elchbrücke"), _media("media-2")]
    )
    chapter = run(builder.async_manifest("trip-1"))["chapters"][0]
    assert chapter["captions"] == [{"media_id": "media-1", "text": "Die Elchbrücke"}]


def verify_a_map_hint_reports_coordinates_without_carrying_them() -> None:
    day = _day(1)
    day["stops"][0]["location"] = {"lat": 60.29, "lon": 24.51}
    builder, _manager, _experience = make(days=[day])
    hint = run(builder.async_manifest("trip-1"))["chapters"][0]["map_hint"]
    assert hint["has_coordinates"] is True
    assert hint["stop_ids"] == ["stop-1a", "stop-1b"]
    assert "lat" not in str(hint)


def verify_nothing_is_generated_or_downloaded() -> None:
    """The fakes offer no provider and no session on purpose.

    If the builder ever reached for one, this would raise instead of
    quietly making the manifest non-deterministic and chargeable.
    """
    builder, manager, experience = make(media=[_media("media-1")])
    run(builder.async_manifest("trip-1"))
    assert not hasattr(manager, "provider")
    assert not hasattr(experience, "async_media_redirect_url")
    source = (PACKAGE_ROOT / "story_context_builder.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    for forbidden in (
        "async_generate_text",
        "async_media_redirect_url",
        "async_get_clientsession",
        "async_download_photo",
    ):
        assert forbidden not in code, f"der Builder darf {forbidden} nicht benutzen"


def verify_an_unknown_trip_is_refused() -> None:
    builder, _manager, _experience = make()
    for missing in ("", "   "):
        try:
            run(builder.async_manifest(missing))
        except roadplanner.ValidationError:
            continue
        raise AssertionError(f"Leere Reise-ID akzeptiert: {missing!r}")


verify_a_trip_becomes_a_valid_manifest()
verify_the_media_order_from_the_store_does_not_change_the_result()
verify_the_manifest_is_cached_until_the_trip_moves()
verify_a_forced_rebuild_and_an_invalidation_both_work()
verify_overrides_on_a_day_reach_the_chapter()
verify_a_stored_summary_is_used_before_anything_is_composed()
verify_a_trip_without_the_media_sidecar_still_has_a_story()
verify_cover_flags_become_roles()
verify_the_trip_cover_is_named_at_the_top()
verify_the_photo_count_is_the_days_own_not_the_chapters()
verify_captions_come_from_media_that_have_one()
verify_a_map_hint_reports_coordinates_without_carrying_them()
verify_nothing_is_generated_or_downloaded()
verify_an_unknown_trip_is_refused()
print("Story context builder tests passed.")
