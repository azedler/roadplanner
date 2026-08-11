"""The decisions of the video pipeline, checked without a provider.

Everything here is arithmetic over stored records, which is the point:
the same code answers "what would this cost?" before anybody spends
money and "what do we have?" afterwards, and neither answer needs a
network.

The records below are the shape the MEDIA LIBRARY actually stores for a
video - `duration_seconds`, `file_hash`, `provider_item_id`, `height` -
and not the shape `video_asset.build_asset` produces. Those are two
different dicts, and a test written against the convenient one is how the
cache key came to read a field that is never present.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"
sys.dont_write_bytecode = True

_PACKAGE = "roadplanner_video_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(INTEGRATION)]
sys.modules[_PACKAGE] = _root

orch = importlib.import_module(f"{_PACKAGE}.video_orchestration")
asset = importlib.import_module(f"{_PACKAGE}.video_asset")
proxy = importlib.import_module(f"{_PACKAGE}.video_proxy")
analysis = importlib.import_module(f"{_PACKAGE}.video_analysis")

MODEL = "gemini-analysis"
SCHEMA = 1


def _library_video(index: int, *, seconds: float = 120.0, height: int = 1080):
    """A video exactly as the media library stores one."""
    return {
        "id": f"media-{index}",
        "media_type": "video",
        "provider_item_id": f"item-{index}",
        "file_hash": f"hash-{index}",
        "duration_seconds": seconds,
        "width": 1920,
        "height": height,
        "linked_day_id": "day-1",
        "name": f"clip{index}.mp4",
    }


def _window(video, start, end):
    return {**video, "segment_start": start, "segment_end": end}


# --- the cache is asked before the wallet -------------------------------


def verify_two_videos_never_share_a_cache_key() -> None:
    """They did: the key read a field the library does not store.

    `content_hash` belongs to `video_asset.build_asset`. A video that
    arrived through the media library has `file_hash` and
    `provider_item_id` instead, so every one of them keyed on None - one
    cached answer for the whole camera roll, silently.
    """
    first = asset.cache_key(_library_video(1), model=MODEL, schema_version=SCHEMA)
    second = asset.cache_key(_library_video(2), model=MODEL, schema_version=SCHEMA)
    assert first != second, "zwei verschiedene Videos teilen sich einen Cache-Eintrag"


def verify_the_key_moves_with_model_and_schema() -> None:
    """A new model or contract is a different question, so a new answer."""
    video = _library_video(1)
    base = asset.cache_key(video, model=MODEL, schema_version=SCHEMA)
    assert base != asset.cache_key(video, model="other", schema_version=SCHEMA)
    assert base != asset.cache_key(video, model=MODEL, schema_version=SCHEMA + 1)


def verify_a_video_without_identity_is_refused_rather_than_pooled() -> None:
    """The failure mode that made this dangerous was silence."""
    try:
        asset.cache_key({"media_type": "video"}, model=MODEL, schema_version=SCHEMA)
    except asset.VideoAssetError:
        return
    raise AssertionError("ein Video ohne Identität bekam einen Schlüssel")


def verify_a_cached_window_is_not_planned_again() -> None:
    """The whole point: an unchanged video costs nothing a second time."""
    windows = [_window(_library_video(1), 0, 30), _window(_library_video(2), 0, 30)]
    empty = orch.analysis_plan(windows, {}, model=MODEL, schema_version=SCHEMA)
    assert empty["new_count"] == 2 and empty["cached_count"] == 0

    stored = {empty["new"][0]["cache_key"]: {"segments": []}}
    second = orch.analysis_plan(windows, stored, model=MODEL, schema_version=SCHEMA)
    assert second["new_count"] == 1, second["new_count"]
    assert second["cached_count"] == 1, second["cached_count"]


def verify_force_replans_everything_without_deleting_anything() -> None:
    """"Analyse again" is explicit, and a failed retry keeps the old answer."""
    windows = [_window(_library_video(1), 0, 30)]
    plan = orch.analysis_plan(windows, {}, model=MODEL, schema_version=SCHEMA)
    stored = {plan["new"][0]["cache_key"]: {"segments": [{"start_seconds": 1}]}}
    forced = orch.analysis_plan(
        windows, stored, model=MODEL, schema_version=SCHEMA, force=True
    )
    assert forced["new_count"] == 1 and forced["cached_count"] == 0
    assert stored, "der Cache wurde beim Neuanalysieren geleert"


def verify_the_cost_is_estimated_before_anything_is_spent() -> None:
    """Shown to a person who has not decided yet, so never optimistic."""
    windows = [_window(_library_video(index), 0, 30) for index in range(1, 11)]
    plan = orch.analysis_plan(windows, {}, model=MODEL, schema_version=SCHEMA)
    assert plan["analysis_seconds"] == 300.0, plan["analysis_seconds"]
    assert plan["estimated_tokens"] == 300 * orch.TOKENS_PER_SECOND_LOW
    assert plan["estimated_eur"] > 0.0
    # A cached window is not billed, so it may not appear in the estimate.
    stored = {entry["cache_key"]: {} for entry in plan["new"][:5]}
    cheaper = orch.analysis_plan(windows, stored, model=MODEL, schema_version=SCHEMA)
    assert cheaper["analysis_seconds"] == 150.0, cheaper["analysis_seconds"]


def verify_one_press_cannot_pay_for_an_unexpected_folder() -> None:
    """A guard against a directory nobody meant to analyse."""
    windows = [_window(_library_video(index), 0, 30) for index in range(200)]
    plan = orch.analysis_plan(windows, {}, model=MODEL, schema_version=SCHEMA)
    assert plan["new_count"] == orch.MAX_ANALYSES_PER_RUN
    assert plan["over_limit"] == 200 - orch.MAX_ANALYSES_PER_RUN


# --- an answer is placed on the recording's own timeline -----------------


def verify_a_segment_is_moved_onto_the_recording() -> None:
    """The model answers in the window's seconds; the film cuts the file."""
    found = orch.normalise_segment(
        {
            "start_seconds": 2.5,
            "end_seconds": 7.0,
            "role": analysis.ROLE_HERO,
            "story_value": 4,
            "visual_quality": 4,
        },
        window_start=52.0,
        duration_seconds=300.0,
    )
    assert found["start_seconds"] == 54.5, found
    assert found["end_seconds"] == 59.0, found
    assert found["duration_seconds"] == 4.5, found


