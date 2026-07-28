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
verify_corrupt_photo_is_skipped_not_placeholdered()
verify_truncated_photo_is_skipped_not_placeholdered()
verify_valid_photo_still_decodes()
verify_many_days_are_bounded()

print("Trip PDF rendering tests passed.")
