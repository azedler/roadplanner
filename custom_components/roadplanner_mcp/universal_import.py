"""Safe parsing helpers for the Roadplanner universal importer.

The module deliberately performs deterministic parsing for well-known exchange
formats and leaves semantic interpretation of free-form documents to the
configured assistant provider.  It never mutates the Roadbook.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import csv
import io
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any
from xml.etree import ElementTree
import zipfile

from .roadplanner import ValidationError

MAX_IMPORT_TEXT_CHARACTERS = 220_000
MAX_IMPORT_PREVIEW_ITEMS = 100
MAX_IMPORT_DRAFTS = 50
MAX_ZIP_FILES = 50
MAX_ZIP_MEMBER_BYTES = 5 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 25 * 1024 * 1024

SUPPORTED_IMPORT_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".json",
    ".csv",
    ".gpx",
    ".ics",
    ".ical",
    ".zip",
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".heic",
    ".heif",
}

_TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".json", ".csv", ".ics", ".ical", ".gpx"}
_ZIP_TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".json", ".csv", ".ics", ".ical", ".gpx"}

_CHANGESET_BEGIN = "ROADPLANNER_CHANGESET_BEGIN"
_CHANGESET_END = "ROADPLANNER_CHANGESET_END"


@dataclass(slots=True)
class ParsedImport:
    """Intermediate import result before optional semantic analysis."""

    format: str
    title: str
    summary: str
    text: str = ""
    basket_delta: dict[str, Any] | None = None
    direct_changeset: dict[str, Any] | None = None
    preview_items: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "title": self.title,
            "summary": self.summary,
            "text": self.text,
            "basket_delta": self.basket_delta,
            "direct_changeset": self.direct_changeset,
            "preview_items": self.preview_items[:MAX_IMPORT_PREVIEW_ITEMS],
            "warnings": self.warnings[:50],
            "metadata": self.metadata,
        }


def _clean(value: Any, maximum: int = 4_000) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _decode_text(data: bytes) -> str:
    """Decode a bounded text payload with BOM support."""
    if len(data) > MAX_ZIP_TOTAL_BYTES:
        raise ValidationError("Die Importdatei ist für die Textanalyse zu groß")
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValidationError("Die Importdatei enthält keinen lesbaren Text")
    if "\x00" in text and not text.startswith("\ufeff"):
        text = text.replace("\x00", "")
    return text[:MAX_IMPORT_TEXT_CHARACTERS]


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _direct_changeset(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if value.get("kind") == "roadplanner_changeset":
        return value
    if value.get("kind") == "roadplanner_drive_import" and isinstance(value.get("changeset"), dict):
        nested = value["changeset"]
        if nested.get("kind") == "roadplanner_changeset":
            return nested
    return None


def extract_marked_changeset(text: str) -> dict[str, Any] | None:
    """Extract one marked Roadplanner ChangeSet from Markdown/plain text."""
    begin = text.find(_CHANGESET_BEGIN)
    if begin < 0:
        return None
    start = begin + len(_CHANGESET_BEGIN)
    end = text.find(_CHANGESET_END, start)
    if end < 0:
        return None
    raw = text[start:end].strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    return _direct_changeset(_json_object(raw))


def parse_text_import(data: bytes, filename: str) -> ParsedImport:
    text = _decode_text(data)
    direct = extract_marked_changeset(text)
    if direct is None:
        direct = _direct_changeset(_json_object(text.strip()))
    suffix = Path(filename).suffix.casefold()
    fmt = "markdown" if suffix in {".md", ".markdown"} else "json" if suffix == ".json" else "text"
    if direct is not None:
        operations = direct.get("operations") if isinstance(direct.get("operations"), list) else []
        return ParsedImport(
            format="roadplanner_changeset",
            title=_clean(direct.get("title") or direct.get("summary") or Path(filename).stem, 500),
            summary=f"Roadplanner-ChangeSet mit {len(operations)} Operationen erkannt.",
            text=text,
            direct_changeset=direct,
            preview_items=[
                {
                    "kind": "operation",
                    "title": _clean(item.get("reason") or item.get("operation_id") or "Operation", 500),
                    "subtitle": f"{_clean(item.get('action'), 50)} {_clean(item.get('entity_type'), 50)}".strip(),
                }
                for item in operations[:MAX_IMPORT_PREVIEW_ITEMS]
                if isinstance(item, dict)
            ],
            metadata={"operation_count": len(operations)},
        )
    heading = ""
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            heading = stripped[:500]
            break
    return ParsedImport(
        format=fmt,
        title=heading or Path(filename).stem or "Textübergabe",
        summary="Freier Reiseplan oder Übergabetext zur semantischen Analyse.",
        text=text,
        metadata={"characters": len(text)},
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _xml_child_text(node: ElementTree.Element, name: str) -> str:
    for child in node:
        if _local_name(child.tag) == name.casefold():
            return _clean(child.text, 2_000)
    return ""


def _coordinate_draft(
    *,
    name: str,
    latitude: float,
    longitude: float,
    notes: str = "",
    stop_type: str = "waypoint",
    day_date: str = "",
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "action": "add",
        "entity_type": "stop",
        "summary": f"Stopp aus Import ergänzen: {name}",
        "place_query": f"{latitude:.7f}, {longitude:.7f}",
        "reason": "Aus einer vom Benutzer importierten Routendatei übernommen.",
        "values": {
            "name": name[:500],
            "type": stop_type,
            "notes": notes[:5_000],
        },
    }
    if day_date:
        item["day_date"] = day_date
    return item


def parse_gpx_import(data: bytes, filename: str) -> ParsedImport:
    if len(data) > MAX_ZIP_TOTAL_BYTES:
        raise ValidationError("Die GPX-Datei ist zu groß")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as err:
        raise ValidationError("Die GPX-Datei ist nicht gültig") from err
    if _local_name(root.tag) != "gpx":
        raise ValidationError("Die XML-Datei ist kein GPX-Dokument")

    points: list[dict[str, Any]] = []
    track_points: list[tuple[float, float]] = []
    for node in root.iter():
        kind = _local_name(node.tag)
        if kind not in {"wpt", "rtept", "trkpt"}:
            continue
        try:
            lat = float(node.attrib.get("lat", ""))
            lon = float(node.attrib.get("lon", ""))
        except ValueError:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        if kind == "trkpt":
            track_points.append((lat, lon))
            continue
        name = _xml_child_text(node, "name") or _xml_child_text(node, "desc")
        if not name:
            name = "GPX-Wegpunkt"
        notes = _xml_child_text(node, "desc") or _xml_child_text(node, "cmt")
        point_type = "destination" if kind == "rtept" else "waypoint"
        points.append({"name": name, "lat": lat, "lon": lon, "notes": notes, "type": point_type})

    if not points and track_points:
        start = track_points[0]
        end = track_points[-1]
        points = [
            {"name": "GPX-Start", "lat": start[0], "lon": start[1], "notes": "Startpunkt des importierten Tracks", "type": "start"},
            {"name": "GPX-Ziel", "lat": end[0], "lon": end[1], "notes": "Endpunkt des importierten Tracks", "type": "destination"},
        ]

    drafts = [
        _coordinate_draft(
            name=item["name"],
            latitude=item["lat"],
            longitude=item["lon"],
            notes=item["notes"],
            stop_type=item["type"],
        )
        for item in points[:MAX_IMPORT_DRAFTS]
    ]
    warnings: list[str] = []
    if len(points) > MAX_IMPORT_DRAFTS:
        warnings.append(f"Nur die ersten {MAX_IMPORT_DRAFTS} GPX-Wegpunkte wurden vorgemerkt.")
    if not drafts:
        warnings.append("Die GPX-Datei enthält keine verwertbaren Weg- oder Trackpunkte.")
    return ParsedImport(
        format="gpx",
        title=Path(filename).stem or "GPX-Route",
        summary=f"{len(points)} Wegpunkte und {len(track_points)} Trackpunkte erkannt.",
        basket_delta={"add_or_update": drafts, "remove_ids": [], "note": "Aus GPX importiert"},
        preview_items=[
            {
                "kind": "stop",
                "title": item["name"],
                "subtitle": f"{item['lat']:.5f}, {item['lon']:.5f}",
            }
            for item in points[:MAX_IMPORT_PREVIEW_ITEMS]
        ],
        warnings=warnings,
        metadata={"waypoint_count": len(points), "track_point_count": len(track_points)},
    )


def _unfold_ics(text: str) -> list[str]:
    result: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and result:
            result[-1] += line[1:]
        else:
            result.append(line)
    return result


def _ics_unescape(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _ics_date(value: str) -> str:
    raw = value.strip()
    if re.fullmatch(r"\d{8}", raw):
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    match = re.match(r"(\d{4})(\d{2})(\d{2})T", raw)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


def _ics_time(value: str) -> str:
    match = re.match(r"\d{8}T(\d{2})(\d{2})", value.strip())
    return f"{match.group(1)}:{match.group(2)}" if match else ""


def parse_ics_import(data: bytes, filename: str) -> ParsedImport:
    text = _decode_text(data)
    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in _unfold_ics(text):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            current = {}
            continue
        if upper == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        raw_key, raw_value = line.split(":", 1)
        key = raw_key.split(";", 1)[0].upper()
        if key in {"SUMMARY", "DESCRIPTION", "LOCATION", "DTSTART", "DTEND", "STATUS"}:
            current[key] = _ics_unescape(raw_value)

    drafts: list[dict[str, Any]] = []
    previews: list[dict[str, Any]] = []
    for event in events[:MAX_IMPORT_DRAFTS]:
        title = _clean(event.get("SUMMARY") or "Kalendereintrag", 500)
        date_value = _ics_date(event.get("DTSTART", ""))
        start_time = _ics_time(event.get("DTSTART", ""))
        location = _clean(event.get("LOCATION"), 500)
        description = _clean(event.get("DESCRIPTION"), 5_000)
        if location:
            values: dict[str, Any] = {"name": title, "type": "activity", "notes": description}
            if start_time:
                values["arrival_time"] = start_time
            draft: dict[str, Any] = {
                "action": "add",
                "entity_type": "stop",
                "summary": f"Kalendertermin übernehmen: {title}",
                "place_query": location,
                "reason": "Aus einer vom Benutzer importierten Kalenderdatei übernommen.",
                "values": values,
            }
            if date_value:
                draft["day_date"] = date_value
        else:
            draft = {
                "action": "plan",
                "entity_type": "day",
                "summary": f"Kalendertermin berücksichtigen: {title}",
                "reason": "Aus einer vom Benutzer importierten Kalenderdatei übernommen.",
                "values": {"notes": "\n".join(part for part in (title, description) if part)},
            }
            if date_value:
                draft["day_date"] = date_value
        drafts.append(draft)
        previews.append({"kind": "event", "title": title, "subtitle": " · ".join(part for part in (date_value, start_time, location) if part)})

    warnings: list[str] = []
    if len(events) > MAX_IMPORT_DRAFTS:
        warnings.append(f"Nur die ersten {MAX_IMPORT_DRAFTS} Kalendereinträge wurden vorgemerkt.")
    if not drafts:
        warnings.append("Die Kalenderdatei enthält keine VEVENT-Einträge.")
    return ParsedImport(
        format="ics",
        title=Path(filename).stem or "Kalenderimport",
        summary=f"{len(events)} Kalendereinträge erkannt.",
        basket_delta={"add_or_update": drafts, "remove_ids": [], "note": "Aus Kalenderdatei importiert"},
        preview_items=previews,
        warnings=warnings,
        metadata={"event_count": len(events)},
        text=text,
    )


def parse_csv_import(data: bytes, filename: str) -> ParsedImport:
    text = _decode_text(data)
    try:
        dialect = csv.Sniffer().sniff(text[:10_000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows: list[list[str]] = []
    for index, row in enumerate(reader):
        if index >= 500:
            break
        rows.append([_clean(cell, 2_000) for cell in row])
    headers = rows[0] if rows else []
    return ParsedImport(
        format="csv",
        title=Path(filename).stem or "CSV-Import",
        summary=f"CSV-Datei mit {max(0, len(rows) - 1)} Datenzeilen erkannt.",
        text=text,
        preview_items=[
            {"kind": "row", "title": " · ".join(row[:4]) or f"Zeile {index + 1}", "subtitle": ""}
            for index, row in enumerate(rows[1:21])
        ],
        warnings=["Die fachliche Zuordnung der CSV-Spalten wird vor der Übernahme durch den Assistenten geprüft."],
        metadata={"row_count": max(0, len(rows) - 1), "headers": headers[:50]},
    )


def parse_zip_import(data: bytes, filename: str) -> ParsedImport:
    if len(data) > MAX_ZIP_TOTAL_BYTES:
        raise ValidationError("Das ZIP-Archiv ist zu groß")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as err:
        raise ValidationError("Das ZIP-Archiv ist beschädigt oder ungültig") from err

    chunks: list[str] = []
    previews: list[dict[str, Any]] = []
    warnings: list[str] = []
    total = 0
    direct_candidates: list[dict[str, Any]] = []
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_FILES:
        warnings.append(f"Das Archiv enthält mehr als {MAX_ZIP_FILES} Dateien; weitere Einträge wurden ignoriert.")
    for info in infos[:MAX_ZIP_FILES]:
        name = info.filename
        pure = PurePosixPath(name)
        if info.is_dir():
            continue
        if pure.is_absolute() or ".." in pure.parts or "\x00" in name:
            warnings.append(f"Unsicherer ZIP-Eintrag wurde ignoriert: {name[:120]}")
            continue
        if info.flag_bits & 0x1:
            warnings.append(f"Verschlüsselter ZIP-Eintrag wurde ignoriert: {name[:120]}")
            continue
        if info.file_size > MAX_ZIP_MEMBER_BYTES:
            warnings.append(f"Zu großer ZIP-Eintrag wurde ignoriert: {name[:120]}")
            continue
        suffix = pure.suffix.casefold()
        previews.append({"kind": "file", "title": pure.name, "subtitle": f"{info.file_size} Bytes"})
        if suffix not in _ZIP_TEXT_EXTENSIONS:
            warnings.append(f"Binärer ZIP-Eintrag wird nur aufgelistet, nicht inline analysiert: {pure.name}")
            continue
        try:
            with archive.open(info, "r") as member_handle:
                member = member_handle.read(MAX_ZIP_MEMBER_BYTES + 1)
        except (OSError, RuntimeError, zipfile.BadZipFile):
            warnings.append(f"ZIP-Eintrag konnte nicht sicher gelesen werden: {pure.name}")
            continue
        if len(member) > MAX_ZIP_MEMBER_BYTES:
            warnings.append(f"Zu großer ZIP-Eintrag wurde ignoriert: {name[:120]}")
            continue
        total += len(member)
        if total > MAX_ZIP_TOTAL_BYTES:
            warnings.append("Das Entpacklimit wurde erreicht; weitere Dateien wurden ignoriert.")
            break
        if suffix == ".gpx":
            parsed = parse_gpx_import(member, pure.name)
            if parsed.basket_delta:
                chunks.append("GPX_IMPORT_RESULT:\n" + json.dumps(parsed.as_dict(), ensure_ascii=False))
            continue
        if suffix in {".ics", ".ical"}:
            parsed = parse_ics_import(member, pure.name)
            if parsed.basket_delta:
                chunks.append("ICS_IMPORT_RESULT:\n" + json.dumps(parsed.as_dict(), ensure_ascii=False))
            continue
        text = _decode_text(member)
        direct = extract_marked_changeset(text) or _direct_changeset(_json_object(text.strip()))
        if direct is not None:
            direct_candidates.append(direct)
        chunks.append(f"\n===== DATEI: {pure.as_posix()} =====\n{text}")

    direct = direct_candidates[0] if len(direct_candidates) == 1 else None
    if len(direct_candidates) > 1:
        warnings.append("Mehrere ChangeSets im ZIP wurden erkannt; sie werden als allgemeine Übergabe analysiert.")
    text = "\n".join(chunks)[:MAX_IMPORT_TEXT_CHARACTERS]
    return ParsedImport(
        format="zip",
        title=Path(filename).stem or "ZIP-Übergabe",
        summary=f"ZIP-Archiv mit {len(previews)} unterstützten oder sichtbaren Einträgen erkannt.",
        text=text,
        direct_changeset=direct,
        preview_items=previews[:MAX_IMPORT_PREVIEW_ITEMS],
        warnings=warnings,
        metadata={"file_count": len(infos), "processed_bytes": total},
    )


def parse_import_file(path: Path, document: dict[str, Any]) -> ParsedImport:
    """Parse one private archive document without semantic mutation."""
    filename = str(document.get("original_filename") or path.name)
    suffix = Path(filename).suffix.casefold()
    mime = str(document.get("mime_type") or "").casefold()
    data = path.read_bytes()

    if suffix in {".gpx"} or mime in {"application/gpx+xml", "application/xml", "text/xml"} and suffix == ".gpx":
        return parse_gpx_import(data, filename)
    if suffix in {".ics", ".ical"} or mime == "text/calendar":
        return parse_ics_import(data, filename)
    if suffix == ".csv" or mime == "text/csv":
        return parse_csv_import(data, filename)
    if suffix == ".zip" or mime in {"application/zip", "application/x-zip-compressed"}:
        return parse_zip_import(data, filename)
    if suffix in _TEXT_EXTENSIONS or mime.startswith("text/") or mime == "application/json":
        return parse_text_import(data, filename)
    if mime == "application/pdf" or mime.startswith("image/"):
        return ParsedImport(
            format="binary_document",
            title=Path(filename).stem or "Dokumentübergabe",
            summary="PDF oder Bild zur semantischen Reiseimport-Analyse.",
            metadata={"size_bytes": len(data), "mime_type": mime},
        )
    raise ValidationError("Dieses Dateiformat wird vom Universal Importer nicht unterstützt")
