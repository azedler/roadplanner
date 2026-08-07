"""The editing run: what it costs, what it reuses, what it cannot touch.

Three of these tests exist because getting them wrong costs real money
rather than correctness:

- opening the panel must never call the provider;
- an unchanged trip must reuse the stored pass;
- typing an override must not invalidate it.

The fourth kind is about damage: the director is given no manager, so it
has no path to the roadbook at all - and this file proves the roadbook is
byte-identical after a run, which is the claim that actually matters to
somebody whose trip it is.
"""
from __future__ import annotations

import asyncio
import copy
import dataclasses
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types

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

package = types.ModuleType("director_pkg")
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules["director_pkg"] = package


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"director_pkg.{name}", PACKAGE_ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# The REAL result wrapper the provider returns. Using a hand-rolled fake
# here is what let a wrong field name ship: the service read `.data`, the
# dataclass has `.value`, and every run failed on the target system while
# every test passed. A fake whose shape I chose can only ever confirm what
# I already believed.
provider_module = load("assistant_provider")
builder_module = load("story_context_builder")
store_module = load("story_direction_store")
service_module = load("story_director_service")
roadplanner = sys.modules["director_pkg.roadplanner"]


class FakeHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


class FakeTrip:
    def __init__(self, day_count: int = 8):
        self.revision = 7
        self.days = [
            {
                "id": f"day-{number + 1}",
                "date": f"2026-07-{17 + number:02d}",
                "title": f"Tag {number + 1}",
                "distance_km": 120.0 + number,
                "drive_minutes": 90 + number,
                "details": {},
                "stops": [
                    {
                        "id": f"stop-{number}-a",
                        "name": "Krumhermsdorf Neuhäuser 40",
                        "type": "camp",
                    },
                    {"id": f"stop-{number}-b", "name": "Spielplatz am See", "type": "stop"},
                ],
            }
            for number in range(day_count)
        ]
        self.snapshot = copy.deepcopy(self.days)

    async def async_get_assistant_payload(self, trip_id):
        return {
            "selected_trip_id": trip_id,
            "summary": {
                "revision": self.revision,
                "trip": {"id": trip_id, "title": "Finnland 2026"},
            },
            "days": {"days": copy.deepcopy(self.days)},
        }

    def unchanged(self) -> bool:
        return self.days == self.snapshot


class FakeExperience:
    async def async_panel_payload(self, trip_id, days=None):
        return {"media": [], "destination_galleries": {}}


class FakeCrew:
    async def async_panel_payload(self):
        return {
            "people": [
                {"id": "person-1", "name": "Aron", "active": True, "note": "geheim"},
                {"id": "person-2", "name": "Schläfer", "active": False},
            ],
            "vehicles": [{"id": "v-1", "name": "Der Bulli", "active": True}],
        }


def FakeAnswer(value):
    """The provider's own result type, not an imitation of it."""
    return provider_module.AssistantJsonResult(value=value)


class FakeProvider:
    """Counts calls, and answers in the shape the schema asks for."""

    configured = True
    model = "gemini-test"

    def __init__(self, *, fail_after: int | None = None):
        self.calls: list[str] = []
        self._fail_after = fail_after

    async def async_generate_json_result(
        self, *, system_instruction, messages, schema, **kwargs
    ):
        is_arc = "title_variant" in (schema.get("properties") or {})
        self.calls.append("arc" if is_arc else "chapters")
        if self._fail_after is not None and len(self.calls) > self._fail_after:
            raise roadplanner.RoadplannerError("Gemini nicht erreichbar")
        payload = json.loads(messages[0]["content"])
        if is_arc:
            return FakeAnswer(
                {
                    "title_variant": "Drei Wochen Norden",
                    "subtitle": "Ein Sommer mit zu wenig Schlaf",
                    "opening": "Es begann mit einem verpassten Kaffee.",
                    "closing": "Und endete mit Sand im Auto.",
                    "motifs": ["Wasser", "lange Abende"],
                    "chapters": [
                        {
                            "chapter_id": day["chapter_id"],
                            "importance": "highlight" if index == 0 else "transition",
                            "story_role": "opening" if index == 0 else "journey",
                            "visual_style": "hero" if index == 0 else "compact",
                        }
                        for index, day in enumerate(payload["tage"])
                    ],
                }
            )
        return FakeAnswer(
            {
                "chapters": [
                    {
                        "chapter_id": day["chapter_id"],
                        "title": f"Redigiert {day['chapter_id']}",
                        "story": f"Eine warme Fassung für {day['chapter_id']}.",
                        "video_caption": f"Kurz für {day['chapter_id']}.",
                        "based_on": ["distance_km"],
                    }
                    for day in payload["tage"]
                ]
            }
        )


