"""The editorial write path: two fields, and a hard boundary around them.

The story editor sits on top of the roadbook, so the interesting tests are
about what it cannot reach and what it cannot damage. Three failure modes
are specific enough to name:

- a browser that loaded the trip before somebody else's edit must not win;
- removing an override must land *exactly* back on the automatic text, not
  merely close to it;
- composing the details patch in the browser would let any tab drop a
  stored summary it never loaded, because the mutation layer replaces
  details rather than merging them.
"""
from __future__ import annotations

import asyncio
import copy
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
service_module = load("story_override_service")
story = load("travel_story_manifest")
roadplanner = sys.modules["story_pkg.roadplanner"]


class FakeHass:
    pass


class FakeTrip:
    """A roadbook that behaves like the real one where it matters.

    Specifically: it checks the expected revision, it *replaces* details
    rather than merging them, and it refuses fields the day does not have.
    """

    def __init__(self):
        self.revision = 7
        self.days = [
            {
                "id": "day-1",
                "date": "2026-07-23",
                "title": "Nuuksio Nationalpark",
                "distance_km": 214.6,
                "drive_minutes": 195,
                "details": {"ai_summary": "Vom Zusammenfassungsdienst geschrieben."},
                "stops": [
                    {"id": "stop-a", "name": "Tampere", "type": "city", "arrival_time": "09:00"},
                    {"id": "stop-b", "name": "Vaasa", "type": "camp"},
                ],
            },
            {
                "id": "day-2",
                "date": "2026-07-24",
                "title": "Weiter nach Norden",
                "details": {},
                "stops": [{"id": "stop-c", "name": "Oulu", "type": "city"}],
            },
        ]
        self.writes = []

    # --- the manager surface the service uses -------------------------

    async def async_get_assistant_payload(self, trip_id):
        return {
            "selected_trip_id": trip_id,
            "summary": {
                "revision": self.revision,
                "trip": {"id": trip_id, "title": "Finnland 2026"},
            },
            "days": {"days": copy.deepcopy(self.days)},
        }

    async def async_update_day(
        self, *, day_id, patch, actor, expected_revision, expected_trip_id=None
    ):
        if expected_revision != self.revision:
            raise roadplanner.RevisionConflictError(expected_revision, self.revision)
        allowed = {"date", "title", "distance_km", "drive_minutes", "details", "notes", "status"}
        unknown = set(patch) - allowed
        if unknown:
            raise roadplanner.ValidationError(f"Nicht erlaubt: {sorted(unknown)}")
        for day in self.days:
            if day["id"] == day_id:
                # The real mutation layer REPLACES details; so does this.
                day.update(copy.deepcopy(patch))
                self.revision += 1
                self.writes.append((day_id, copy.deepcopy(patch), actor))
                return {"revision": self.revision}
        raise roadplanner.ValidationError("Reisetag nicht gefunden")


class FakeExperience:
    async def async_panel_payload(self, trip_id, days=None):
        return {"media": [], "destination_galleries": {}}


def make():
    trip = FakeTrip()
    context = builder_module.StoryContextBuilder(FakeHass(), trip, FakeExperience())
    service = service_module.StoryOverrideService(FakeHass(), trip, context)
    return service, context, trip


def run(coroutine):
    return asyncio.run(coroutine)


def _chapter(manifest, chapter_id="day-1"):
    return next(c for c in manifest["chapters"] if c["chapter_id"] == chapter_id)


def verify_saving_a_story_reaches_the_manifest() -> None:
    service, context, trip = make()
    before = run(context.async_manifest("trip-1"))
    assert _chapter(before)["story"]["source"] == story.STORY_FROM_STORED

    result = run(
        service.async_set(
            trip_id="trip-1",
            day_id="day-1",
            changes={"story": "Der Tag, an dem der Regen aufhörte."},
            expected_revision=trip.revision,
            actor="test",
        )
    )
    assert result["changed"] is True
    chapter = _chapter(result["story_manifest"])
    assert chapter["story"]["text"] == "Der Tag, an dem der Regen aufhörte."
    assert chapter["story"]["source"] == story.STORY_FROM_OVERRIDE
    story.validate_manifest(result["story_manifest"])