def verify_a_segment_past_the_end_is_refused_not_clamped() -> None:
    """Evidence the answer is about something else, not a range to repair."""
    assert (
        orch.normalise_segment(
            {"start_seconds": 2.5, "end_seconds": 7.0},
            window_start=298.0,
            duration_seconds=300.0,
        )
        is None
    )


def verify_an_unmeasured_recording_does_not_invent_a_bound() -> None:
    """Duration zero means nobody measured it, not that it is zero long."""
    found = orch.normalise_segment(
        {"start_seconds": 1.0, "end_seconds": 5.0},
        window_start=0.0,
        duration_seconds=0.0,
    )
    assert found is not None and found["end_seconds"] == 5.0


def verify_absurd_lengths_are_refused() -> None:
    """Below a moment, above a scene."""
    assert (
        orch.normalise_segment(
            {"start_seconds": 1.0, "end_seconds": 1.2}, window_start=0, duration_seconds=60
        )
        is None
    )
    assert (
        orch.normalise_segment(
            {"start_seconds": 1.0, "end_seconds": 55.0}, window_start=0, duration_seconds=60
        )
        is None
    )


def verify_the_same_moment_proposed_twice_is_kept_once() -> None:
    """Windows overlap on purpose, so two of them can see one moment.

    Keeping both is the photograph duplication bug in another medium.
    """
    segments = [
        {"start_seconds": 10, "end_seconds": 15, "story_value": 5, "visual_quality": 4},
        {"start_seconds": 12, "end_seconds": 17, "story_value": 2, "visual_quality": 2},
        {"start_seconds": 40, "end_seconds": 45, "story_value": 4, "visual_quality": 4},
    ]
    kept = orch.merge_overlapping(segments)
    assert len(kept) == 2, kept
    # The stronger proposal keeps the ground it shares.
    assert kept[0]["start_seconds"] == 10 and kept[0]["story_value"] == 5


