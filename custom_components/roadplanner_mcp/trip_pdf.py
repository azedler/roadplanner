"""Render a Roadplanner trip summary as a PDF.

Pure, synchronous rendering only - no Home Assistant, no I/O beyond building
bytes in memory. Callers gather trip/crew/photo data asynchronously first
(see trip_pdf_export.py) and run this module's build function in an executor,
since reportlab's canvas drawing is CPU-bound.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import io
import math

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

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


@dataclass
class PdfVehicle:
    name: str
    note: str = ""


@dataclass
class PdfStop:
    name: str
    stop_type: str = ""


@dataclass
class PdfDay:
    title: str
    date: str
    stops: list[PdfStop] = field(default_factory=list)
    photos: list[bytes] = field(default_factory=list)


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


def _rounded_rect(c, x, y, w, h, r, fill, stroke=None) -> None:
    c.setFillColor(fill)
    if stroke:
        c.setStrokeColor(stroke)
        c.setLineWidth(0.75)
    c.roundRect(x, y, w, h, r, fill=1, stroke=1 if stroke else 0)


def _footer(c, label: str) -> None:
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 8)
    c.drawString(MARGIN, 10 * mm, "Roadplanner – Reise-Rückblick")
    c.drawRightString(PAGE_W - MARGIN, 10 * mm, label)


def _wrap_text(c, text: str, x: float, y: float, max_width: float, leading: float) -> float:
    words = text.split()
    line = ""
    cur_y = y
    for word in words:
        candidate = f"{line} {word}".strip()
        if pdfmetrics.stringWidth(candidate, "Helvetica-Oblique", 11.5) > max_width:
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
        body_w, body_h = 20 * mm, 9 * mm
        body_x, body_y = cx - body_w / 2, cy - body_h / 2
        c.roundRect(body_x, body_y, body_w, body_h, 1.5 * mm, fill=0, stroke=1)
        c.line(body_x + body_w * 0.62, body_y, body_x + body_w * 0.62, body_y + body_h)
        c.rect(body_x + body_w * 0.72, body_y + body_h * 0.3, body_w * 0.18, body_h * 0.4, fill=0, stroke=1)
        c.circle(body_x + body_w * 0.22, body_y - 0.5 * mm, 2.2 * mm, fill=0, stroke=1)
        c.circle(body_x + body_w * 0.82, body_y - 0.5 * mm, 2.2 * mm, fill=0, stroke=1)
    else:
        icon_w, icon_h = 13 * mm, 9 * mm
        icon_x = x + w / 2 - icon_w / 2
        icon_y = y + h / 2 - 1 * mm
        c.roundRect(icon_x, icon_y, icon_w, icon_h, 1.5 * mm, fill=0, stroke=1)
        c.circle(icon_x + icon_w / 2, icon_y + icon_h / 2, 2.6 * mm, fill=0, stroke=1)
        c.rect(icon_x + icon_w * 0.62, icon_y + icon_h - 1.2 * mm, icon_w * 0.22, 2 * mm, fill=1, stroke=0)

    if icon == "camera" or w > 40 * mm:
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(x + w / 2, y + h / 2 - 11 * mm, label)
    c.restoreState()


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
    except Exception:  # noqa: BLE001 - a corrupt/unsupported/truncated photo must not abort the PDF
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
    c.setFont("Helvetica-Bold", 30 if len(data.title) > 28 else 34)
    c.drawString(MARGIN, PAGE_H * 0.30, data.title)

    c.setFont("Helvetica", 13)
    c.setFillColor(SAND)
    date_range = (
        f"{data.start_date} – {data.end_date}"
        if data.start_date and data.end_date
        else data.start_date or data.end_date or ""
    )
    if date_range:
        c.drawString(MARGIN, PAGE_H * 0.30 - 10 * mm, date_range)

    c.setFont("Helvetica-Oblique", 12)
    c.setFillColor(HexColor("#cfe3e6"))
    c.drawString(MARGIN, PAGE_H * 0.30 - 18 * mm, "Ein Reise-Rückblick")

    chip_y = 28 * mm
    x = MARGIN
    names = [member.name for member in data.crew]
    if data.vehicle:
        names.append(data.vehicle.name)
    for name in names:
        w = pdfmetrics.stringWidth(name, "Helvetica-Bold", 11) + 14 * mm
        if x + w > PAGE_W - MARGIN:
            break
        _rounded_rect(c, x, chip_y, w, 9 * mm, 4.5 * mm, HexColor("#12414f"))
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(x + 7 * mm, chip_y + 3 * mm, name)
        x += w + 4 * mm

    _footer(c, "1")
    c.showPage()


def _crew_page(c, data: TripPdfData) -> int:
    if not data.crew and not data.vehicle:
        return 0
    c.setFillColor(CREAM)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 24)
    c.drawString(MARGIN, PAGE_H - 30 * mm, "Die Crew")
    c.setStrokeColor(ORANGE)
    c.setLineWidth(2)
    c.line(MARGIN, PAGE_H - 34 * mm, MARGIN + 26 * mm, PAGE_H - 34 * mm)

    entries: list[tuple[str, str, str]] = [
        (member.name, member.note, member.kind) for member in data.crew
    ]
    if data.vehicle:
        entries.append((data.vehicle.name, data.vehicle.note, "camper"))

    card_h = 34 * mm
    gap = 8 * mm
    photo_size = card_h - 8 * mm
    top = PAGE_H - 50 * mm
    max_rows = int((top - 16 * mm) // (card_h + gap))
    for index, (name, note, kind) in enumerate(entries[:max_rows]):
        y = top - index * (card_h + gap)
        _rounded_rect(
            c, MARGIN, y - card_h, PAGE_W - 2 * MARGIN, card_h, 5 * mm,
            WHITE, stroke=HexColor("#e4dccb"),
        )
        photo_x = MARGIN + 4 * mm
        photo_y = y - card_h + 4 * mm
        tone = {"dog": ORANGE, "camper": OLIVE}.get(kind, TEAL)
        _icon_placeholder(c, photo_x, photo_y, photo_size, photo_size, name, tone, icon=kind)

        text_x = photo_x + photo_size + 8 * mm
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(text_x, y - card_h / 2 + 3 * mm, name)
        if note:
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 10.5)
            c.drawString(text_x, y - card_h / 2 - 5 * mm, note[:90])

    _footer(c, "2")
    c.showPage()
    return 1


def _route_page(c, data: TripPdfData, page_number: int) -> int:
    names = [day.title for day in data.days if day.title][:12]
    if len(names) < 2:
        return 0
    c.setFillColor(WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(MARGIN, PAGE_H - 28 * mm, "Die Route")
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 9.5)
    c.drawString(MARGIN, PAGE_H - 34 * mm, "Schematische Übersicht der Reisetage.")

    map_x, map_y = MARGIN, 40 * mm
    map_w, map_h = PAGE_W - 2 * MARGIN, PAGE_H - 90 * mm
    _rounded_rect(c, map_x, map_y, map_w, map_h, 6 * mm, HexColor("#eaf2ee"), stroke=HexColor("#cfe0d8"))

    n = len(names)
    pts = []
    for i, name in enumerate(names):
        t = i / (n - 1)
        x = map_x + 18 * mm + t * (map_w - 36 * mm)
        wobble = math.sin(t * math.pi * 2.2) * (map_h * 0.18)
        y = map_y + map_h / 2 + wobble
        pts.append((x, y, name, wobble))

    c.setStrokeColor(TEAL)
    c.setLineWidth(2.2)
    path = c.beginPath()
    path.moveTo(*pts[0][:2])
    for x, y, _, _ in pts[1:]:
        path.lineTo(x, y)
    c.drawPath(path, stroke=1, fill=0)

    for i, (x, y, name, wobble) in enumerate(pts):
        color = ORANGE if i in (0, len(pts) - 1) else NAVY
        c.setFillColor(color)
        c.circle(x, y, 3 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(x, y - 2.6, str(i + 1))
        c.setFillColor(INK)
        c.setFont("Helvetica", 9)
        label_y = y - 11 * mm if wobble >= 0 else y + 7 * mm
        c.drawCentredString(x, label_y, name[:24])

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
        c.setFont("Helvetica-Bold", 8.5)
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
    c.setFont("Helvetica-Bold", 10)
    c.drawString(MARGIN, PAGE_H - 20 * mm, f"TAG {index} / {total}")
    c.setFont("Helvetica-Bold", 22)
    c.drawString(MARGIN, PAGE_H - 31 * mm, day.title[:60] or f"Tag {index}")
    if day.date:
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 10.5)
        c.drawString(MARGIN, PAGE_H - 38 * mm, day.date)

    photo_y = PAGE_H - 118 * mm
    photo_h = 76 * mm
    full_w = PAGE_W - 2 * MARGIN
    gap = 8 * mm
    decoded_photos = [decoded for photo in day.photos[:2] if (decoded := _decode_photo(photo))]
    if len(decoded_photos) == 2:
        photo_w = (full_w - gap) / 2
        for i, (image, iw, ih) in enumerate(decoded_photos):
            x = MARGIN + i * (photo_w + gap)
            _draw_photo(c, x, photo_y, photo_w, photo_h, image, iw, ih)
        chip_y = photo_y - 14 * mm
    elif len(decoded_photos) == 1:
        image, iw, ih = decoded_photos[0]
        _draw_photo(c, MARGIN, photo_y, full_w, photo_h, image, iw, ih)
        chip_y = photo_y - 14 * mm
    else:
        # No real, usable photo for this day - reclaim the photo area
        # instead of drawing a generic icon filler.
        chip_y = PAGE_H - 56 * mm
    x = MARGIN
    c.setFont("Helvetica-Bold", 9)
    for stop in day.stops[:MAX_STOPS_PER_DAY_CHIP]:
        label = f"● {stop.name}"[:60]
        w = pdfmetrics.stringWidth(label, "Helvetica-Bold", 9) + 10 * mm
        if x + w > PAGE_W - MARGIN:
            break
        _rounded_rect(c, x, chip_y, w, 8 * mm, 4 * mm, WHITE, stroke=HexColor("#d8cfba"))
        c.setFillColor(ORANGE)
        c.drawString(x + 5 * mm, chip_y + 2.6 * mm, label)
        x += w + 4 * mm

    c.setFillColor(NAVY)
    c.rect(0, 0, PAGE_W, 5 * mm, fill=1, stroke=0)
    _footer(c, str(page_number))
    c.showPage()


def _closing_page(c, data: TripPdfData, page_number: int) -> None:
    c.setFillColor(NAVY)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 26)
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
        c.setFont("Helvetica-Bold", 18)
        c.drawCentredString(x + box_w / 2, y + 16 * mm, value)
        c.setFillColor(SAND)
        c.setFont("Helvetica", 9)
        c.drawCentredString(x + box_w / 2, y + 8 * mm, label)
        x += box_w + 8 * mm

    c.setFillColor(HexColor("#9fb8bd"))
    c.setFont("Helvetica", 9)
    c.drawCentredString(PAGE_W / 2, 20 * mm, "Erstellt mit Roadplanner")
    _footer(c, str(page_number))
    c.showPage()


def build_trip_pdf(data: TripPdfData) -> bytes:
    """Render the full trip-summary PDF and return its bytes."""
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