def make(*, day_count: int = 8, provider: FakeProvider | None = None, root: Path):
    trip = FakeTrip(day_count)
    store = store_module.StoryDirectionStore(root)
    store.initialize()
    hass = FakeHass()
    context = builder_module.StoryContextBuilder(
        hass, trip, FakeExperience(), direction_store=store, crew=FakeCrew()
    )
    service = service_module.StoryDirectorService(
        hass,
        provider=provider or FakeProvider(),
        story_context=context,
        store=store,
        crew=FakeCrew(),
    )
    return service, context, trip, store


def run(coroutine):
    return asyncio.run(coroutine)


def verify_the_answer_is_read_from_the_field_the_provider_actually_uses() -> None:
    """The bug this test exists for shipped and broke every real run.

    The service read ``.data``; ``AssistantJsonResult`` has ``.value``. So
    ``getattr(answer, "data", answer)`` fell through to the wrapper
    object, the merge saw something that was not a dict, and the run died
    - while the tests stayed green, because the fake had the shape I
    assumed rather than the shape the provider returns.

    Pinned against the real class, so the two cannot drift apart again.
    """
    fields = {field.name for field in dataclasses.fields(provider_module.AssistantJsonResult)}
    assert "value" in fields, fields
    assert "data" not in fields, "der Wrapper hat kein data - der Dienst darf es nicht lesen"
    wrapped = provider_module.AssistantJsonResult(value={"chapters": []})
    assert service_module._answer_value(wrapped) == {"chapters": []}
    # A bare dict is accepted too, so a caller may hand the answer in raw.
    assert service_module._answer_value({"chapters": []}) == {"chapters": []}
    # And something unreadable becomes None rather than itself, so the
    # merge refuses it instead of trying to iterate a wrapper.
    assert service_module._answer_value(object()) is None


def verify_an_unreadable_arc_is_reported_not_crashed() -> None:
    class BrokenProvider(FakeProvider):
        async def async_generate_json_result(self, **kwargs):
            self.calls.append("arc")
            return object()

    with tempfile.TemporaryDirectory() as tmp:
        service, context, _trip, store = make(provider=BrokenProvider(), root=Path(tmp))
        try:
            run(service.async_run("trip-1"))
        except roadplanner.ValidationError as err:
            assert "unverändert" in str(err), err
        else:
            raise AssertionError("Eine unlesbare Antwort wurde verschwiegen")
        assert store.load("trip-1") is None
        manifest = run(context.async_manifest("trip-1"))
    assert manifest["story_sources"]["composed"] == 8


