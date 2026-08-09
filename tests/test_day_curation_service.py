"""The wiring, which is where a rewrite like this actually breaks.

`media_curation` is arithmetic and easy to be sure about. The service
around it is where the interesting failures live: a cache key that never
matches so every render pays again, an exclusion that reaches the
provider anyway, a day that produces nothing at all when the model is
switched off.

So this drives the real `DayCurationService` against a fake provider
that records what it was asked. Nothing here reaches a network, and the
fake returning fixed answers is the point: what is under test is the
plumbing, not Gemini.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True


def _module(name: str, **attributes) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules.setdefault(name, module)
    return module


_module("homeassistant").__path__ = []
_module("homeassistant.util").__path__ = []
_module(
    "homeassistant.util.dt",
    parse_datetime=lambda text: None,
    as_local=lambda value: value,
    utcnow=lambda: None,
)
_module("homeassistant.core", HomeAssistant=object, callback=lambda fn: fn)
_module("homeassistant.config_entries", ConfigEntry=object)
_module("homeassistant.exceptions", HomeAssistantError=type("E", (Exception,), {}))
_module("homeassistant.helpers").__path__ = []
_module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: None)
_module("homeassistant.helpers.storage", Store=object)
_module("homeassistant.helpers.event", async_track_time_interval=lambda *a, **k: None)
_module(
    "aiohttp",
    ClientError=type("ClientError", (Exception,), {}),
    ClientSession=object,
    ClientTimeout=lambda *a, **k: None,
)

_PACKAGE = "roadplanner_service_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(ROOT / "custom_components" / "roadplanner_mcp")]
sys.modules[_PACKAGE] = _root

service_module = importlib.import_module(f"{_PACKAGE}.day_curation_service")
store_module = importlib.import_module(f"{_PACKAGE}.experience_store")


class _Hass:
    """Home Assistant's executor, minus Home Assistant."""

    async def async_add_executor_job(self, target, *args):
        return target(*args)


class _Manager:
    def __init__(self, days):
        self._days = days

    async def async_get_assistant_payload(self, trip_id):
        return {"days": self._days}


class _Result:
    def __init__(self, value):
        self.value = value
        self.model_version = "fake"
        self.usage = {}


class _Provider:
    """Records what it was shown and answers from a fixed table."""

    def __init__(self, table):
        self.table = table
        self.calls = 0
        self.seen_ids: list[str] = []
        self.prompts: list[str] = []

    async def async_analyze_images(self, *, system_instruction, prompt, images, schema, max_output_tokens):
        self.calls += 1
        self.prompts.append(prompt)
        self.seen_ids.extend(item.image_id for item in images)
        return _Result(
            {
                "photos": [
                    {"image_id": item.image_id, **self.table.get(item.image_id, {})}
                    for item in images
                ]
            }
        )


class _Image:
    def __init__(self, image_id):
        self.image_id = image_id
        self.data = b"x" * 100


class _Vision:
    def __init__(self, provider, *, enabled=True):
        self.provider = provider
        self.vision_enabled = enabled and provider is not None
        self.media_vision_daily_limit = 5

    async def async_curation_images(self, candidates):
        return [_Image(str(item.get("id"))) for item in candidates]


def _photo(media_id: str, *, seconds: int, **extra):
    base = {
        "id": media_id,
        "trip_id": "trip-1",
        "provider_item_id": f"item-{media_id}",
        "name": f"{media_id}.jpg",
        "media_type": "photo",
        "assignment_status": "automatic",
        "linked_day_id": "day-1",
        "file_hash": f"hash-{media_id}",
        "taken_at": f"2026-07-21T14:{seconds // 60:02d}:{seconds % 60:02d}+02:00",
        "location": {"latitude": 63.4, "longitude": 18.4},
        "width": 4032,
        "height": 3024,
        "size_bytes": 3_000_000,
    }
    base.update(extra)
    return base


DAY = {
    "id": "day-1",
    "date": "2026-07-21",
    "title": "Tag im Elchpark",
    "stops": [{"id": "s1", "name": "Elchpark Smaland", "type": "sight"}],
}

