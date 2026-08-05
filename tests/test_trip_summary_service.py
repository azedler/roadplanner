"""Behavioral tests for generating and STORING the trip's written summaries.

Fakes stand in for Gemini, the trip store and the crew registry, but the
real service code runs: what gets sent, what gets written, and what happens
when a single call fails are all exercised end to end.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_NAME = "roadplanner_summary_test_pkg"
PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
aiohttp_stub.ClientTimeout = lambda *args, **kwargs: None
sys.modules["aiohttp"] = aiohttp_stub

ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object
homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules["homeassistant"] = homeassistant
sys.modules["homeassistant.core"] = ha_core
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers.__path__ = []
sys.modules["homeassistant.helpers"] = ha_helpers
ha_aiohttp = types.ModuleType("homeassistant.helpers.aiohttp_client")
ha_aiohttp.async_get_clientsession = lambda *a, **k: None
sys.modules["homeassistant.helpers.aiohttp_client"] = ha_aiohttp


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


for optional in ("bounded_json", "json_io", "identifiers", "json_tree_validation"):
    try:
        load(optional)
    except FileNotFoundError:
        pass
load("canonical_day")
service_module = load("trip_summary_service")
RoadplannerError = sys.modules[f"{PACKAGE_NAME}.roadplanner"].RoadplannerError


class FakeHass:
    def async_create_task(self, coro):
        return asyncio.ensure_future(coro)

    async def async_add_executor_job(self, func, *args):
        return func(*args)


class FakeResult:
    def __init__(self, text="", data=None):
        self.text = text
        self.data = data


class FakeProvider:
    """Answers vision and text calls, and records exactly what it was asked."""

    configured = True

    def __init__(self, *, vision_error_on=(), text=""):
        self.vision_calls = []
        self.text_calls = []
        self._vision_error_on = set(vision_error_on)
        self._text = text

    async def async_analyze_images(self, *, system_instruction, prompt, images, schema, max_output_tokens):
        self.vision_calls.append(
            {"prompt": prompt, "images": images, "system": system_instruction}
        )
        if len(self.vision_calls) in self._vision_error_on:
            raise RoadplannerError("Vision kaputt")
        return FakeResult(data={"summary": f"Vision-Text {len(self.vision_calls)}"})

    async def async_generate_text(self, *, system_instruction, messages, enable_search, max_output_tokens, temperature):
        self.text_calls.append({"system": system_instruction, "messages": messages})
        return FakeResult(text=self._text or f"Text {len(self.text_calls)}")


class FakeManager:
    def __init__(self, days, trip=None):
        self._days = days
        self._trip = trip or {"id": "trip-1", "title": "Testreise", "details": {}}
        self.revision = 7
        self.day_patches = []
        self.trip_patches = []

    async def async_get_assistant_payload(self, trip_id):
        return {
            "summary": {
                "revision": self.revision,
                "trip": self._trip,
                "total_distance_km": 5279.3,
                "country_count": 7,
            },
            "days": {"days": self._days},
        }

    async def async_update_day(self, *, day_id, patch, actor, expected_revision, expected_trip_id=None):
        assert expected_revision == self.revision, (
            f"stale revision {expected_revision} against {self.revision}"
        )
        self.revision += 1
        self.day_patches.append({"day_id": day_id, "patch": patch})
        for day in self._days:
            if day.get("id") == day_id:
                day["details"] = patch["details"]
        return {"changed": True, "revision": self.revision}

    async def async_update_trip(self, *, patch, actor, expected_revision, expected_trip_id=None):
        assert expected_revision == self.revision
        self.revision += 1
        self.trip_patches.append(patch)
        self._trip["details"] = patch["details"]
        return {"changed": True, "revision": self.revision}


class FakeExperience:
    def __init__(self, media):
        self._media = media

    async def async_panel_payload(self, trip_id, days=None):
        return {"media": self._media}


class FakeCrew:
    def __init__(self, people):
        self._people = people
        self.patches = []

    async def async_panel_payload(self):
        return {"people": self._people}

    async def async_update_person(self, *, person_id, patch):
        self.patches.append({"person_id": person_id, "patch": patch})
        return {"id": person_id, **patch}


JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes"


def _service(manager, experience, crew, provider, photos_per_day=2):
    svc = service_module.TripSummaryService(
        FakeHass(), manager, experience, crew, provider
    )

    async def fake_photos(session, trip_id, day, by_stop, by_day, *, limit):
        return [JPEG] * min(photos_per_day, limit)

    svc._async_day_photos = fake_photos

    async def fake_reference(session, trip_id, person):
        return JPEG if person.get("reference_media_id") else None

    svc._async_reference_photo = fake_reference
    return svc


def _days():
    return [
        {"id": "day-1", "title": "Anreise", "date": "2026-07-17", "stops": [], "details": {"keep": 1}},
        {"id": "day-2", "title": "Masuren", "date": "2026-07-18", "stops": [], "details": {}},
    ]


def verify_days_get_photos_and_facts_and_are_stored() -> None:
    """Live answer: the day text comes from photos AND the plan data."""
    days = _days()
    manager = FakeManager(days)
    provider = FakeProvider()
    svc = _service(manager, FakeExperience([]), FakeCrew([]), provider)

    asyncio.run(svc.async_generate("trip-1", scope="days"))

    assert len(provider.vision_calls) == 2, "one vision call per day"
    first = provider.vision_calls[0]
    # The PLAN facts travel with the photos - photos alone cannot know a
    # place name, facts alone cannot know what was experienced.
    assert "Anreise" in first["prompt"]
    assert "2026-07-17" in first["prompt"]
    assert len(first["images"]) == 2

    assert len(manager.day_patches) == 2
    stored = manager.day_patches[0]["patch"]["details"]
    assert stored[service_module.SUMMARY_DETAIL_KEY] == "Vision-Text 1"
    assert stored[f"{service_module.SUMMARY_DETAIL_KEY}_generated_at"]
    # Existing detail keys must survive - details is replaced wholesale by
    # the store, so the service has to read-modify-write.
    assert stored["keep"] == 1, stored


def verify_a_day_without_photos_falls_back_to_the_careful_prompt() -> None:
    days = _days()
    manager = FakeManager(days)
    provider = FakeProvider()
    svc = _service(manager, FakeExperience([]), FakeCrew([]), provider, photos_per_day=0)

    asyncio.run(svc.async_generate("trip-1", scope="days"))

    assert not provider.vision_calls, "no photos, no vision call"
    assert len(provider.text_calls) == 2
    system = provider.text_calls[0]["system"]
    assert "keine Fotos" in system, "a photo-less day must not claim experiences"


def verify_one_failing_day_does_not_sink_the_run() -> None:
    days = _days()
    manager = FakeManager(days)
    # The first vision call fails; the day falls back to text, the second
    # day is unaffected.
    provider = FakeProvider(vision_error_on=(1,))
    svc = _service(manager, FakeExperience([]), FakeCrew([]), provider)

    asyncio.run(svc.async_generate("trip-1", scope="days"))

    assert len(manager.day_patches) == 2, "both days must still be written"
    assert provider.text_calls, "the failed day fell back to the text prompt"


def verify_the_trip_foreword_is_built_from_the_written_days() -> None:
    days = _days()
    manager = FakeManager(days)
    provider = FakeProvider()
    svc = _service(manager, FakeExperience([]), FakeCrew([]), provider)

    asyncio.run(svc.async_generate("trip-1", scope="all"))

    assert manager.trip_patches, "the trip summary must be stored"
    content = provider.text_calls[-1]["messages"][0]["content"]
    assert "Vision-Text 1" in content, "the foreword sees the day summaries"
    assert "5279 km" in content
    stored = manager.trip_patches[-1]["details"][service_module.SUMMARY_DETAIL_KEY]
    assert stored.startswith("Text ")


def verify_a_person_is_recognised_via_the_reference_portrait() -> None:
    people = [
        {"id": "p1", "name": "Peer", "reference_media_id": "m1", "active": True},
        {"id": "p2", "name": "Ohne Foto", "active": True},
        {"id": "p3", "name": "Inaktiv", "reference_media_id": "m2", "active": False},
    ]
    crew = FakeCrew(people)
    manager = FakeManager(_days())
    provider = FakeProvider()
    svc = _service(manager, FakeExperience([]), crew, provider)

    asyncio.run(svc.async_generate("trip-1", scope="people"))

    # Only the person WITH a reference portrait can be told apart.
    vision = [call for call in provider.vision_calls if "Peer" in call["prompt"]]
    assert vision, "the person with a reference photo gets a vision call"
    assert vision[0]["images"][0].image_id == "referenz"
    assert "Peer" in vision[0]["images"][0].label

    stored = {patch["person_id"] for patch in crew.patches}
    assert "p1" in stored
    assert "p3" not in stored, "an inactive crew member is skipped"
    for patch in crew.patches:
        assert patch["patch"]["summary"]
        assert patch["patch"]["summary_generated_at"]


def verify_a_missing_provider_is_refused_up_front() -> None:
    svc = service_module.TripSummaryService(
        FakeHass(), FakeManager(_days()), FakeExperience([]), FakeCrew([]), None
    )
    try:
        svc.async_start("trip-1")
    except RoadplannerError as err:
        assert "Reisebegleiter" in str(err)
    else:
        raise AssertionError("no provider must fail fast, not half-generate")


def verify_a_cancelled_run_does_not_spin_forever() -> None:
    svc = service_module.TripSummaryService(
        FakeHass(), FakeManager(_days()), FakeExperience([]), FakeCrew([]), FakeProvider()
    )
    svc._status = {"state": "running"}

    async def cancelled(trip_id, scope="all"):
        raise asyncio.CancelledError

    svc.async_generate = cancelled
    try:
        asyncio.run(svc._async_run("trip-1", "all"))
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("cancellation must still propagate")
    assert svc._status["state"] == "error"
    assert "abgebrochen" in svc._status["error"].casefold()


verify_days_get_photos_and_facts_and_are_stored()
verify_a_day_without_photos_falls_back_to_the_careful_prompt()
verify_one_failing_day_does_not_sink_the_run()
verify_the_trip_foreword_is_built_from_the_written_days()
verify_a_person_is_recognised_via_the_reference_portrait()
verify_a_missing_provider_is_refused_up_front()
verify_a_cancelled_run_does_not_spin_forever()
print("Trip summary service tests passed.")
