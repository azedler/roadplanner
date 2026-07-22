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
assert presentation["stop_covers"]["stop-1"] == "cover"
assert presentation["day_covers"]["day-1"] == "cover"
assert len(presentation["stop_highlights"]["stop-1"]) == 3
assert presentation["selection_mode"] == "local_metadata"

print("Media intelligence tests passed.")
