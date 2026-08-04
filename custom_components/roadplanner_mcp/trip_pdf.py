"""Render a Roadplanner trip summary as a PDF.

Pure, synchronous rendering only - no Home Assistant, no I/O beyond building
bytes in memory. Callers gather trip/crew/photo data asynchronously first
(see trip_pdf_export.py) and run this module's build function in an executor,
since reportlab's canvas drawing is CPU-bound.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import io
import logging
import math
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

_LOGGER = logging.getLogger(__name__)

# Bundled DejaVu Sans (see assets/fonts/LICENSE): the built-in Helvetica
# only covers WinAnsi - Polish place names (Łeba, Międzywodami) and bullet
# characters rendered as black boxes (live report). Registration happens
# lazily on the first build; if it fails, Helvetica remains the fallback.
_FONTS_DIR = Path(__file__).parent / "assets" / "fonts"
FONT = "Helvetica"
FONT_B = "Helvetica-Bold"
FONT_I = "Helvetica-Oblique"


def _ensure_fonts() -> None:
    global FONT, FONT_B, FONT_I
    if FONT == "RoadplannerSans":
        return
    try:
        pdfmetrics.registerFont(
            TTFont("RoadplannerSans", str(_FONTS_DIR / "DejaVuSans.ttf"))
        )
        pdfmetrics.registerFont(
            TTFont("RoadplannerSans-Bold", str(_FONTS_DIR / "DejaVuSans-Bold.ttf"))
        )
    except Exception as err:  # noqa: BLE001 - Helvetica fallback must always work
        _LOGGER.warning("PDF Unicode fonts unavailable, using Helvetica: %s", err)
        return
    FONT = FONT_I = "RoadplannerSans"
    FONT_B = "RoadplannerSans-Bold"


def _wrap_lines(
    text: str, font: str, size: float, max_width: float, *, max_lines: int
) -> list[str]:
    """Greedy word wrap; the last permitted line is ellipsis-fitted."""
    words = str(text or "").split()
    lines: list[str] = []
    current = ""
    for index, word in enumerate(words):
        candidate = f"{current} {word}".strip()
        if pdfmetrics.stringWidth(candidate, font, size) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        if len(lines) == max_lines - 1:
            lines.append(_fit(" ".join(words[index:]), font, size, max_width))
            return lines
        current = word
    if current:
        lines.append(current)
    return lines[:max_lines]


def _fit(text: str, font: str, size: float, max_width: float) -> str:
    """Truncate ``text`` with an ellipsis so it fits ``max_width``."""
    if pdfmetrics.stringWidth(text, font, size) <= max_width:
        return text
    while text and pdfmetrics.stringWidth(text + "…", font, size) > max_width:
        text = text[:-1]
    return f"{text}…" if text else ""

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm

NAVY = HexColor("#0f2e3d")
TEAL = HexColor("#1c5d6b")
SAND = HexColor("#f4ede1")
ORANGE = HexColor("#e07a3f")
CREAM = HexColor("#faf6ef")
INK = HexColor("#20242a")
MUTED = HexColor("#6b7280")
WHITE = HexColor("#ffffff")
OLIVE = HexColor("#8a9a5b")

MAX_DAYS_RENDERED = 60
MAX_STOPS_PER_DAY_CHIP = 6
MAX_PHOTOS_PER_DAY = 2


@dataclass
class PdfCrewMember:
    name: str
    kind: str = "person"  # "person" | "dog"
    note: str = ""
    photo: bytes | None = None
    summary: str = ""


@dataclass
class PdfVehicle:
    name: str
    note: str = ""


@dataclass
class PdfStop:
    name: str
    stop_type: str = ""
    arrival_time: str = ""
    departure_time: str = ""


@dataclass
class PdfDay:
    title: str
    date: str
    stops: list[PdfStop] = field(default_factory=list)
    photos: list[bytes] = field(default_factory=list)
    distance_km: float | None = None
    duration_minutes: int | None = None
    highlights: list[str] = field(default_factory=list)


_STOP_TYPE_LABELS = {
    "start": "Start",
    "destination": "Ziel",
    "overnight": "Übernachtung",
    "campsite": "Campingplatz",
    "camping": "Camping",
    "stellplatz": "Stellplatz",
    "wildcamp": "Wildcamp",
    "accommodation": "Unterkunft",
    "parking": "Parken",
    "sightseeing": "Sightseeing",
    "attraction": "Sehenswürdigkeit",
    "activity": "Aktivität",
    "restaurant": "Restaurant",
    "shopping": "Einkaufen",
    "fuel": "Tanken",
    "charging": "Laden",
    "service": "Service",
    "water": "Wasser",
    "waste": "Entsorgung",
    "laundry": "Wäsche",
    "ferry": "Fähre",
    "border": "Grenze",
    "break": "Pause",
    "viewpoint": "Aussichtspunkt",
    "fishing": "Angeln",
    "waypoint": "Zwischenstopp",
}


@dataclass
class TripPdfData:
    title: str
    start_date: str
    end_date: str
    crew: list[PdfCrewMember] = field(default_factory=list)
    vehicle: PdfVehicle | None = None
    days: list[PdfDay] = field(default_factory=list)
    total_distance_km: float = 0.0
    country_count: int = 0
    route_map: bytes | None = None


def _rounded_rect(c, x, y, w, h, r, fill, stroke=None) -> None:
    c.setFillColor(fill)
    if stroke:
        c.setStrokeColor(stroke)
        c.setLineWidth(0.75)
    c.roundRect(x, y, w, h, r, fill=1, stroke=1 if stroke else 0)


def _footer(c, label: str) -> None:
    c.setFillColor(MUTED)
    c.setFont(FONT, 8)
    c.drawString(MARGIN, 10 * mm, "Roadplanner – Reise-Rückblick")
    c.drawRightString(PAGE_W - MARGIN, 10 * mm, label)


def _wrap_text(c, text: str, x: float, y: float, max_width: float, leading: float) -> float:
    words = text.split()
    line = ""
    cur_y = y
    for word in words:
        candidate = f"{line} {word}".strip()
        if pdfmetrics.stringWidth(candidate, FONT_I, 11.5) > max_width:
            c.drawString(x, cur_y, line)
            cur_y -= leading
            line = word
        else:
            line = candidate
    if line:
        c.drawString(x, cur_y, line)
        cur_y -= leading
    return cur_y


def _icon_placeholder(c, x, y, w, h, label, tone, *, icon: str = "camera") -> None:
    """Draw a flat glyph placeholder when no real photo is available."""
    _rounded_rect(c, x, y, w, h, 4 * mm, tone)
    c.saveState()
    c.setFillColor(WHITE)
    c.setStrokeColor(WHITE)
    cx, cy = x + w / 2, y + h / 2 + (3 * mm if icon == "camera" else 1 * mm)

    if icon == "person":
        c.circle(cx, cy + 4 * mm, 4 * mm, fill=0, stroke=1)
        c.roundRect(cx - 6 * mm, cy - 8 * mm, 12 * mm, 9 * mm, 4 * mm, fill=0, stroke=1)
    elif icon == "dog":
        c.circle(cx, cy + 1 * mm, 5 * mm, fill=0, stroke=1)
        c.ellipse(cx - 7 * mm, cy + 3 * mm, cx - 3 * mm, cy + 8 * mm, fill=0, stroke=1)
        c.ellipse(cx + 3 * mm, cy + 3 * mm, cx + 7 * mm, cy + 8 * mm, fill=0, stroke=1)
    elif icon == "camper":
        # A high-roof camper van (Ford Nugget Plus style): van body with a
        # sloped windscreen and a raised roof box - not the boxy caravan
        # trailer drawn before (live report).
        body_w, body_h = 21 * mm, 7.5 * mm
        body_x, body_y = cx - body_w / 2, cy - body_h / 2
        nose = body_w * 0.24
        path = c.beginPath()
        path.moveTo(body_x, body_y + body_h)          # rear top
        path.lineTo(body_x + body_w - nose, body_y + body_h)  # cabin top
        path.lineTo(body_x + body_w, body_y + body_h * 0.45)  # windscreen
        path.lineTo(body_x + body_w, body_y)          # bumper
        path.lineTo(body_x, body_y)                   # underside
        path.close()
        c.drawPath(path, fill=0, stroke=1)
        # Raised roof, slightly shorter than the body and set back.
        roof_w = body_w * 0.62
        c.roundRect(
            body_x + body_w * 0.06,
            body_y + body_h,
            roof_w,
            3.2 * mm,
            0.8 * mm,
            fill=0,
            stroke=1,
        )
        # Side window and wheels.
        c.rect(
            body_x + body_w * 0.36,
            body_y + body_h * 0.42,
            body_w * 0.22,
            body_h * 0.36,
            fill=0,
            stroke=1,
        )
        c.circle(body_x + body_w * 0.24, body_y, 2.1 * mm, fill=0, stroke=1)
        c.circle(body_x + body_w * 0.79, body_y, 2.1 * mm, fill=0, stroke=1)
    else:
        icon_w, icon_h = 13 * mm, 9 * mm
        icon_x = x + w / 2 - icon_w / 2
        icon_y = y + h / 2 - 1 * mm
        c.roundRect(icon_x, icon_y, icon_w, icon_h, 1.5 * mm, fill=0, stroke=1)
        c.circle(icon_x + icon_w / 2, icon_y + icon_h / 2, 2.6 * mm, fill=0, stroke=1)
        c.rect(icon_x + icon_w * 0.62, icon_y + icon_h - 1.2 * mm, icon_w * 0.22, 2 * mm, fill=1, stroke=0)

    if icon == "camera" or w > 40 * mm:
        c.setFont(FONT_B, 9)
        c.drawCentredString(x + w / 2, y + h / 2 - 11 * mm, label)
    c.restoreState()


def _image_format_hint(photo: bytes) -> str:
    """Name the image container so an undecodable photo is diagnosable."""
    head = photo[:16]
    if head[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if head[4:12] in (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypmif1"):
        return "HEIC (von Pillow nicht unterstützt)"
    if head[:4] == b"RIFF" and photo[8:12] == b"WEBP":
        return "WEBP"
    return "unbekanntes Format"


def _decode_photo(photo: bytes | None) -> tuple[ImageReader, int, int] | None:
    """Return a fully-decoded (reader, width, height), or None if unusable.

    A day with no real, usable photo simply gets no photo tile at all - a
    generic camera-icon filler would only make a personal trip retrospective
    look assembled rather than curated. Decoding is forced here (not just a
    header read) so a truncated download - valid header, incomplete body -
    is caught now rather than crashing later, inside drawImage().
    """
    if not photo:
        return None
    try:
        image = ImageReader(io.BytesIO(photo))
        iw, ih = image.getSize()
        if not iw or not ih:
            return None
        image.getRGBData()
        return image, iw, ih
    except Exception as err:  # noqa: BLE001 - a corrupt/unsupported/truncated photo must not abort the PDF
        _LOGGER.warning(
            "PDF photo could not be decoded (%s, %s): %s",
            _image_format_hint(photo),
            f"{len(photo)} Bytes",
            type(err).__name__,
        )
        return None


def _draw_photo(c, x: float, y: float, w: float, h: float, image: ImageReader, iw: int, ih: int) -> None:
    """Draw an already-decoded photo, cropped to fill the frame."""
    c.saveState()
    try:
        path = c.beginPath()
        path.roundRect(x, y, w, h, 4 * mm)
        c.clipPath(path, stroke=0, fill=0)
        # "cover" fit: scale so the image fills the frame, cropping the overflow.
        scale = max(w / iw, h / ih)
        draw_w, draw_h = iw * scale, ih * scale
        draw_x = x + (w - draw_w) / 2
        draw_y = y + (h - draw_h) / 2
        c.drawImage(image, draw_x, draw_y, draw_w, draw_h, mask="auto")
    finally:
        c.restoreState()


def _cover_page(c, data: TripPdfData) -> None:
    c.setFillColor(NAVY)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    c.setFillColor(TEAL)
    c.rect(0, PAGE_H * 0.42, PAGE_W, PAGE_H * 0.18, fill=1, stroke=0)
    c.setFillColor(ORANGE)
    c.circle(PAGE_W - 55 * mm, PAGE_H * 0.58, 16 * mm, fill=1, stroke=0)

    c.setStrokeColor(SAND)
    c.setLineWidth(1.4)
    day_count = max(1, len(data.days))
    stops = [min(7, day_count)]
    xs = [30 * mm + i * (PAGE_W - 60 * mm) / 6 for i in range(7)]
    y_route = PAGE_H * 0.46
    c.setDash(2, 3)
    c.line(xs[0], y_route, xs[-1], y_route)
    c.setDash()
    for x in xs:
        c.setFillColor(SAND)
        c.circle(x, y_route, 1.6 * mm, fill=1, stroke=0)
    del stops

    c.setFillColor(WHITE)
    c.setFont(FONT_B, 30 if len(data.title) > 28 else 34)
    c.drawString(MARGIN, PAGE_H * 0.30, data.title)

    c.setFont(FONT, 13)
    c.setFillColor(SAND)
    date_range = (
        f"{data.start_date} – {data.end_date}"
        if data.start_date and data.end_date
        else data.start_date or data.end_date or ""
    )
    if date_range:
        c.drawString(MARGIN, PAGE_H * 0.30 - 10 * mm, date_range)

    c.setFont(FONT_I, 12)
    c.setFillColor(HexColor("#cfe3e6"))
    c.drawString(MARGIN, PAGE_H * 0.30 - 18 * mm, "Ein Reise-Rückblick")

    chip_y = 28 * mm
    x = MARGIN
    names = [member.name for member in data.crew]
    if data.vehicle:
        names.append(data.vehicle.name)
    for name in names:
        w = pdfmetrics.stringWidth(name, FONT_B, 11) + 14 * mm
        if x + w > PAGE_W - MARGIN:
            break
        _rounded_rect(c, x, chip_y, w, 9 * mm, 4.5 * mm, HexColor("#12414f"))
        c.setFillColor(WHITE)
        c.setFont(FONT_B, 11)
        c.drawString(x + 7 * mm, chip_y + 3 * mm, name)
        x += w + 4 * mm

    _footer(c, "1")
    c.showPage()


def _crew_page(c, data: TripPdfData) -> int:
    if not data.crew and not data.vehicle:
        return 0
    c.setFillColor(CREAM)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    entries: list[PdfCrewMember] = list(data.crew)
    if data.vehicle:
        entries.append(
            PdfCrewMember(
                name=data.vehicle.name, kind="camper", note=data.vehicle.note
            )
        )

    card_h = 44 * mm
    gap = 7 * mm
    photo_size = card_h - 8 * mm
    top = PAGE_H - 50 * mm
    max_rows = max(1, int((top - 16 * mm) // (card_h + gap)))
    pages = 0
    for page_start in range(0, len(entries), max_rows):
        if pages:
            c.setFillColor(CREAM)
            c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
        c.setFillColor(NAVY)
        c.setFont(FONT_B, 24)
        c.drawString(MARGIN, PAGE_H - 30 * mm, "Die Crew")
        c.setStrokeColor(ORANGE)
        c.setLineWidth(2)
        c.line(MARGIN, PAGE_H - 34 * mm, MARGIN + 26 * mm, PAGE_H - 34 * mm)
        for index, member in enumerate(entries[page_start:page_start + max_rows]):
            y = top - index * (card_h + gap)
            _rounded_rect(
                c, MARGIN, y - card_h, PAGE_W - 2 * MARGIN, card_h, 5 * mm,
                WHITE, stroke=HexColor("#e4dccb"),
            )
            photo_x = MARGIN + 4 * mm
            photo_y = y - card_h + 4 * mm
            decoded = _decode_photo(member.photo)
            if decoded is not None:
                image, iw, ih = decoded
                _draw_photo(c, photo_x, photo_y, photo_size, photo_size, image, iw, ih)
            else:
                tone = {"dog": ORANGE, "camper": OLIVE}.get(member.kind, TEAL)
                _icon_placeholder(
                    c, photo_x, photo_y, photo_size, photo_size,
                    member.name, tone, icon=member.kind,
                )

            text_x = photo_x + photo_size + 8 * mm
            max_text_width = PAGE_W - MARGIN - 6 * mm - text_x
            c.setFillColor(INK)
            c.setFont(FONT_B, 15)
            c.drawString(text_x, y - 10 * mm, member.name)
            if member.note:
                c.setFillColor(MUTED)
                c.setFont(FONT, 10)
                # Fitted to the card - long notes ran off the page edge
                # (live report).
                c.drawString(
                    text_x,
                    y - 16.5 * mm,
                    _fit(member.note, FONT, 10, max_text_width),
                )
            if member.summary:
                # The personal part: what this person experienced on the
                # trip, distilled from their photo captions.
                c.setFillColor(TEAL)
                c.setFont(FONT, 9.5)
                summary_y = y - 23 * mm
                for line in _wrap_lines(
                    member.summary, FONT, 9.5, max_text_width, max_lines=4
                ):
                    c.drawString(text_x, summary_y, line)
                    summary_y -= 4.6 * mm
        pages += 1
        _footer(c, str(1 + pages))
        c.showPage()
    return pages


def _route_page(c, data: TripPdfData, page_number: int) -> int:
    titled_days = [
        (index + 1, day.title) for index, day in enumerate(data.days) if day.title
    ]
    if len(titled_days) < 2:
        return 0
    # Sample at most 12 nodes EVENLY across the whole trip (first and last
    # always included) - previously the first 12 of 28 days were shown,
    # numbered as if they were the whole route.
    node_count = min(12, len(titled_days))
    sampled = [
        titled_days[round(i * (len(titled_days) - 1) / (node_count - 1))]
        for i in range(node_count)
    ]
    names = sampled
    c.setFillColor(WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    c.setFillColor(NAVY)
    c.setFont(FONT_B, 22)
    c.drawString(MARGIN, PAGE_H - 28 * mm, "Die Route")

    real_map = _decode_photo(data.route_map)
    if real_map is not None:
        # A real map of where the trip actually went - the schematic zigzag
        # below is only the fallback when no coordinates exist (live report:
        # "Karte macht keinen Sinn").
        c.setFillColor(MUTED)
        c.setFont(FONT, 9.5)
        c.drawString(
            MARGIN, PAGE_H - 34 * mm, "Die tatsächlich gefahrene Route."
        )
        image, iw, ih = real_map
        map_w = PAGE_W - 2 * MARGIN
        map_h = min(PAGE_H - 92 * mm, map_w * ih / iw if iw else map_w * 0.7)
        map_y = PAGE_H - 40 * mm - map_h
        _draw_photo(c, MARGIN, map_y, map_w, map_h, image, iw, ih)
        c.setFillColor(MUTED)
        c.setFont(FONT, 8)
        c.drawString(MARGIN, map_y - 6 * mm, "© OpenStreetMap contributors")
        legend_y = map_y - 14 * mm
        c.setFillColor(INK)
        c.setFont(FONT, 9)
        line = ""
        for day_number, name in names:
            entry = f"{day_number}. {name}"
            candidate = f"{line}   ·   {entry}" if line else entry
            if pdfmetrics.stringWidth(candidate, FONT, 9) > PAGE_W - 2 * MARGIN:
                c.drawString(MARGIN, legend_y, line)
                legend_y -= 5.5 * mm
                line = entry
                if legend_y < 20 * mm:
                    line = ""
                    break
            else:
                line = candidate
        if line:
            c.drawString(MARGIN, legend_y, line)
        _footer(c, str(page_number))
        c.showPage()
        return 1

    c.setFillColor(MUTED)
    c.setFont(FONT, 9.5)
    c.drawString(MARGIN, PAGE_H - 34 * mm, "Schematische Übersicht der Reisetage.")

    map_x, map_y = MARGIN, 40 * mm
    map_w, map_h = PAGE_W - 2 * MARGIN, PAGE_H - 90 * mm
    _rounded_rect(c, map_x, map_y, map_w, map_h, 6 * mm, HexColor("#eaf2ee"), stroke=HexColor("#cfe0d8"))

    n = len(names)
    pts = []
    for i, (day_number, name) in enumerate(names):
        t = i / (n - 1)
        x = map_x + 18 * mm + t * (map_w - 36 * mm)
        wobble = math.sin(t * math.pi * 2.2) * (map_h * 0.18)
        y = map_y + map_h / 2 + wobble
        pts.append((x, y, name, wobble, day_number))

    c.setStrokeColor(TEAL)
    c.setLineWidth(2.2)
    path = c.beginPath()
    path.moveTo(*pts[0][:2])
    for x, y, _, _, _ in pts[1:]:
        path.lineTo(x, y)
    c.drawPath(path, stroke=1, fill=0)

    # Labels pick the first candidate offset whose box collides neither
    # with an already placed label nor with any node circle - before,
    # adjacent labels overprinted each other and the nodes into
    # unreadable mush (live report).
    placed_boxes: list[tuple[float, float, float, float]] = []
    node_boxes = [
        (px - 4 * mm, py - 4 * mm, px + 4 * mm, py + 4 * mm)
        for px, py, _, _, _ in pts
    ]
    if data.vehicle:
        # Reserve the vehicle badge's area (drawn below) so no label ends
        # up underneath it.
        badge_nx, badge_ny = pts[n // 2][0], pts[n // 2][1]
        node_boxes.append(
            (
                badge_nx + 6 * mm,
                badge_ny + 12 * mm,
                badge_nx + 28 * mm,
                badge_ny + 33 * mm,
            )
        )

    def _collides(box: tuple[float, float, float, float]) -> bool:
        for other in placed_boxes + node_boxes:
            if (
                box[0] < other[2]
                and box[2] > other[0]
                and box[1] < other[3]
                and box[3] > other[1]
            ):
                return True
        return False

    label_offsets = (
        9 * mm, -12 * mm, 15 * mm, -18 * mm, 21 * mm, -24 * mm, 27 * mm
    )
    for i, (x, y, name, _wobble, day_number) in enumerate(pts):
        color = ORANGE if i in (0, len(pts) - 1) else NAVY
        c.setFillColor(color)
        c.circle(x, y, 3 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont(FONT_B, 8)
        c.drawCentredString(x, y - 2.6, str(day_number))
        label = _fit(name, FONT, 8, 34 * mm)
        label_w = pdfmetrics.stringWidth(label, FONT, 8)
        label_x = min(max(x, map_x + 4 * mm + label_w / 2), map_x + map_w - 4 * mm - label_w / 2)
        for offset in label_offsets:
            label_y = y + offset
            box = (
                label_x - label_w / 2 - 1 * mm,
                label_y - 1.2 * mm,
                label_x + label_w / 2 + 1 * mm,
                label_y + 2.6 * mm,
            )
            if not _collides(box) and map_y + 3 * mm < label_y < map_y + map_h - 5 * mm:
                placed_boxes.append(box)
                c.setFillColor(INK)
                c.setFont(FONT, 8)
                c.drawCentredString(label_x, label_y, label)
                break

    if data.vehicle:
        idx = n // 2
        nx, ny = pts[idx][0], pts[idx][1]
        badge_w, badge_h = 16 * mm, 11 * mm
        badge_x, badge_y = nx + 8 * mm, ny + 14 * mm
        c.setStrokeColor(ORANGE)
        c.setLineWidth(1)
        c.setDash(1, 2)
        c.line(nx, ny, badge_x + badge_w / 2, badge_y - 3 * mm)
        c.setDash()
        _rounded_rect(c, badge_x, badge_y, badge_w, badge_h, 2.5 * mm, OLIVE)
        c.saveState()
        c.setFillColor(WHITE)
        c.setStrokeColor(WHITE)
        body_w, body_h = 10 * mm, 4.6 * mm
        bx = badge_x + badge_w / 2 - body_w / 2
        by = badge_y + badge_h / 2 - body_h / 2 + 0.3 * mm
        c.roundRect(bx, by, body_w, body_h, 1 * mm, fill=0, stroke=1)
        c.line(bx + body_w * 0.62, by, bx + body_w * 0.62, by + body_h)
        c.rect(bx + body_w * 0.72, by + body_h * 0.3, body_w * 0.18, body_h * 0.4, fill=0, stroke=1)
        c.circle(bx + body_w * 0.22, by - 0.3 * mm, 1.1 * mm, fill=0, stroke=1)
        c.circle(bx + body_w * 0.82, by - 0.3 * mm, 1.1 * mm, fill=0, stroke=1)
        c.restoreState()
        c.setFillColor(INK)
        c.setFont(FONT_B, 8.5)
        c.drawCentredString(badge_x + badge_w / 2, badge_y + badge_h + 3 * mm, data.vehicle.name[:16])

    _footer(c, str(page_number))
    c.showPage()
    return 1


def _day_page(c, day: PdfDay, index: int, total: int, page_number: int) -> None:
    c.setFillColor(SAND)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    c.setFillColor(ORANGE)
    c.rect(0, PAGE_H - 6 * mm, PAGE_W, 6 * mm, fill=1, stroke=0)

    c.setFillColor(NAVY)
    c.setFont(FONT_B, 10)
    c.drawString(MARGIN, PAGE_H - 20 * mm, f"TAG {index} / {total}")
    c.setFont(FONT_B, 22)
    c.drawString(MARGIN, PAGE_H - 31 * mm, day.title[:60] or f"Tag {index}")
    meta_parts = [part for part in (day.date,) if part]
    if day.distance_km:
        meta_parts.append(f"{day.distance_km:.0f} km")
    if day.duration_minutes:
        hours, minutes = divmod(int(day.duration_minutes), 60)
        meta_parts.append(f"{hours} h {minutes:02d} min Fahrzeit" if hours else f"{minutes} min Fahrzeit")
    if meta_parts:
        c.setFillColor(MUTED)
        c.setFont(FONT, 10.5)
        c.drawString(MARGIN, PAGE_H - 38 * mm, "  ·  ".join(meta_parts))

    # Highlight chips: a tiny at-a-glance description of the day (live
    # request: "eine kleine Beschreibung ... als Stichpunkte").
    if day.highlights:
        chip_y = PAGE_H - 47 * mm
        x = MARGIN
        c.setFont(FONT_B, 9)
        for highlight in day.highlights[:4]:
            label = _fit(highlight, FONT_B, 9, 62 * mm)
            w = pdfmetrics.stringWidth(label, FONT_B, 9) + 11 * mm
            if x + w > PAGE_W - MARGIN:
                break
            _rounded_rect(c, x, chip_y, w, 8 * mm, 4 * mm, WHITE, stroke=HexColor("#d8cfba"))
            c.setFillColor(ORANGE)
            c.circle(x + 4.4 * mm, chip_y + 4 * mm, 1.2 * mm, fill=1, stroke=0)
            c.setFillColor(INK)
            c.drawString(x + 7.2 * mm, chip_y + 2.6 * mm, label)
            x += w + 4 * mm

    content_top = PAGE_H - (56 * mm if day.highlights else 48 * mm)
    photo_h = 72 * mm
    photo_y = content_top - photo_h
    full_w = PAGE_W - 2 * MARGIN
    gap = 8 * mm
    decoded_photos = [decoded for photo in day.photos[:2] if (decoded := _decode_photo(photo))]
    if len(decoded_photos) == 2:
        photo_w = (full_w - gap) / 2
        for i, (image, iw, ih) in enumerate(decoded_photos):
            x = MARGIN + i * (photo_w + gap)
            _draw_photo(c, x, photo_y, photo_w, photo_h, image, iw, ih)
        list_top = photo_y - 12 * mm
    elif len(decoded_photos) == 1:
        image, iw, ih = decoded_photos[0]
        _draw_photo(c, MARGIN, photo_y, full_w, photo_h, image, iw, ih)
        list_top = photo_y - 12 * mm
    else:
        # No real, usable photo for this day - reclaim the photo area for
        # the stop list instead of drawing a generic icon filler.
        list_top = content_top - 4 * mm

    # The day's stops as a real list with type and times - the old chip row
    # fit two or three names and left the rest of the page empty (live
    # report: "eine Zumutung").
    row_h = 9 * mm
    y = list_top
    for stop in day.stops:
        if y < 22 * mm:
            remaining = len(day.stops) - day.stops.index(stop)
            c.setFillColor(MUTED)
            c.setFont(FONT, 9)
            c.drawString(MARGIN, y, f"… und {remaining} weitere Stopps")
            break
        c.setFillColor(ORANGE)
        c.circle(MARGIN + 1.6 * mm, y + 1.4 * mm, 1.4 * mm, fill=1, stroke=0)
        c.setFillColor(INK)
        c.setFont(FONT_B, 11)
        name_width = (PAGE_W - 2 * MARGIN) * 0.62
        c.drawString(MARGIN + 6 * mm, y, _fit(stop.name, FONT_B, 11, name_width))
        detail_parts = []
        type_label = _STOP_TYPE_LABELS.get(
            stop.stop_type.casefold(), stop.stop_type.capitalize()
        ) if stop.stop_type else ""
        if type_label:
            detail_parts.append(type_label)
        if stop.arrival_time:
            detail_parts.append(f"an {stop.arrival_time}")
        if stop.departure_time:
            detail_parts.append(f"ab {stop.departure_time}")
        if detail_parts:
            c.setFillColor(MUTED)
            c.setFont(FONT, 9.5)
            c.drawRightString(
                PAGE_W - MARGIN,
                y,
                _fit(" · ".join(detail_parts), FONT, 9.5, (PAGE_W - 2 * MARGIN) * 0.34),
            )
        y -= row_h

    c.setFillColor(NAVY)
    c.rect(0, 0, PAGE_W, 5 * mm, fill=1, stroke=0)
    _footer(c, str(page_number))
    c.showPage()


def _closing_page(c, data: TripPdfData, page_number: int) -> None:
    c.setFillColor(NAVY)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont(FONT_B, 26)
    c.drawCentredString(PAGE_W / 2, PAGE_H * 0.62, "Danke fürs Mitreisen")

    total_stops = sum(len(day.stops) for day in data.days)
    entries = [
        (str(len(data.days)), "Tage"),
        (f"{round(data.total_distance_km):,}".replace(",", "."), "gefahrene km"),
        (str(data.country_count), "Länder"),
        (str(total_stops), "Stopps"),
    ]
    box_w = (PAGE_W - 2 * MARGIN - 3 * 8 * mm) / 4
    x = MARGIN
    y = PAGE_H * 0.40
    for value, label in entries:
        _rounded_rect(c, x, y, box_w, 26 * mm, 4 * mm, HexColor("#12414f"))
        c.setFillColor(ORANGE)
        c.setFont(FONT_B, 18)
        c.drawCentredString(x + box_w / 2, y + 16 * mm, value)
        c.setFillColor(SAND)
        c.setFont(FONT, 9)
        c.drawCentredString(x + box_w / 2, y + 8 * mm, label)
        x += box_w + 8 * mm

    c.setFillColor(HexColor("#9fb8bd"))
    c.setFont(FONT, 9)
    c.drawCentredString(PAGE_W / 2, 20 * mm, "Erstellt mit Roadplanner")
    _footer(c, str(page_number))
    c.showPage()


def build_trip_pdf(data: TripPdfData) -> bytes:
    """Render the full trip-summary PDF and return its bytes."""
    _ensure_fonts()
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    _cover_page(c, data)
    page_number = 1 + _crew_page(c, data)
    page_number += _route_page(c, data, page_number + 1)

    days = data.days[:MAX_DAYS_RENDERED]
    total = len(days)
    for index, day in enumerate(days, start=1):
        page_number += 1
        _day_page(c, day, index, total, page_number)

    _closing_page(c, data, page_number + 1)
    c.save()
    return buffer.getvalue()
