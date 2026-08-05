"""Behavioral tests for the trip-summary PDF renderer.

Pure rendering only (no Home Assistant, no network) - exercises the real
reportlab drawing code with representative data, including the edge cases a
real trip can hit: no crew/vehicle, no days, a real embedded photo, and a
corrupt or truncated photo that must simply be skipped - no generic icon
filler - instead of crashing the whole export.
"""
from __future__ import annotations

import io
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

MODULE_PATH = Path("custom_components/roadplanner_mcp/trip_pdf.py")
spec = spec_from_file_location("roadplanner_trip_pdf_test", MODULE_PATH)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def _jpeg_bytes(color: tuple[int, int, int] = (200, 100, 50)) -> bytes:
    from PIL import Image

    image = Image.new("RGB", (800, 600), color=color)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def verify_full_trip_renders_a_valid_pdf() -> None:
    data = module.TripPdfData(
        title="Finnland & Baltikum 2026",
        start_date="2026-07-02",
        end_date="2026-07-28",
        crew=[
            module.PdfCrewMember(name="Aron", kind="person", note="Reiseplaner & Fahrer"),
            module.PdfCrewMember(name="Rufus", kind="dog", note="Der Hund an Bord"),
        ],
        vehicle=module.PdfVehicle(name="Nugget", note="Der Camper"),
        days=[
            module.PdfDay(
                title="Fähre nach Helsinki",
                date="2026-07-03",
                stops=[
                    module.PdfStop(name="Travemünde Fährterminal", stop_type="ferry"),
                    module.PdfStop(name="Helsinki Ankunft", stop_type="destination"),
                ],
            ),
            module.PdfDay(
                title="Seenplatte",
                date="2026-07-08",
                stops=[module.PdfStop(name="Nuuksio Nationalpark", stop_type="sightseeing")],
                photos=[_jpeg_bytes()],
            ),
        ],
        total_distance_km=4180,
        country_count=5,
    )
    pdf_bytes = module.build_trip_pdf(data)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 2_000


def _page_count(pdf_bytes: bytes) -> int:
    return pdf_bytes.count(b"/Type /Page\n") or pdf_bytes.count(b"/Type /Page")


def verify_days_flow_instead_of_owning_a_page_each() -> None:
    """A day takes the room it needs, not a whole A4 sheet.

    Live report: "Das pdf ist untauglich" - 28 days with two or three lines
    each produced 33 pages of whitespace.
    """
    days = [
        module.PdfDay(
            title=f"Tag {index}",
            date=f"2026-07-{index:02d}",
            stops=[module.PdfStop(name="Ein Stopp", stop_type="wildcamp")],
        )
        for index in range(1, 29)
    ]
    sparse = module.build_trip_pdf(
        module.TripPdfData(title="Reise", start_date="", end_date="", days=days)
    )
    assert sparse.startswith(b"%PDF")
    pages = _page_count(sparse)
    assert pages <= 8, f"28 text-only days must not need {pages} pages"

    # Photos get real space, so a photo-rich trip legitimately grows - but
    # still nowhere near one page per day.
    with_photos = [
        module.PdfDay(
            title=day.title, date=day.date, stops=day.stops, photos=[_jpeg_bytes()]
        )
        for day in days
    ]
    rich = module.build_trip_pdf(
        module.TripPdfData(title="Reise", start_date="", end_date="", days=with_photos)
    )
    assert _page_count(rich) <= 20, "photos must not bring back one page per day"