def verify_a_dropped_chapter_is_asked_for_again() -> None:
    """Live report: 20 of 23 chapters edited, five calls, nothing failed.

    A batch of six can come back with five. The call succeeded and the
    schema was honoured; one day is simply not in the answer, and it then
    sits on the template text with nothing to say anything went wrong.
    The gap is silent by nature, so it has to be looked for.
    """

    class ForgetfulProvider(FakeProvider):
        """Drops one chapter from its first chapter answer, then behaves."""

        def __init__(self):
            super().__init__()
            self._dropped = False

        async def async_generate_json_result(self, **kwargs):
            answer = await super().async_generate_json_result(**kwargs)
            is_arc = "title_variant" in (kwargs["schema"].get("properties") or {})
            if is_arc or self._dropped:
                return answer
            self._dropped = True
            return FakeAnswer({"chapters": answer.value["chapters"][:-1]})

    with tempfile.TemporaryDirectory() as tmp:
        provider = ForgetfulProvider()
        service, context, _trip, _store = make(
            day_count=12, provider=provider, root=Path(tmp)
        )
        result = run(service.async_run("trip-1"))
        manifest = run(context.async_manifest("trip-1"))

    # Arc, two batches, one retry for the single dropped day.
    assert provider.calls == ["arc", "chapters", "chapters", "chapters"], provider.calls
    assert result["retried_batches"] == 1
    assert result["chapters_without_edit"] == 0
    assert result["directed_chapters"] == 12
    assert manifest["story_sources"]["directed"] == 12


def verify_a_run_that_mostly_came_back_empty_is_not_paid_for_twice() -> None:
    """A trip where nearly nothing landed has a different problem."""

    class SilentProvider(FakeProvider):
        async def async_generate_json_result(self, **kwargs):
            answer = await super().async_generate_json_result(**kwargs)
            is_arc = "title_variant" in (kwargs["schema"].get("properties") or {})
            return answer if is_arc else FakeAnswer({"chapters": []})

    with tempfile.TemporaryDirectory() as tmp:
        provider = SilentProvider()
        service, _context, _trip, _store = make(
            day_count=18, provider=provider, root=Path(tmp)
        )
        result = run(service.async_run("trip-1"))

    # Arc plus three batches. No retry: 18 missing is past the limit.
    assert provider.calls == ["arc"] + ["chapters"] * 3, provider.calls
    assert result["retried_batches"] == 0
    assert result["chapters_without_edit"] == 18


def verify_opening_the_panel_costs_nothing() -> None:
    """The status is read on every panel open. It must be free."""
    with tempfile.TemporaryDirectory() as tmp:
        provider = FakeProvider()
        service, context, _trip, _store = make(provider=provider, root=Path(tmp))
        status = run(service.async_status("trip-1"))
        # And the manifest itself, several times over.
        run(context.async_manifest("trip-1"))
        run(context.async_manifest("trip-1"))
        assert provider.calls == [], provider.calls
        assert status["has_direction"] is False
        assert status["current"] is False


def verify_a_whole_trip_is_a_handful_of_calls() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        provider = FakeProvider()
        service, _context, _trip, _store = make(
            day_count=23, provider=provider, root=Path(tmp)
        )
        result = run(service.async_run("trip-1"))
    # One arc pass plus four batches of six.
    assert provider.calls == ["arc"] + ["chapters"] * 4, provider.calls
    assert result["calls"] == 5
    assert result["directed_chapters"] == 23


def verify_an_unchanged_trip_is_not_edited_twice() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        provider = FakeProvider()
        service, _context, _trip, _store = make(provider=provider, root=Path(tmp))
        run(service.async_run("trip-1"))
        first = len(provider.calls)
        again = run(service.async_run("trip-1"))
    assert again["reused"] is True
    assert again["calls"] == 0
    assert len(provider.calls) == first, "eine unveränderte Reise darf nichts kosten"


def verify_typing_an_override_does_not_buy_a_new_edit() -> None:
    """The context hash is blind to what editing produces, on purpose."""
    with tempfile.TemporaryDirectory() as tmp:
        provider = FakeProvider()
        service, context, trip, _store = make(provider=provider, root=Path(tmp))
        run(service.async_run("trip-1"))
        spent = len(provider.calls)

        # Somebody writes a chapter by hand, exactly as the editor does.
        trip.days[0]["details"]["story_override"] = "So war es wirklich."
        trip.revision += 1
        context.invalidate("trip-1")

        status = run(service.async_status("trip-1"))
        assert status["current"] is True, "ein Override ist kein neuer Auftrag"
        again = run(service.async_run("trip-1"))
    assert again["reused"] is True
    assert len(provider.calls) == spent