def verify_saving_a_title_reaches_the_manifest() -> None:
    service, _context, trip = make()
    result = run(
        service.async_set(
            trip_id="trip-1",
            day_id="day-1",
            changes={"title": "Finnland wird langsam finnisch"},
            expected_revision=trip.revision,
            actor="test",
        )
    )
    chapter = _chapter(result["story_manifest"])
    assert chapter["title"] == "Finnland wird langsam finnisch"
    assert chapter["title_overridden"] is True


def verify_removing_an_override_lands_exactly_back_on_the_automatic_version() -> None:
    """Exactly, not approximately - the content hash has to match again."""
    service, context, trip = make()
    original = run(context.async_manifest("trip-1"))
    original_hash = original["content_hash"]

    run(
        service.async_set(
            trip_id="trip-1",
            day_id="day-1",
            changes={"title": "Ein Titel", "story": "Ein Text."},
            expected_revision=trip.revision,
            actor="test",
        )
    )
    changed = run(context.async_manifest("trip-1"))
    assert changed["content_hash"] != original_hash

    restored = run(
        service.async_set(
            trip_id="trip-1",
            day_id="day-1",
            changes={"title": "", "story": None},
            expected_revision=trip.revision,
            actor="test",
        )
    )["story_manifest"]
    assert restored["content_hash"] == original_hash, "der Rückfall war nicht exakt"
    assert _chapter(restored)["story"]["source"] == story.STORY_FROM_STORED
    # The key is gone, not emptied - an empty string left behind would keep
    # the inputs subtly different forever.
    details = next(day for day in trip.days if day["id"] == "day-1")["details"]
    assert builder_module.OVERRIDE_STORY_KEY not in details
    assert builder_module.OVERRIDE_TITLE_KEY not in details


def verify_the_stored_summary_survives_an_override_and_its_removal() -> None:
    """The detail the editor never touches must not be collateral damage."""
    service, _context, trip = make()
    run(
        service.async_set(
            trip_id="trip-1",
            day_id="day-1",
            changes={"story": "Von Hand."},
            expected_revision=trip.revision,
            actor="test",
        )
    )
    run(
        service.async_set(
            trip_id="trip-1",
            day_id="day-1",
            changes={"story": ""},
            expected_revision=trip.revision,
            actor="test",
        )
    )
    details = next(day for day in trip.days if day["id"] == "day-1")["details"]
    assert details["ai_summary"] == "Vom Zusammenfassungsdienst geschrieben."


def verify_one_field_does_not_disturb_the_other() -> None:
    service, _context, trip = make()
    run(
        service.async_set(
            trip_id="trip-1",
            day_id="day-1",
            changes={"story": "Erst der Text."},
            expected_revision=trip.revision,
            actor="test",
        )
    )
    result = run(
        service.async_set(
            trip_id="trip-1",
            day_id="day-1",
            changes={"title": "Dann der Titel."},
            expected_revision=trip.revision,
            actor="test",
        )
    )
    chapter = _chapter(result["story_manifest"])
    assert chapter["story"]["text"] == "Erst der Text."
    assert chapter["title"] == "Dann der Titel."


def verify_an_older_browser_cannot_overwrite_a_newer_edit() -> None:
    """The exact race the revision check exists for."""
    service, _context, trip = make()
    stale_revision = trip.revision

    run(
        service.async_set(
            trip_id="trip-1",
            day_id="day-1",
            changes={"story": "Die neuere Bearbeitung."},
            expected_revision=trip.revision,
            actor="erster",
        )
    )
    try:
        run(
            service.async_set(
                trip_id="trip-1",
                day_id="day-1",
                changes={"story": "Der alte Browserzustand."},
                expected_revision=stale_revision,
                actor="zweiter",
            )
        )
    except roadplanner.RevisionConflictError:
        details = next(day for day in trip.days if day["id"] == "day-1")["details"]
        assert details[builder_module.OVERRIDE_STORY_KEY] == "Die neuere Bearbeitung."
        return
    raise AssertionError("Ein veralteter Browserzustand hat überschrieben")


def verify_the_roadbook_facts_are_untouched() -> None:
    service, _context, trip = make()
    before = copy.deepcopy(trip.days)
    run(
        service.async_set(
            trip_id="trip-1",
            day_id="day-1",
            changes={"title": "Neuer Titel", "story": "Neuer Text."},
            expected_revision=trip.revision,
            actor="test",
        )
    )
    after = trip.days
    for old, new in zip(before, after, strict=True):
        for field in ("id", "date", "title", "distance_km", "drive_minutes", "stops"):
            assert old.get(field) == new.get(field), f"{field} wurde verändert"
    # Only details changed, and only by the two allowed keys.
    changed_keys = set(after[0]["details"]) - set(before[0]["details"])
    assert changed_keys <= {
        builder_module.OVERRIDE_TITLE_KEY,
        builder_module.OVERRIDE_STORY_KEY,
    }


