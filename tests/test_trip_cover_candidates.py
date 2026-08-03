"""Trip-cover candidates: trip-window filter and sticky retention.

Live report: the trip hero image changed AGAIN, this time to a
Christmas-tree night shot - a photo provably taken outside the July trip.
Two guarantees now hold at candidate-selection level: (1) photos whose
timestamp clearly lies outside the trip window never compete for the trip
cover (unless the user explicitly chose them), and (2) the currently
chosen cover stays in the candidate set even when new photo batches push
it out of the local top ranking - otherwise the sticky-cover guarantee
broke on every larger sync.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_trip_cover_candidates_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


module = load("media_intelligence")


def photo(identifier: str, taken_at: str | None, **extra):
    return {
        "id": identifier,
        "media_type": "photo",
        "assignment_status": "automatic",
        "linked_stop_id": f"stop-{identifier}",
        "taken_at": taken_at,
        "width": 4000,
        "height": 3000,
        "name": f"{identifier}.jpg",
        **extra,
    }


TRIP_START = "2026-07-17"
TRIP_END = "2026-08-08"


def verify_out_of_window_photos_never_compete() -> None:
    media = [
        photo("july", "2026-07-20T12:00:00Z"),
        photo("christmas", "2025-12-24T18:00:00Z"),
        photo("no-date", None),
    ]
    selected = module.select_trip_cover_candidates(
        media, trip_start=TRIP_START, trip_end=TRIP_END
    )
    ids = [item["id"] for item in selected]
    assert "christmas" not in ids, ids
    assert "july" in ids
    assert "no-date" in ids, "photos without a parsable timestamp stay eligible"
    # An explicit user-chosen trip cover is never excluded by the window.
    chosen = photo("chosen-old", "2025-12-24T18:00:00Z", is_trip_cover=True)
    selected = module.select_trip_cover_candidates(
        [photo("july", "2026-07-20T12:00:00Z"), chosen],
        trip_start=TRIP_START,
        trip_end=TRIP_END,
    )
    assert "chosen-old" in [item["id"] for item in selected]


def verify_sticky_cover_survives_top_n_cut() -> None:
    # 20 strong new photos push the current cover out of the top ranking -
    # it must still be part of the candidate set so the sticky guarantee in
    # the vision engine holds.
    media = [
        photo(f"new-{i}", "2026-07-21T10:00:00Z", is_cover=(i < 5))
        for i in range(20)
    ] + [photo("current-cover", "2026-07-18T09:00:00Z")]
    selected = module.select_trip_cover_candidates(
        media,
        limit=6,
        trip_start=TRIP_START,
        trip_end=TRIP_END,
        sticky_cover_id="current-cover",
    )
    ids = [item["id"] for item in selected]
    assert "current-cover" in ids, ids
    assert len(ids) <= 6
    # An OUT-OF-WINDOW current cover is deliberately NOT retained - that is
    # exactly the Christmas-tree case: it must be replaced.
    media = [photo("july", "2026-07-20T12:00:00Z"), photo("christmas", "2025-12-24T18:00:00Z")]
    selected = module.select_trip_cover_candidates(
        media,
        trip_start=TRIP_START,
        trip_end=TRIP_END,
        sticky_cover_id="christmas",
    )
    assert "christmas" not in [item["id"] for item in selected]


def verify_manager_passes_window_and_sticky() -> None:
    source = (PACKAGE_ROOT / "media_curation_manager.py").read_text(encoding="utf-8")
    assert source.count('trip_start=trip.get("start_date")') == 2, (
        "both trip-cover call sites pass the trip window"
    )
    assert source.count('trip_end=trip.get("end_date")') == 2
    assert source.count("sticky_cover_id=") == 2


if __name__ == "__main__":
    verify_out_of_window_photos_never_compete()
    verify_sticky_cover_survives_top_n_cut()
    verify_manager_passes_window_and_sticky()
    print("Trip cover candidate tests passed.")