def verify_real_movement_does_invalidate_the_edit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        provider = FakeProvider()
        service, context, trip, _store = make(provider=provider, root=Path(tmp))
        run(service.async_run("trip-1"))
        spent = len(provider.calls)

        trip.days.append(
            {
                "id": "day-neu",
                "date": "2026-08-01",
                "title": "Ein neuer Tag",
                "details": {},
                "stops": [{"id": "stop-neu", "name": "Oulu", "type": "city"}],
            }
        )
        trip.revision += 1
        context.invalidate("trip-1")

        status = run(service.async_status("trip-1"))
        assert status["current"] is False
        run(service.async_run("trip-1"))
    assert len(provider.calls) > spent


def verify_the_edited_text_reaches_the_manifest_and_the_roadbook_is_untouched() -> None:
    """The director is not given the manager. This proves the consequence."""
    with tempfile.TemporaryDirectory() as tmp:
        service, context, trip, _store = make(root=Path(tmp))
        run(service.async_run("trip-1"))
        manifest = run(context.async_manifest("trip-1"))

    assert trip.unchanged(), "der Roadbook-Inhalt muss unverändert sein"
    first = manifest["chapters"][0]
    assert first["story"]["source"] == "directed"
    assert first["title"] == "Redigiert day-1"
    assert first["video_caption"] == "Kurz für day-1."
    assert first["importance"] == "highlight"
    assert first["story_role"] == "opening"
    assert manifest["narrative"]["subtitle"] == "Ein Sommer mit zu wenig Schlaf"
    assert manifest["story_sources"]["directed"] == len(manifest["chapters"])
    # The canonical stop name is still the canonical stop name.
    assert first["stops"][0]["name"] == "Krumhermsdorf Neuhäuser 40"


def verify_a_person_still_beats_the_editor_after_a_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service, context, trip, _store = make(root=Path(tmp))
        run(service.async_run("trip-1"))
        trip.days[0]["details"]["story_override"] = "So war es wirklich."
        trip.days[0]["details"]["story_title_override"] = "Mein Titel"
        trip.revision += 1
        context.invalidate("trip-1")
        manifest = run(context.async_manifest("trip-1"))
    first = manifest["chapters"][0]
    assert first["story"]["source"] == "override"
    assert first["story"]["text"] == "So war es wirklich."
    assert first["title"] == "Mein Titel"
    # And the day after it still carries the edit.
    assert manifest["chapters"][1]["story"]["source"] == "directed"


def verify_one_failed_batch_does_not_lose_the_others() -> None:
    """A partly edited trip is better than an abandoned one - and visible."""
    with tempfile.TemporaryDirectory() as tmp:
        provider = FakeProvider(fail_after=2)  # arc plus one batch succeed
        service, context, _trip, _store = make(
            day_count=18, provider=provider, root=Path(tmp)
        )
        result = run(service.async_run("trip-1"))
        manifest = run(context.async_manifest("trip-1"))
    assert result["failed_batches"] == 2
    assert result["directed_chapters"] == 6
    # No retry: a batch that errored is an outage, not an omission, and
    # asking again inside the same run pays for a known-bad state.
    assert result["retried_batches"] == 0
    assert result["chapters_without_edit"] == 12
    sources = manifest["story_sources"]
    assert sources["directed"] == 6
    assert sources["composed"] == 12, sources


def verify_a_failed_arc_changes_nothing_at_all() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        provider = FakeProvider(fail_after=0)
        service, context, _trip, store = make(provider=provider, root=Path(tmp))
        try:
            run(service.async_run("trip-1"))
        except roadplanner.ValidationError as err:
            assert "unverändert" in str(err), err
        else:
            raise AssertionError("Ein gescheiterter Reisebogen wurde verschwiegen")
        manifest = run(context.async_manifest("trip-1"))
        assert store.load("trip-1") is None
    assert manifest["story_sources"]["directed"] == 0