TABLE = {
    **{
        f"moose-{index}": {
            "motifs": ["Elch", "Wald"],
            "visual_quality": 3,
            "story_value": 3,
            "shows": ["elch"],
        }
        for index in range(6)
    },
    "carpark": {"motifs": ["Parkplatz"], "visual_quality": 5, "story_value": 5},
    "lake": {"motifs": ["See"], "visual_quality": 4, "story_value": 4},
}


def _service(media, *, provider=None, enabled=True):
    directory = tempfile.mkdtemp()
    store = store_module.ExperienceStore(Path(directory))
    store.initialize()
    state = store.load("trip-1")
    state["media"] = [store_module.normalize_media(item) for item in media]
    store.write(state)
    vision = _Vision(provider, enabled=enabled)
    return (
        service_module.DayCurationService(_Hass(), store, _Manager([DAY]), vision),
        store,
    )


def _media_set():
    return [_photo(f"moose-{index}", seconds=index * 2) for index in range(6)] + [
        _photo("carpark", seconds=1_800),
        _photo("lake", seconds=3_600),
    ]


def verify_the_moose_reaches_the_film() -> None:
    """End to end, on the shape of the day that started all of this.

    Six attempts at an animal and two prettier photographs of other
    things. The old pipeline kept one moose at random and then ranked it
    below a sharp picture of gravel. This asserts the whole chain: the
    series survives, the pool is wide, the model is asked, the day's
    subject wins.
    """
    provider = _Provider(TABLE)
    service, store = _service(_media_set(), provider=provider)
    result = asyncio.run(service.async_curate_trip("trip-1"))
    day = result["days"][0]

    assert day["brief"]["must_cover"] == ["elch"], day["brief"]
    assert day["coverage"]["complete"], day["coverage"]
    assert any(media_id.startswith("moose") for media_id in day["media_ids"]), day
    # The carpark photograph scores highest and is still not what the day
    # is about, so it may be there - after the moose.
    first_moose = next(
        index for index, value in enumerate(day["media_ids"]) if value.startswith("moose")
    )
    assert first_moose == 0, day["media_ids"]

    # Every one of the six was compared rather than five thrown away.
    assert len([value for value in provider.seen_ids if value.startswith("moose")]) >= 2
    stored = store.load("trip-1")["day_curations"]["day-1"]
    assert stored["media_ids"] == day["media_ids"]


def verify_an_unchanged_day_is_never_paid_for_twice() -> None:
    """The cache key is the pool's own hashes, so a second run is free."""
    provider = _Provider(TABLE)
    service, _store = _service(_media_set(), provider=provider)
    asyncio.run(service.async_curate_trip("trip-1"))
    first = provider.calls
    assert first >= 1
    second = asyncio.run(service.async_curate_trip("trip-1"))
    assert provider.calls == first, "ein zweiter Lauf darf nichts kosten"
    assert second["days"][0]["note"] == "unverändert, keine neue Analyse"
    assert second["days"][0]["coverage"]["complete"], second

    # ...and forcing it does ask again, because that is what force means.
    asyncio.run(service.async_curate_trip("trip-1", force=True))
    assert provider.calls > first


def verify_a_day_still_works_with_the_model_switched_off() -> None:
    """No key, no money, no analyses - and still a usable day.

    A film with weaker pictures is better than a film with none, and
    "the paid feature is off" must never mean "this day is empty".
    """
    service, _store = _service(_media_set(), provider=None, enabled=False)
    result = asyncio.run(service.async_curate_trip("trip-1"))
    day = result["days"][0]
    assert day["media_ids"], day
    assert day["analysed_count"] == 0
    assert day["coverage"]["unmet"] == ["elch"], "ohne Analyse ist nichts belegt"
    assert "ausgeschaltet" in day["note"]