def verify_only_the_patch_the_service_built_is_written() -> None:
    """The browser never sends a details object; the service composes it."""
    service, _context, trip = make()
    run(
        service.async_set(
            trip_id="trip-1",
            day_id="day-1",
            changes={"story": "Text."},
            expected_revision=trip.revision,
            actor="test",
        )
    )
    _day_id, patch, _actor = trip.writes[-1]
    assert set(patch) == {"details"}, patch
    assert patch["details"]["ai_summary"] == "Vom Zusammenfassungsdienst geschrieben."


def verify_no_other_field_can_be_addressed() -> None:
    service, _context, trip = make()
    for forbidden in ("date", "distance_km", "stops", "details", "title_override"):
        try:
            run(
                service.async_set(
                    trip_id="trip-1",
                    day_id="day-1",
                    changes={forbidden: "irgendwas"},
                    expected_revision=trip.revision,
                    actor="test",
                )
            )
        except roadplanner.ValidationError as err:
            assert forbidden in str(err)
            continue
        raise AssertionError(f"Feld {forbidden} wurde akzeptiert")
    assert not trip.writes, "es hätte gar nicht geschrieben werden dürfen"


def verify_a_change_that_changes_nothing_costs_no_revision() -> None:
    """An empty edit must not invalidate every other browser's revision."""
    service, _context, trip = make()
    before = trip.revision
    result = run(
        service.async_set(
            trip_id="trip-1",
            day_id="day-2",
            changes={"story": ""},
            expected_revision=trip.revision,
            actor="test",
        )
    )
    assert result["changed"] is False
    assert trip.revision == before
    assert not trip.writes


def verify_a_day_from_another_trip_is_refused() -> None:
    service, _context, trip = make()
    try:
        run(
            service.async_set(
                trip_id="trip-1",
                day_id="day-fremd",
                changes={"story": "Text."},
                expected_revision=trip.revision,
                actor="test",
            )
        )
    except roadplanner.ValidationError:
        assert not trip.writes
        return
    raise AssertionError("Ein fremder Reisetag wurde akzeptiert")


def verify_a_written_story_may_have_paragraphs() -> None:
    service, _context, trip = make()
    result = run(
        service.async_set(
            trip_id="trip-1",
            day_id="day-1",
            changes={"story": "Erster Absatz.\r\n\r\n\r\n\r\nZweiter   Absatz."},
            expected_revision=trip.revision,
            actor="test",
        )
    )
    assert _chapter(result["story_manifest"])["story"]["text"] == (
        "Erster Absatz.\n\nZweiter Absatz."
    )


def verify_nothing_is_stored_but_the_two_keys() -> None:
    """No second copy of the manifest, anywhere."""
    service, context, trip = make()
    run(
        service.async_set(
            trip_id="trip-1",
            day_id="day-1",
            changes={"story": "Text."},
            expected_revision=trip.revision,
            actor="test",
        )
    )
    stored = str(trip.days)
    for leaked in ("content_hash", "manifest_version", "roadplanner.travel_story"):
        assert leaked not in stored, f"{leaked} wurde persistiert"
    # And the description still comes from a rebuild.
    assert run(context.async_manifest("trip-1"))["content_hash"]


verify_saving_a_story_reaches_the_manifest()
verify_saving_a_title_reaches_the_manifest()
verify_removing_an_override_lands_exactly_back_on_the_automatic_version()
verify_the_stored_summary_survives_an_override_and_its_removal()
verify_one_field_does_not_disturb_the_other()
verify_an_older_browser_cannot_overwrite_a_newer_edit()
verify_the_roadbook_facts_are_untouched()
verify_only_the_patch_the_service_built_is_written()
verify_no_other_field_can_be_addressed()
verify_a_change_that_changes_nothing_costs_no_revision()
verify_a_day_from_another_trip_is_refused()
verify_a_written_story_may_have_paragraphs()
verify_nothing_is_stored_but_the_two_keys()
print("Story override service tests passed.")
