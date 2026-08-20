"""A stored LINK must never become a cover IMAGE.

Live finding: the trips tab drew a campsite's web page as the trip's
cover - `naturalWidth = 0`, "Bild nicht verfügbar". The projection's
``url`` alias was honoured on a bare details object, where ``url`` is a
provider page link written by the lookup features, not a picture. The
alias belongs to the explicit ``media`` block alone; a details object
without one offers only ``image_url``.
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))

from simulation_harness import check_invariants, load, make_store, revision  # noqa: E402

trip_projections = load("trip_projections")
_media_from_details = trip_projections._media_from_details

PAGE_LINK = "https://example.com/lieu/51373/"
IMAGE = "https://img.example.com/cover.jpg"


def verify_a_bare_link_is_not_an_image() -> None:
    assert _media_from_details({"url": PAGE_LINK}) is None, (
        "ein Quelllink im Detailobjekt wurde wieder zum Titelbild erklärt"
    )
    assert _media_from_details({"url": PAGE_LINK, "notes": "x"}) is None


def verify_real_media_still_works() -> None:
    assert _media_from_details({"image_url": IMAGE})["image_url"] == IMAGE
    assert _media_from_details({"media": {"image_url": IMAGE}})["image_url"] == IMAGE
    # The alias survives where it belongs: inside the explicit block.
    assert _media_from_details({"media": {"url": IMAGE}})["image_url"] == IMAGE
    assert _media_from_details({"media": {"url": IMAGE}, "url": PAGE_LINK})[
        "image_url"
    ] == IMAGE
    assert _media_from_details(None) is None
    assert _media_from_details({}) is None


def verify_list_trips_prefers_no_cover_over_a_page_link() -> None:
    with tempfile.TemporaryDirectory() as base:
        store = make_store(Path(base))
        day = store.add_day(
            actor="test", expected_revision=revision(store),
            title="Anreise", day_date="2026-07-01",
        )
        # A stop whose details hold ONLY a lookup link - the live shape.
        store.add_stop(
            day_id=day["day"]["id"], name="Campingplatz", actor="test",
            expected_revision=revision(store), stop_type="campsite",
            details={"url": PAGE_LINK},
        )
        check_invariants(store)
        trips = store.list_trips()["trips"]
        assert trips[0]["cover_image"] is None, (
            f"die Reisekarte bekommt wieder eine Webseite als Bild: {trips[0]['cover_image']}"
        )
        # And a real photo on the same stop wins immediately.
        stop_id = next(
            stop["id"]
            for document in store.load_trip()["days"]
            for stop in document["stops"]
        )
        store.update_stop(
            day_id=day["day"]["id"], stop_id=stop_id, actor="test",
            expected_revision=revision(store),
            patch={"details": {"url": PAGE_LINK, "media": {"image_url": IMAGE}}},
        )
        trips = store.list_trips()["trips"]
        assert trips[0]["cover_image"]["image_url"] == IMAGE


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Trip cover image tests passed.")


if __name__ == "__main__":
    main()
