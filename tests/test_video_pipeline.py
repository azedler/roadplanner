"""Videos as media, and the two gates that keep them cheap and private.

The wish is easy to say: find the good moments in our videos and cut them
into the film. Doing it wrong is easy too, and both ways cost something
that is not money.

**Sending everything to a model.** A camera roll from three weeks holds a
lot of video, most of it not film material. Uploading a family's private
footage in order to be told it is uninteresting is the wrong trade at any
price, so the obvious cases are settled offline first - and each
rejection has to be able to say why.

**Trusting what comes back.** A model asked about a clip will happily
describe the day, the place, and what happened before. All of that is
already in the roadbook, measured, and a second opinion about it is not a
second source - it is a way for a film to state something nobody
recorded. So the answer is a time range or it is nothing, and the range
is checked as arithmetic.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys

import types

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True

# `experience_helpers` reaches for Home Assistant's date utilities, which
# the release suite does not install. Two thin wrappers, supplied here
# rather than pulling in Home Assistant to read a media record.
_homeassistant = types.ModuleType("homeassistant")
_homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", _homeassistant)
_ha_util = types.ModuleType("homeassistant.util")
_ha_util.__path__ = []
sys.modules.setdefault("homeassistant.util", _ha_util)
_ha_dt = types.ModuleType("homeassistant.util.dt")
_ha_dt.parse_datetime = lambda text: __import__("datetime").datetime.fromisoformat(text)
_ha_dt.as_local = lambda value: value
sys.modules.setdefault("homeassistant.util.dt", _ha_dt)

# The same for aiohttp: `experience_helpers` imports a path helper from
# the OneDrive client, and that client imports a HTTP library the release
# suite has no reason to install. Only the names have to exist.
_aiohttp = types.ModuleType("aiohttp")
_aiohttp.ClientError = type("ClientError", (Exception,), {})
_aiohttp.ClientSession = object
_aiohttp.ClientTimeout = object
sys.modules.setdefault("aiohttp", _aiohttp)
_ha_helpers = types.ModuleType("homeassistant.helpers")
_ha_helpers.__path__ = []
sys.modules.setdefault("homeassistant.helpers", _ha_helpers)
_ha_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
_ha_client.async_get_clientsession = lambda hass: None
sys.modules.setdefault("homeassistant.helpers.aiohttp_client", _ha_client)
for _name in ("homeassistant.core", "homeassistant.config_entries", "homeassistant.exceptions"):
    _module = types.ModuleType(_name)
    _module.HomeAssistant = object
    _module.ConfigEntry = object
    _module.HomeAssistantError = type("HomeAssistantError", (Exception,), {})
    _module.callback = lambda fn: fn
    sys.modules.setdefault(_name, _module)
_ha_storage = types.ModuleType("homeassistant.helpers.storage")
_ha_storage.Store = object
sys.modules.setdefault("homeassistant.helpers.storage", _ha_storage)

_PACKAGE = "roadplanner_video_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(ROOT / "custom_components" / "roadplanner_mcp")]
sys.modules[_PACKAGE] = _root

prefilter = importlib.import_module(f"{_PACKAGE}.video_prefilter")
analysis = importlib.import_module(f"{_PACKAGE}.video_analysis")
helpers = importlib.import_module(f"{_PACKAGE}.experience_helpers")


def _video(**overrides):
    base = {
        "media_id": "m1",
        "media_type": "video",
        "duration_seconds": 12.0,
        "height": 1080,
        "chapter_id": "day-1",
        "taken_at": "2026-07-14T10:00:00Z",
    }
    base.update(overrides)
    return base


def verify_a_video_is_a_media_asset_not_a_second_kind_of_thing() -> None:
    """Same record, different `media_type`.

    Roadplanner refused everything that was not an image when it built a
    media record, which is why a family's films were invisible to a film.
    A video is the same structure with a different type and its own
    facts, so nothing that reads photos has to learn anything to keep
    working.
    """
    item = {
        "id": "01ABC",
        "name": "IMG_4711.MOV",
        "size": 40_000_000,
        "file": {"mimeType": "video/quicktime"},
        "video": {"duration": 8500, "width": 1920, "height": 1080},
        "fileSystemInfo": {"createdDateTime": "2026-07-14T10:00:00Z"},
    }
    builder = helpers._provider_media
    record = builder(item)
    assert record is not None, "ein Video muss ein Medienobjekt ergeben"
    assert record["media_type"] == "video", record
    # Duration in seconds, because every other duration in Roadplanner is
    # in seconds and a unit that changes between modules is a bug with a
    # deadline.
    assert abs(record["duration_seconds"] - 8.5) < 0.001, record
    assert record["width"] == 1920 and record["height"] == 1080

    # And a photograph is untouched by any of this.
    photo = builder(
        {
            "id": "02XYZ",
            "name": "IMG_0001.HEIC",
            "size": 2_000_000,
            "file": {"mimeType": "image/heic"},
            "photo": {"takenDateTime": "2026-07-14T11:00:00Z"},
            "image": {"width": 4032, "height": 3024},
        }
    )
    assert photo["media_type"] == "photo", photo
    assert photo.get("duration_seconds") is None


def verify_the_obvious_rejects_never_reach_the_cloud() -> None:
    """Free, offline, and each refusal states its reason."""
    chosen, rejected = prefilter.select_candidates(
        [
            _video(media_id="short", duration_seconds=0.6),
            _video(media_id="tiny", height=240, taken_at="t2"),
            _video(media_id="good", taken_at="t3"),
        ]
    )
    assert [c["media_id"] for c in chosen] == ["good"], chosen
    reasons = {entry["media_id"]: entry["skipped_reason"] for entry in rejected}
    assert reasons["short"] == prefilter.REASON_TOO_SHORT
    assert reasons["tiny"] == prefilter.REASON_TOO_SMALL
    # Every rejection is explainable. A number nobody can account for is
    # worse than no number: "only three of your forty videos were looked
    # at" has to be an answer rather than a suspicion.
    assert all(entry.get("skipped_reason") for entry in rejected)


def verify_a_long_recording_becomes_several_moments() -> None:
    """Twelve minutes of driving is not one candidate.

    Asked about as one thing it gets one answer about one thing, and the
    two moments in it are lost in the average.
    """
    chosen, _ = prefilter.select_candidates([_video(media_id="drive", duration_seconds=300.0)])
    assert len(chosen) > 1, chosen
    starts = [c["segment_start"] for c in chosen]
    assert starts == sorted(starts)
    # Spread across the recording rather than taken from the front: the
    # first ninety seconds of a drive are the same road every time.
    assert max(starts) > 100, starts
    for candidate in chosen:
        assert candidate["segment_end"] <= 300.0 + 0.001
        assert candidate["segment_end"] > candidate["segment_start"]


def verify_windows_of_one_recording_are_not_mistaken_for_duplicates() -> None:
    """The ordering bug that ate long clips, kept as a test.

    Every window of one recording carries that recording's capture time.
    Deduplicating windows therefore saw twelve copies of a twelve-minute
    drive and kept one thirty-second piece. Deduplication runs on
    recordings, before windowing, and this is what says so.
    """
    chosen, _ = prefilter.select_candidates(
        [_video(media_id="one", duration_seconds=240.0, taken_at="same")]
    )
    assert len(chosen) >= 2, chosen
    assert {c["media_id"] for c in chosen} == {"one"}

    # Two different recordings at the same moment ARE the same take.
    chosen, rejected = prefilter.select_candidates(
        [
            _video(media_id="take1", duration_seconds=9.0, taken_at="same"),
            _video(media_id="take2", duration_seconds=12.0, taken_at="same"),
        ]
    )
    assert [c["media_id"] for c in chosen] == ["take2"], "die längere Aufnahme gewinnt"
    assert rejected[0]["skipped_reason"] == prefilter.REASON_DUPLICATE


def verify_an_answer_outside_the_clip_is_refused_not_repaired() -> None:
    """A wrong number quietly corrected is a wrong number nobody notices.

    A range past the end of what was sent is evidence the answer is about
    something else. Clamping it produces something that looks exactly like
    a good answer, which is worse than having none.
    """
    good = {
        "usable": True,
        "start_seconds": 2.0,
        "end_seconds": 7.0,
        "role": "hero_clip",
        "visual_quality": 4,
        "story_value": 5,
    }
    parsed = analysis.parse_analysis(good, window_seconds=12.0)
    assert parsed and parsed["duration_seconds"] == 5.0

    for broken, label in (
        ({**good, "end_seconds": 30.0}, "über das Ende hinaus"),
        ({**good, "start_seconds": 8.0, "end_seconds": 7.0}, "invertiert"),
        ({**good, "end_seconds": 2.5}, "kürzer als ein Schnitt"),
        ({**good, "end_seconds": 11.9, "start_seconds": 0.0}, "länger als erlaubt"),
        ({**good, "usable": False}, "selbst als unbrauchbar gemeldet"),
        ({"usable": True}, "ohne Zeiten"),
        ("nope", "gar kein Objekt"),
    ):
        assert analysis.parse_analysis(broken, window_seconds=12.0) is None, label


def verify_the_model_is_never_asked_about_the_journey() -> None:
    """Day and place come from the roadbook, measured, or from nowhere.

    Everything the model is told is something it can repeat back as if it
    had seen it. So it is told about the clip and nothing else, and the
    instructions say outright that it does not know where or when this
    was and should not say.
    """
    prompt = analysis.SYSTEM_PROMPT
    assert "Erfinde nichts" in prompt
    assert "Reisetagebuch" in prompt, "der Prompt muss sagen, woher Tag und Ort kommen"
    assert "Identifiziere keine Personen" in prompt
    request = analysis.build_request(_video(), 0.0, 12.0)
    blob = repr(request)
    for leak in ("day-1", "2026-07-14", "chapter"):
        assert leak not in blob, f"{leak} darf nicht in die Anfrage"


def verify_the_same_clip_is_never_paid_for_twice() -> None:
    """The cache key is what makes a second render free."""
    asset = {"media_id": "m1", "file_hash": "abc"}
    first = analysis.analysis_key(asset, 0.0, 12.0)
    assert first == analysis.analysis_key(asset, 0.0, 12.0)
    assert first != analysis.analysis_key(asset, 0.0, 11.0), "anderes Fenster, andere Frage"
    assert first != analysis.analysis_key({**asset, "file_hash": "xyz"}, 0.0, 12.0)


def verify_original_sound_is_only_ever_a_recommendation() -> None:
    """Nothing switches on a family's private conversation by itself."""
    parsed = analysis.parse_analysis(
        {
            "usable": True,
            "start_seconds": 1.0,
            "end_seconds": 5.0,
            "original_sound_interesting": True,
        },
        window_seconds=12.0,
    )
    assert parsed["original_sound_interesting"] is True
    # It is a flag on the analysis and nothing else - there is no field
    # here that a renderer could read as "play this out loud".
    assert "play_sound" not in parsed
    assert "muted" not in parsed


for check in (
    verify_a_video_is_a_media_asset_not_a_second_kind_of_thing,
    verify_the_obvious_rejects_never_reach_the_cloud,
    verify_a_long_recording_becomes_several_moments,
    verify_windows_of_one_recording_are_not_mistaken_for_duplicates,
    verify_an_answer_outside_the_clip_is_refused_not_repaired,
    verify_the_model_is_never_asked_about_the_journey,
    verify_the_same_clip_is_never_paid_for_twice,
    verify_original_sound_is_only_ever_a_recommendation,
):
    check()

print("Video pipeline tests passed.")