# --- a video is a medium like a photograph ------------------------------


def verify_a_day_shows_some_of_its_clips_not_all_of_them() -> None:
    """Twelve recordings may legitimately produce four clips."""
    segments = [
        {
            "start_seconds": index * 20,
            "end_seconds": index * 20 + 5,
            "story_value": 5,
            "visual_quality": 4,
            "role": analysis.ROLE_AMBIENT,
        }
        for index in range(12)
    ]
    assert len(orch.clips_for_day(segments, importance="transition")) == 1
    assert len(orch.clips_for_day(segments, importance="normal")) == 2
    assert len(orch.clips_for_day(segments, importance="highlight")) == 3


def verify_a_weak_clip_is_not_used_at_all() -> None:
    """It would displace a photograph that is better than it."""
    weak = [
        {
            "start_seconds": 0,
            "end_seconds": 4,
            "story_value": 0,
            "visual_quality": 1,
            "role": analysis.ROLE_AMBIENT,
        }
    ]
    assert orch.clips_for_day(weak, importance="highlight") == []


def verify_a_day_has_at_most_one_hero_clip() -> None:
    """Two "moment of the day" clips is no moment of the day."""
    segments = [
        {
            "start_seconds": index * 20,
            "end_seconds": index * 20 + 5,
            "story_value": 5,
            "visual_quality": 5,
            "role": analysis.ROLE_HERO,
        }
        for index in range(3)
    ]
    chosen = orch.clips_for_day(segments, importance="highlight")
    heroes = [clip for clip in chosen if clip["role"] == analysis.ROLE_HERO]
    assert len(heroes) == 1, [clip["role"] for clip in chosen]


def verify_a_clip_costs_a_photograph_rather_than_lengthening_the_day() -> None:
    """The whole of "a video is a medium like a photograph"."""
    clips = [{"start_seconds": 0, "end_seconds": 4}, {"start_seconds": 9, "end_seconds": 13}]
    assert orch.photo_places_taken(clips) == 2
    assert orch.photo_places_taken([]) == 0


# --- what leaves the house ----------------------------------------------


def verify_the_analysis_proxy_is_small_short_and_silent() -> None:
    """A private recording leaves in the smallest form that can answer."""
    args = proxy.analysis_args(Path("/in.mp4"), Path("/out.mp4"), start=10, end=40)
    assert "-an" in args, "der Analyseproxy würde Ton mitschicken"
    assert f"fps={proxy.ANALYSIS_FPS}" in " ".join(args)
    assert f"scale=-2:{proxy.ANALYSIS_HEIGHT}" in " ".join(args)
    # The window, not the recording.
    assert "-t" in args and args[args.index("-t") + 1] == "30.000"


def verify_a_window_cannot_become_the_whole_recording() -> None:
    """A caller that forgot to window is bounded rather than trusted."""
    args = proxy.analysis_args(Path("/in.mp4"), Path("/out.mp4"), start=0, end=9999)
    assert float(args[args.index("-t") + 1]) == proxy.MAX_ANALYSIS_SECONDS


def verify_the_render_proxy_is_silent_too() -> None:
    """Original sound is stored as a recommendation and never acted on."""
    args = proxy.render_args(Path("/in.mp4"), Path("/out.mp4"), start=54.5, end=59.0)
    assert "-an" in args, "ein privates Gespräch könnte hörbar werden"
    assert f"scale=-2:{proxy.RENDER_HEIGHT}" in " ".join(args)
    # Accurate seek: -ss AFTER -i, or the clip starts before the moment.
    assert args.index("-i") < args.index("-ss")


def verify_the_analysis_proxy_seeks_fast_and_the_render_proxy_accurately() -> None:
    """Two cuts, two different trades, both deliberate."""
    analysis_call = proxy.analysis_args(Path("/i"), Path("/o"), start=5, end=15)
    assert analysis_call.index("-ss") < analysis_call.index("-i")