def verify_discarding_falls_back_to_the_automatic_version() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service, context, _trip, store = make(root=Path(tmp))
        run(service.async_run("trip-1"))
        before = run(context.async_manifest("trip-1"))
        assert before["chapters"][0]["story"]["source"] == "directed"

        removed = run(service.async_discard("trip-1"))
        after = run(context.async_manifest("trip-1"))
        assert store.load("trip-1") is None
    assert removed["removed"] is True
    assert after["chapters"][0]["story"]["source"] == "composed"
    assert after["narrative"] is None


def verify_the_crew_reaches_the_story_as_names_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service, context, _trip, _store = make(root=Path(tmp))
        manifest = run(context.async_manifest("trip-1"))
        profile = run(service.async_crew_profile())
    assert manifest["crew"] == {"people": ["Aron"], "vehicle": "Der Bulli"}
    assert profile == {"people": ["Aron"], "vehicle": "Der Bulli"}
    serialised = json.dumps(manifest, ensure_ascii=False)
    for forbidden in ("person-1", "geheim", "Schläfer"):
        assert forbidden not in serialised, forbidden


def verify_a_stored_pass_survives_a_restart() -> None:
    """The whole reason it is on disk rather than in memory."""
    with tempfile.TemporaryDirectory() as tmp:
        provider = FakeProvider()
        service, _context, _trip, _store = make(provider=provider, root=Path(tmp))
        run(service.async_run("trip-1"))
        spent = len(provider.calls)

        # Everything in memory is gone; the directory is not.
        fresh_provider = FakeProvider()
        service2, context2, _trip2, _store2 = make(
            provider=fresh_provider, root=Path(tmp)
        )
        status = run(service2.async_status("trip-1"))
        manifest = run(context2.async_manifest("trip-1"))
    assert status["current"] is True
    assert fresh_provider.calls == [], "ein Neustart darf nicht neu bezahlen"
    assert manifest["chapters"][0]["story"]["source"] == "directed"
    assert spent > 0


def verify_a_corrupt_store_costs_the_story_and_not_the_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service, context, _trip, store = make(root=Path(tmp))
        run(service.async_run("trip-1"))
        path = Path(tmp) / "trips" / "trip-1" / "story_direction.json"
        path.write_text("{kaputt", encoding="utf-8")
        context.invalidate("trip-1")
        manifest = run(context.async_manifest("trip-1"))
        assert store.load("trip-1") is None
    # The trip is still fully described, just with the composed sentences.
    assert len(manifest["chapters"]) == 8
    assert manifest["story_sources"]["composed"] == 8


verify_the_answer_is_read_from_the_field_the_provider_actually_uses()
verify_an_unreadable_arc_is_reported_not_crashed()
verify_a_dropped_chapter_is_asked_for_again()
verify_a_run_that_mostly_came_back_empty_is_not_paid_for_twice()
verify_opening_the_panel_costs_nothing()
verify_a_whole_trip_is_a_handful_of_calls()
verify_an_unchanged_trip_is_not_edited_twice()
verify_typing_an_override_does_not_buy_a_new_edit()
verify_real_movement_does_invalidate_the_edit()
verify_the_edited_text_reaches_the_manifest_and_the_roadbook_is_untouched()
verify_a_person_still_beats_the_editor_after_a_run()
verify_one_failed_batch_does_not_lose_the_others()
verify_a_failed_arc_changes_nothing_at_all()
verify_discarding_falls_back_to_the_automatic_version()
verify_the_crew_reaches_the_story_as_names_only()
verify_a_stored_pass_survives_a_restart()
verify_a_corrupt_store_costs_the_story_and_not_the_trip()
print("Story director service tests passed.")
