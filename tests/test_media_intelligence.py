"""Contract tests for deterministic Roadplanner photo curation."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

path = Path("custom_components/roadplanner_mcp/media_intelligence.py")
spec = spec_from_file_location("roadplanner_media_intelligence_test", path)
assert spec and spec.loader
module = module_from_spec(spec)
spec.loader.exec_module(module)


def photo(media_id, *, taken_at, file_hash="", name="photo.jpg", cover=False, width=4032, height=3024, stop="stop-1", day="day-1", confidence=0.9):
    return {
        "id": media_id,
        "provider_item_id": f"provider-{media_id}",
        "media_type": "photo",
        "name": name,
        "taken_at": taken_at,
        "file_hash": file_hash,
        "size_bytes": 3_000_000,
        "width": width,
        "height": height,
        "thumbnail_available": True,
        "linked_stop_id": stop,
        "linked_day_id": day,
        "assignment_status": "manual" if cover else "automatic",
        "confidence": confidence,
        "distance_m": 20,
        "is_cover": cover,
        "location": {"latitude": 57.0, "longitude": 24.0},
    }


media = [
    photo("cover", taken_at="2026-07-21T10:00:00Z", cover=True),
    photo("duplicate-a", taken_at="2026-07-21T10:10:00Z", file_hash="same"),
    photo("duplicate-b", taken_at="2026-07-21T10:10:00Z", file_hash="same", confidence=0.4),
    photo("burst-a", taken_at="2026-07-21T10:20:00Z"),
    photo("burst-b", taken_at="2026-07-21T10:20:02Z", confidence=0.6),
    photo("screenshot", taken_at="2026-07-21T10:30:00Z", name="Screenshot 2026-07-21.png"),
    photo("portrait", taken_at="2026-07-21T10:40:00Z", width=3024, height=4032),
]

selected, stats = module.select_media_highlights(media, limit=3)
selected_ids = [item["id"] for item in selected]
assert selected_ids[0] == "cover"
assert not ({"duplicate-a", "duplicate-b"} <= set(selected_ids))
assert not ({"burst-a", "burst-b"} <= set(selected_ids))
assert "screenshot" not in selected_ids
assert stats["duplicate_count"] == 1
assert stats["burst_suppressed_count"] >= 1
assert stats["highlight_count"] == 3
assert module.media_quality_score(media[0]) > module.media_quality_score(media[-2])

presentation = module.build_media_presentation(media, limit=3)
assert presentation["trip_cover"] is None, "metadata-only photos must not become an arbitrary trip hero"
assert presentation["trip_selection_mode"] == "none"
assert presentation["stop_covers"]["stop-1"] == "cover"
assert presentation["day_covers"]["day-1"] == "cover"
assert len(presentation["stop_highlights"]["stop-1"]) == 3
assert presentation["selection_mode"] == "hybrid_local_first"
assert presentation["display_source_by_stop"]["stop-1"] == "travel_images"
assert presentation["display_source_by_day"]["day-1"] == "travel_images"
assert all(item.get("selection_reason") for item in selected)
# The diversity pass must not pick all highlights from the same short moment.
selected_times = {item.get("taken_at") for item in selected}
assert len(selected_times) >= 2

curation = {
    "stop-1": {
        "status": "ready",
        "highlight_ids": ["portrait", "cover", "duplicate-a"],
        "cover_id": "portrait",
        "reasons": {"portrait": "abwechslungsreiches eigenes Reisefoto"},
    }
}
vision_presentation = module.build_media_presentation(media, limit=3, curations=curation)
assert vision_presentation["stop_covers"]["stop-1"] == "cover", "manual cover must override Vision"
assert "hybrid_vision" in vision_presentation["selection_mode_by_stop"]["stop-1"]
assert vision_presentation["curation"]["vision_curated_stop_count"] == 1

# A merely date-matched suggestion must never become the automatic trip cover.
date_only = photo(
    "supermarket-shelf",
    taken_at="2026-07-21T11:00:00Z",
    name="IMG_0001.jpg",
    stop="",
    day="day-1",
    confidence=0.7,
)
date_only["assignment_status"] = "suggested"
date_only["location"] = {}
date_only["distance_m"] = None
candidate_ids = [item["id"] for item in module.select_trip_cover_candidates([date_only, *media])]
assert "supermarket-shelf" not in candidate_ids
assert "cover" in candidate_ids

explicit_trip = photo(
    "trip-cover",
    taken_at="2026-07-21T12:00:00Z",
    stop="stop-2",
    day="day-1",
)
explicit_trip["is_trip_cover"] = True
trip_presentation = module.build_media_presentation([date_only, *media, explicit_trip], limit=3)
assert trip_presentation["version"] == 3
assert trip_presentation["trip_cover"] == "trip-cover"
assert trip_presentation["trip_selection_mode"] == "manual"
assert trip_presentation["trip_cover"] != "supermarket-shelf"

# A technically strong photo linked to a pure transit stop (e.g. right after
# leaving home) must not even be eligible as an automatic trip-cover
# candidate just because it outscores every other candidate on quality
# metadata alone - a Vision curation pass could otherwise still pick it.
home_departure = photo(
    "home-road",
    taken_at="2026-07-17T08:00:00Z",
    stop="stop-departure",
    day="day-1",
    confidence=0.95,
)
scenic_stop = photo(
    "finnish-lake",
    taken_at="2026-07-24T09:00:00Z",
    stop="stop-saimaa",
    day="day-8",
    confidence=0.5,
)
candidates_without_filter = module.select_trip_cover_candidates([home_departure, scenic_stop])
assert [item["id"] for item in candidates_without_filter][0] == "home-road", (
    "sanity check: without transit awareness the higher-quality photo wins"
)
candidates_with_filter = module.select_trip_cover_candidates(
    [home_departure, scenic_stop], transit_stop_ids=frozenset({"stop-departure"})
)
candidate_ids_with_filter = [item["id"] for item in candidates_with_filter]
assert "home-road" not in candidate_ids_with_filter
assert candidate_ids_with_filter == ["finnish-lake"]

# A Vision curation that (incorrectly) requested the transit photo as trip
# cover must be ignored once it is excluded from the candidate pool - the
# panel then falls back to the planning image instead.
transit_vision_curation = {
    "trip-cover": {"status": "ready", "cover_id": "home-road"},
}
ignored_presentation = module.build_media_presentation(
    [home_departure, scenic_stop],
    limit=3,
    curations=transit_vision_curation,
    transit_stop_ids=frozenset({"stop-departure"}),
)
assert ignored_presentation["trip_cover"] is None
assert ignored_presentation["trip_cover"] != "home-road"

# The same Vision curation is honored once the requested photo is not a
# transit-only stop.
scenic_vision_curation = {
    "trip-cover": {"status": "ready", "cover_id": "finnish-lake"},
}
honored_presentation = module.build_media_presentation(
    [home_departure, scenic_stop],
    limit=3,
    curations=scenic_vision_curation,
    transit_stop_ids=frozenset({"stop-departure"}),
)
assert honored_presentation["trip_cover"] == "finnish-lake"

# An explicit user choice always wins regardless of the stop's transit status.
home_departure_explicit = dict(home_departure, is_trip_cover=True)
explicit_wins_presentation = module.build_media_presentation(
    [home_departure_explicit, scenic_stop],
    limit=3,
    transit_stop_ids=frozenset({"stop-departure"}),
)
assert explicit_wins_presentation["trip_cover"] == "home-road"

print("Media intelligence tests passed.")
