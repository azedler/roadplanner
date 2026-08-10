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
from datetime import datetime, timezone
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

    async def async_analyze_images(
        self, *, system_instruction, prompt, images, schema, max_output_tokens,
        max_images=15,
    ):
        # The fake enforces the SAME ceiling as the real provider. A fake
        # that quietly accepts any number of images is how a batch size
        # larger than the provider allows passed every test here and
        # failed on every real trip.
        if len(images) > int(max_images):
            raise AssertionError(
                f"{len(images)} Bilder in einem Aufruf, erlaubt sind {max_images}"
            )
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


def verify_a_forced_rerun_pays_each_day_once_and_converges() -> None:
    """Force must survive the panel's batching loop without paying twice.

    The 4.83.0 loop sent force in every round, and force alone re-paid
    days 1-4 in every round: they are the first days with photographs,
    the batch budget was spent on them again, day 5 was never reached,
    and the daily limit went up in re-answering questions asked a minute
    earlier. The run marker is what makes the rounds converge: a record
    younger than the first round's start time is this run's own finished
    work and is not forced again.
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

    asyncio.run(service.async_curate_trip("trip-1", max_days=None))
    primed = provider.calls
    assert primed == 6, primed

    # The marker has second granularity; in real life minutes pass between
    # the first curation and a forced refresh, in this test microseconds
    # do. Age the stored records so they are visibly older than the run.
    state = store.load("trip-1")
    for record in state["day_curations"].values():
        record["curated_at"] = "2026-01-01T00:00:00Z"
    store.write(state)

    marker = None
    rounds = 0
    while True:
        rounds += 1
        assert rounds <= 6, "die erzwungene Schleife muss konvergieren"
        result = asyncio.run(
            service.async_curate_trip(
                "trip-1", force=True, max_days=2, fresh_after=marker
            )
        )
        if marker is None:
            marker = result["run_marker"]
        if not result["remaining"]:
            break
    assert rounds == 3, rounds
    assert provider.calls == primed * 2, (
        "jeder Tag wird in einem erzwungenen Lauf genau einmal neu bezahlt"
    )


def verify_a_failed_look_never_erases_the_stored_answer() -> None:
    """A look that produced nothing is a failure, not an answer.

    Storing an empty result over a good one is how a daily limit that ran
    out mid-trip wiped whole days: the next unforced run found a matching
    key, trusted the empty record, and never asked again.
    """
    errors = importlib.import_module(f"{_PACKAGE}.roadplanner")
    provider = _Provider(TABLE)
    service, store = _service(_media_set(), provider=provider)
    asyncio.run(service.async_curate_trip("trip-1"))
    record = store.load("trip-1")["day_curations"]["day-1"]
    assert record["analyses"], record
    good = dict(record["analyses"])
    good_key = record["analysis_key"]

    class _Broken:
        async def async_analyze_images(self, **_kwargs):
            raise errors.RoadplannerError("Provider nicht erreichbar")

    service._vision.provider = _Broken()
    asyncio.run(service.async_curate_trip("trip-1", force=True))
    after = store.load("trip-1")["day_curations"]["day-1"]
    assert after["analyses"] == good, "ein fehlgeschlagener Blick darf nichts löschen"
    assert after["analysed_count"] == 0
    assert after["analysis_key"] == good_key


def verify_an_empty_stored_analysis_is_asked_again() -> None:
    """The poisoned record that pinned days 2-5 at "analysiert 0".

    A transient failure once stored an empty analysis under a matching
    key; from then on every unforced run served it back as if it were an
    answer. An empty analysis is not an answer, and the next ordinary run
    must heal the day without anyone pressing force.
    """
    provider = _Provider(TABLE)
    service, store = _service(_media_set(), provider=provider)
    asyncio.run(service.async_curate_trip("trip-1"))
    state = store.load("trip-1")
    state["day_curations"]["day-1"]["analyses"] = {}
    store.write(state)
    calls = provider.calls
    asyncio.run(service.async_curate_trip("trip-1"))
    assert provider.calls > calls, "eine leere gespeicherte Analyse ist kein Cache-Treffer"
    healed = store.load("trip-1")["day_curations"]["day-1"]
    assert healed["analyses"], "der Tag ist geheilt"


def verify_unseen_days_get_the_daily_budget_before_refreshes() -> None:
    """Whose day the limited daily budget belongs to.

    The daily limit is one budget for the whole trip. Spent in journey
    order, a run re-asks days that already hold good answers and can
    reach the end of the budget before a day that has NO answer at all -
    which is what kept days 2-5 of the real trip at "analysiert 0" while
    every later day was analysed. A day nobody has looked at goes first.
    """
    days = [
        {**DAY, "id": f"day-{index}", "title": "Tag im Elchpark"}
        for index in range(1, 5)
    ]
    media = []
    for index in range(1, 5):
        for entry in _media_set():
            media.append({**entry, "id": f"d{index}-{entry['id']}",
                          "provider_item_id": f"item-d{index}-{entry['id']}",
                          "file_hash": f"hash-d{index}-{entry['id']}",
                          "linked_day_id": f"day-{index}"})
    table = {
        f"d{index}-{key}": value
        for index in range(1, 5)
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

    # Days 1 and 2 already answered; days 3 and 4 never looked at.
    asyncio.run(service.async_curate_trip("trip-1", max_days=2))
    answered = set(store.load("trip-1")["day_curations"])
    assert answered == {"day-1", "day-2"}, answered

    # One paid day left in this call: it must go to a day with NOTHING,
    # not to refreshing day 1.
    result = asyncio.run(service.async_curate_trip("trip-1", force=True, max_days=1))
    paid = [entry["day_id"] for entry in result["days"] if entry["analysed_count"]]
    assert paid == ["day-3"], paid


def verify_the_daily_limit_stops_the_run_and_says_so() -> None:
    """A spent budget is a fact about the trip, not about one day.

    Marching on stamps "Tageslimit erreicht" over every remaining day's
    note - erasing the more useful thing it said - and hands the panel a
    `remaining` it loops on for forty rounds that each do nothing, which
    is why a run with no budget left "finished" in five seconds.
    """
    days = [
        {**DAY, "id": f"day-{index}", "title": "Tag im Elchpark"}
        for index in range(1, 5)
    ]
    media = []
    for index in range(1, 5):
        for entry in _media_set():
            media.append({**entry, "id": f"d{index}-{entry['id']}",
                          "provider_item_id": f"item-d{index}-{entry['id']}",
                          "file_hash": f"hash-d{index}-{entry['id']}",
                          "linked_day_id": f"day-{index}"})
    provider = _Provider({})
    directory = tempfile.mkdtemp()
    store = store_module.ExperienceStore(Path(directory))
    store.initialize()
    state = store.load("trip-1")
    state["media"] = [store_module.normalize_media(item) for item in media]
    # Today's budget is already gone.
    state["vision_usage"] = {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "count": service_module.DAY_CURATION_DAILY_LIMIT,
    }
    store.write(state)
    service = service_module.DayCurationService(
        _Hass(), store, _Manager(days), _Vision(provider)
    )

    result = asyncio.run(service.async_curate_trip("trip-1", max_days=None))
    assert result["quota_exhausted"] is True, result
    assert provider.calls == 0, "ohne Kontingent darf nichts an den Provider gehen"
    assert result["remaining"] == 0, (
        "die Schleife im Panel darf nicht vierzig Runden lang nichts tun"
    )
    assert len(result["days"]) == 1, "nach dem Limit wird kein weiterer Tag angefasst"
    assert "Tageslimit" in result["days"][0]["note"]
    # The days it never reached keep whatever they knew before.
    assert set(store.load("trip-1")["day_curations"]) == {"day-1"}


def verify_the_daily_limit_is_settable() -> None:
    """The budget is a setting, not a constant recompiled into a release.

    It used to be a number in the source, so the one day somebody needed
    a few more looks - a trip whose early days had never been analysed -
    the only way to get them was to wait for the UTC rollover.
    """
    provider = _Provider(TABLE)
    directory = tempfile.mkdtemp()
    store = store_module.ExperienceStore(Path(directory))
    store.initialize()
    state = store.load("trip-1")
    state["media"] = [store_module.normalize_media(item) for item in _media_set()]
    store.write(state)

    # Zero means off, and it must not reach the provider at all.
    service = service_module.DayCurationService(
        _Hass(), store, _Manager([DAY]), _Vision(provider), daily_limit=0
    )
    result = asyncio.run(service.async_curate_trip("trip-1"))
    assert provider.calls == 0, "ein Limit von null darf nichts kosten"
    assert result["quota_exhausted"] is True
    # ...and the day still comes out usable, ordered locally.
    assert result["days"][0]["media_ids"], result["days"][0]

    # A raised limit is honoured by the same service.
    generous = service_module.DayCurationService(
        _Hass(), store, _Manager([DAY]), _Vision(provider), daily_limit=120
    )
    assert generous.daily_limit == 120
    asyncio.run(generous.async_curate_trip("trip-1", force=True))
    assert provider.calls >= 1, "mit Kontingent wird wieder gefragt"

    # The default stays what the code always used.
    assert (
        service_module.DayCurationService(
            _Hass(), store, _Manager([DAY]), _Vision(provider)
        ).daily_limit
        == service_module.DAY_CURATION_DAILY_LIMIT
    )


def verify_the_batch_size_and_the_provider_limit_agree() -> None:
    """One number in two modules, and only one side was ever raised.

    `batches()` splits a day at MAX_IMAGES_PER_CALL; the provider refused
    anything above its own ceiling, which was a hard 15 written for the
    STOP curation. Every day whose pool held 16 to 24 photographs
    therefore produced exactly one oversized group, was refused, and went
    unanalysed - forever, because no amount of quota or force changes an
    argument the provider rejects. Days 2, 4 and 5 of the real trip stood
    at "analysiert 0" through five releases because of it.

    This reads BOTH numbers out of the real modules and compares them,
    which is the remedy this project wrote down for exactly this pattern.
    """
    vision = importlib.import_module(f"{_PACKAGE}.media_curation_vision")
    source = (
        ROOT / "custom_components" / "roadplanner_mcp" / "day_curation_service.py"
    ).read_text(encoding="utf-8")
    assert "max_images=MAX_IMAGES_PER_CALL" in source, (
        "die Tages-Kuratierung muss dem Provider ihre eigene Batchgröße nennen"
    )

    # And no batch of any plausible pool may exceed what it sends.
    for pool_size in range(2, 121):
        groups = vision.batches([f"m{i}" for i in range(pool_size)])
        assert groups, pool_size
        assert max(len(group) for group in groups) <= vision.MAX_IMAGES_PER_CALL, (
            f"Pool {pool_size} erzeugt einen Batch über MAX_IMAGES_PER_CALL"
        )
        assert sum(len(group) for group in groups) == pool_size, pool_size


def verify_one_refused_batch_does_not_cost_the_whole_day() -> None:
    """A safety block is about those pictures, not about the day.

    On the real trip a day of 28 photographs came back
    PROHIBITED_CONTENT and the run stopped there, throwing away the other
    group's answers as well. A refused group is skipped; what the rest
    returned is kept.
    """
    errors = importlib.import_module(f"{_PACKAGE}.roadplanner")

    class _Blocked(errors.RoadplannerError):
        code = "content_blocked"

    class _HalfBlocking:
        """Refuses the first batch, answers the second."""

        def __init__(self):
            self.calls = 0
            self.seen_ids = []
            self.prompts = []

        async def async_analyze_images(self, *, images, max_images=15, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise _Blocked(
                    "Gemini hat die Anfrage aus Sicherheitsgründen blockiert "
                    "(PROHIBITED_CONTENT)."
                )
            self.seen_ids.extend(item.image_id for item in images)
            return _Result(
                {"photos": [{"image_id": item.image_id, **TABLE.get(item.image_id, {})}
                            for item in images]}
            )

    # A pool wide enough to need two batches.
    media = [_photo(f"moose-{index}", seconds=index * 2) for index in range(30)]
    provider = _HalfBlocking()
    service, store = _service(media, provider=provider)
    result = asyncio.run(service.async_curate_trip("trip-1"))
    day = result["days"][0]

    assert provider.calls == 2, "nach einem blockierten Batch muss weitergefragt werden"
    assert day["analysed_count"] > 0, "die Antworten des zweiten Batches bleiben erhalten"
    assert "blockiert" in day["note"], day["note"]
    assert store.load("trip-1")["day_curations"]["day-1"]["analyses"], "der Tag ist nicht leer"


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
    verify_a_forced_rerun_pays_each_day_once_and_converges,
    verify_unseen_days_get_the_daily_budget_before_refreshes,
    verify_the_daily_limit_stops_the_run_and_says_so,
    verify_the_daily_limit_is_settable,
    verify_the_batch_size_and_the_provider_limit_agree,
    verify_one_refused_batch_does_not_cost_the_whole_day,
    verify_a_failed_look_never_erases_the_stored_answer,
    verify_an_empty_stored_analysis_is_asked_again,
    verify_the_panel_summary_leaves_the_analysis_behind,
):
    check()

print("Day curation service tests passed.")