def verify_a_video_keeps_its_length_through_the_store() -> None:
    """The one fact the whole video pipeline is built on.

    `normalize_media` dropped `duration_seconds`, so every stored video
    had no length: the windows a recording is offered in, the estimate of
    what analysing it costs and the bound a proposed segment is checked
    against all come from that number. Nothing failed - it simply
    behaved as though every recording were of unknown length.
    """
    store = importlib.import_module(f"{_PACKAGE}.experience_store")
    stored = store.normalize_media(
        {
            "id": "m1",
            "trip_id": "t1",
            "provider_item_id": "p1",
            "media_type": "video",
            "duration_seconds": 123.4,
        }
    )
    assert stored["media_type"] == "video"
    assert stored["duration_seconds"] == 123.4, stored["duration_seconds"]
    # A photograph has no length, and gains none.
    photo = store.normalize_media(
        {"id": "m2", "trip_id": "t1", "provider_item_id": "p2", "media_type": "photo"}
    )
    assert photo["duration_seconds"] is None


def verify_the_library_rescans_once_for_videos() -> None:
    """A delta sync only reports changes, so old videos never arrive."""
    library = (INTEGRATION / "media_library_manager.py").read_text(encoding="utf-8")
    version = int(
        library.split("_MEDIA_SYNC_STRATEGY_VERSION = ")[1].split("\n")[0]
    )
    assert version >= 4, version


def verify_the_daily_budget_is_actually_daily() -> None:
    """It was the whole trip's, because it read a field nobody writes.

    `select_candidates` grouped by `chapter_id`; the media library stores
    `linked_day_id`. So every recording answered "" and shared ONE day's
    allowance - on a real trip, twenty videos in, six out, eighteen
    rejected as "Tagesbudget erreicht" for a budget that was never per
    day.
    """
    videos = [
        {
            **_library_video(index),
            "linked_day_id": f"day-{index % 8}",
            "duration_seconds": 31.0,
        }
        for index in range(20)
    ]
    chosen, rejected = importlib.import_module(
        f"{_PACKAGE}.video_prefilter"
    ).select_candidates(videos)
    assert len(chosen) == 20, (len(chosen), len(rejected))
    assert not [
        entry for entry in rejected if "Tagesbudget" in str(entry.get("skipped_reason"))
    ], rejected

    # And a day that really is over its allowance is still capped.
    crowded = [
        {**_library_video(100 + index), "linked_day_id": "day-1", "duration_seconds": 31.0}
        for index in range(12)
    ]
    kept, dropped = importlib.import_module(
        f"{_PACKAGE}.video_prefilter"
    ).select_candidates(crowded)
    assert len(kept) < len(crowded), (len(kept), len(dropped))


for check in (
    verify_two_videos_never_share_a_cache_key,
    verify_the_key_moves_with_model_and_schema,
    verify_a_video_without_identity_is_refused_rather_than_pooled,
    verify_a_cached_window_is_not_planned_again,
    verify_force_replans_everything_without_deleting_anything,
    verify_the_cost_is_estimated_before_anything_is_spent,
    verify_one_press_cannot_pay_for_an_unexpected_folder,
    verify_a_segment_is_moved_onto_the_recording,
    verify_a_segment_past_the_end_is_refused_not_clamped,
    verify_an_unmeasured_recording_does_not_invent_a_bound,
    verify_absurd_lengths_are_refused,
    verify_the_same_moment_proposed_twice_is_kept_once,
    verify_a_day_shows_some_of_its_clips_not_all_of_them,
    verify_a_weak_clip_is_not_used_at_all,
    verify_a_day_has_at_most_one_hero_clip,
    verify_a_clip_costs_a_photograph_rather_than_lengthening_the_day,
    verify_the_analysis_proxy_is_small_short_and_silent,
    verify_a_window_cannot_become_the_whole_recording,
    verify_the_render_proxy_is_silent_too,
    verify_the_analysis_proxy_seeks_fast_and_the_render_proxy_accurately,
    verify_a_video_keeps_its_length_through_the_store,
    verify_the_library_rescans_once_for_videos,
    verify_the_daily_budget_is_actually_daily,
):
    check()

print("Video orchestration tests passed.")