def verify_an_excluded_photo_never_reaches_the_provider() -> None:
    """A decision against a picture is also a decision against paying for it."""
    media = _media_set()
    for item in media:
        if item["id"] == "moose-0":
            item["film_pin"] = "exclude"
        if item["id"] == "carpark":
            item["film_pin"] = "show"
    provider = _Provider(TABLE)
    service, _store = _service(media, provider=provider)
    day = asyncio.run(service.async_curate_trip("trip-1"))["days"][0]

    assert "moose-0" not in provider.seen_ids, provider.seen_ids
    assert "moose-0" not in day["media_ids"], day
    # A pin is honoured and comes first, however the model scored it.
    assert day["media_ids"][0] == "carpark", day["media_ids"]
    assert day["reasons"]["carpark"] == "vom Nutzer ausgewählt"


def verify_the_day_is_never_named_to_the_model() -> None:
    """The terms go out as a question; the day does not go out at all."""
    provider = _Provider(TABLE)
    service, _store = _service(_media_set(), provider=provider)
    asyncio.run(service.async_curate_trip("trip-1"))
    blob = " ".join(provider.prompts)
    assert "elch" in blob, "die Begriffe sind die Frage"
    for leak in ("day-1", "2026-07-21", "Smaland", "Elchpark"):
        assert leak not in blob, leak


def verify_a_long_trip_is_curated_in_batches() -> None:
    """The first real run said "Connection lost" and kept going invisibly.

    A three-week trip is roughly one model call per day. Done in a single
    panel action that is minutes long, the websocket gives up while the
    work carries on behind it - so a call pays for a few days and says
    how many are left.

    `remaining` counts days that would COST something, not days that
    exist: a cached day is recomputed for free, and counting those would
    leave a progress loop stuck at a number that never falls.
    """
    days = [
        {**DAY, "id": f"day-{index}", "title": "Tag im Elchpark"}
        for index in range(1, 7)
    ]
    media = []
    for index in range(1, 7):
        for entry in _media_set():
            media.append({**entry, "id": f"d{index}-{entry['id']}",
                          "provider_item_id": f"item-d{index}-{entry['id']}",
                          "file_hash": f"hash-d{index}-{entry['id']}",
                          "linked_day_id": f"day-{index}"})
    table = {
        f"d{index}-{key}": value
        for index in range(1, 7)
        for key, value in TABLE.items()
    }
    provider = _Provider(table)
    directory = tempfile.mkdtemp()
    store = store_module.ExperienceStore(Path(directory))
    store.initialize()
    state = store.load("trip-1")
    state["media"] = [store_module.normalize_media(item) for item in media]
    store.write(state)
    service = service_module.DayCurationService(
        _Hass(), store, _Manager(days), _Vision(provider)
    )

    first = asyncio.run(service.async_curate_trip("trip-1", max_days=2))
    assert first["remaining"] == 4, first["remaining"]
    rounds = 1
    while first["remaining"]:
        first = asyncio.run(service.async_curate_trip("trip-1", max_days=2))
        rounds += 1
        assert rounds < 10, "die Schleife kommt nicht zum Ende"
    assert rounds == 3, rounds

    # Everything is curated, so another round is free and finished.
    calls = provider.calls
    again = asyncio.run(service.async_curate_trip("trip-1", max_days=2))
    assert again["remaining"] == 0, again
    assert provider.calls == calls, "ein fertiger Lauf darf nichts mehr kosten"
    assert len(store.load("trip-1")["day_curations"]) == 6


def verify_the_panel_summary_leaves_the_analysis_behind() -> None:
    """What a phone downloads is counts and words, not every score."""
    provider = _Provider(TABLE)
    service, store = _service(_media_set(), provider=provider)
    asyncio.run(service.async_curate_trip("trip-1"))
    record = store.load("trip-1")["day_curations"]["day-1"]
    summary = service_module.day_summary(record)
    assert "analyses" not in summary
    assert summary["selected_count"] == len(record["media_ids"])
    assert summary["must_cover"] == ["elch"]
    assert summary["photo_count"] == 8


for check in (
    verify_the_moose_reaches_the_film,
    verify_an_unchanged_day_is_never_paid_for_twice,
    verify_a_day_still_works_with_the_model_switched_off,
    verify_an_excluded_photo_never_reaches_the_provider,
    verify_the_day_is_never_named_to_the_model,
    verify_a_long_trip_is_curated_in_batches,
    verify_the_panel_summary_leaves_the_analysis_behind,
):
    check()

print("Day curation service tests passed.")