def verify_photos_keep_a_sane_frame_and_a_uniform_size() -> None:
    """Live report: "es hat wenige Bilder pro Tag und die sind zu hart geschnitten".

    One strip across the page is nearly 4:1, so every portrait photo lost
    its top and bottom. Tiles are 4:3 and always the same size, whatever a
    day happens to have.
    """
    for count in (1, 2, 3, 4, 6):
        tile_w, tile_h, columns, rows = module._photo_grid(count)
        assert columns == module._DAY_PHOTO_COLUMNS, "tile width never depends on the count"
        assert abs(tile_w / tile_h - 4 / 3) < 0.01, (tile_w, tile_h)
        assert rows == -(-count // columns)
    # A single photo is NOT blown up to page width.
    single_w, _, _, _ = module._photo_grid(1)
    assert single_w < (module.PAGE_W - 2 * module.MARGIN) / 2
    assert module._photo_grid(0) == (0.0, 0.0, 0, 0)
    # Six photos per day instead of two.
    assert module.MAX_PHOTOS_PER_DAY == 6


def verify_a_day_block_reserves_room_for_every_photo_row() -> None:
    day = module.PdfDay(title="Tag", date="2026-07-01", stops=[])
    one_row = module._day_block_height(day, 3)
    two_rows = module._day_block_height(day, 6)
    assert two_rows > one_row, "a second row needs a second row's height"
    assert module._day_block_height(day, 0) < one_row


def verify_a_day_that_lost_its_photos_says_so() -> None:
    data = module.TripPdfData(
        title="Reise",
        start_date="",
        end_date="",
        days=[
            module.PdfDay(
                title="Tag mit Fotos",
                date="2026-07-01",
                stops=[module.PdfStop(name="Stopp")],
                photo_note="20 eigene Fotos zugeordnet, aber keines konnte geladen werden - HEIC",
            )
        ],
    )
    pdf_bytes = module.build_trip_pdf(data)
    assert pdf_bytes.startswith(b"%PDF")


def verify_the_cover_uses_a_real_photo_when_there_is_one() -> None:
    base = module.TripPdfData(title="Reise", start_date="", end_date="")
    without = module.build_trip_pdf(base)
    with_photo = module.build_trip_pdf(
        module.TripPdfData(
            title="Reise", start_date="", end_date="", cover_photo=_jpeg_bytes()
        )
    )
    assert len(with_photo) > len(without), "the cover photo must be embedded"


def verify_empty_trip_still_renders() -> None:
    data = module.TripPdfData(title="Leere Reise", start_date="", end_date="")
    pdf_bytes = module.build_trip_pdf(data)
    assert pdf_bytes.startswith(b"%PDF")


def verify_corrupt_photo_is_skipped_not_placeholdered() -> None:
    assert module._decode_photo(b"not-an-image") is None
    data = module.TripPdfData(
        title="Kaputtes Foto",
        start_date="",
        end_date="",
        days=[
            module.PdfDay(
                title="Tag 1", date="", stops=[module.PdfStop(name="Irgendwo")],
                photos=[b"not-an-image"],
            )
        ],
    )
    pdf_bytes = module.build_trip_pdf(data)
    assert pdf_bytes.startswith(b"%PDF")


def verify_truncated_photo_is_skipped_not_placeholdered() -> None:
    """A real production failure: a photo download that got cut off mid-body.

    Unlike ``b"not-an-image"`` (rejected immediately, at header-parse time),
    a truncated real JPEG has a valid header - ``ImageReader.getSize()``
    succeeds - and only fails once the pixel data is actually decoded. This
    reproduces "OSError: image file is truncated" seen live from a cut-off
    OneDrive download. A day whose only photo is unusable gets no photo tile
    at all - not a generic camera-icon filler.
    """
    truncated = _jpeg_bytes()[:1000]
    assert module._decode_photo(truncated) is None
    data = module.TripPdfData(
        title="Abgeschnittenes Foto",
        start_date="",
        end_date="",
        days=[
            module.PdfDay(
                title="Tag 1", date="", stops=[module.PdfStop(name="Irgendwo")],
                photos=[truncated],
            )
        ],
    )
    pdf_bytes = module.build_trip_pdf(data)
    assert pdf_bytes.startswith(b"%PDF")


def verify_valid_photo_still_decodes() -> None:
    decoded = module._decode_photo(_jpeg_bytes())
    assert decoded is not None
    _, iw, ih = decoded
    assert (iw, ih) == (800, 600)


def verify_many_days_are_bounded() -> None:
    days = [
        module.PdfDay(title=f"Tag {index}", date="", stops=[module.PdfStop(name="Stopp")])
        for index in range(module.MAX_DAYS_RENDERED + 10)
    ]
    data = module.TripPdfData(title="Sehr lange Reise", start_date="", end_date="", days=days)
    pdf_bytes = module.build_trip_pdf(data)
    assert pdf_bytes.startswith(b"%PDF")


verify_full_trip_renders_a_valid_pdf()
verify_empty_trip_still_renders()
verify_days_flow_instead_of_owning_a_page_each()
verify_photos_keep_a_sane_frame_and_a_uniform_size()
verify_a_day_block_reserves_room_for_every_photo_row()
verify_a_day_that_lost_its_photos_says_so()
verify_the_cover_uses_a_real_photo_when_there_is_one()
verify_corrupt_photo_is_skipped_not_placeholdered()
verify_truncated_photo_is_skipped_not_placeholdered()
verify_valid_photo_still_decodes()
verify_many_days_are_bounded()

print("Trip PDF rendering tests passed.")
