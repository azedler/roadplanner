"""Private travel documents, expenses, and todos for Roadplanner.

The archive is intentionally stored outside ``/config/www``. Original files are
never published through a static path; they can only be retrieved through a
short-lived ticket issued to an authenticated Roadplanner panel user.

The canonical trip/day/stop JSON files remain the route source of truth. This
module stores document metadata, extracted booking facts, expenses, and todos in
per-trip sidecar indexes so they can evolve independently without weakening the
existing ChangeSet contract.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
from tempfile import NamedTemporaryFile
from typing import Any
import uuid

from .roadplanner import StorageError, ValidationError, validate_identifier

ARCHIVE_SCHEMA_VERSION = 2
MAX_DOCUMENTS_PER_TRIP = 1000
MAX_EXPENSES_PER_TRIP = 10_000
MAX_TODOS_PER_TRIP = 10_000
MAX_TEXT = 100_000
MAX_ANALYSIS_ITEMS = 200

ALLOWED_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
        "text/plain",
        "text/markdown",
        "text/csv",
        "text/calendar",
        "application/gpx+xml",
        "application/xml",
        "text/xml",
        "application/zip",
        "application/x-zip-compressed",
        "application/json",
    }
)

DOCUMENT_TYPES = frozenset(
    {
        "ferry_booking",
        "camping_booking",
        "accommodation_booking",
        "restaurant_reservation",
        "event_ticket",
        "admission_ticket",
        "transport_ticket",
        "invoice",
        "receipt",
        "insurance",
        "vehicle_document",
        "fishing_license",
        "travel_document",
        "other",
    }
)

EXPENSE_CATEGORIES = frozenset(
    {
        "fuel",
        "charging",
        "campsite",
        "motorhome_site",
        "parking",
        "restaurant",
        "snack",
        "groceries",
        "ferry",
        "transport",
        "other",
    }
)

# Existing 2.4/2.6 sidecars are migrated lazily while loading.  The next write
# persists the canonical 2.6.5 category without deleting any expense.
_LEGACY_EXPENSE_CATEGORY_MAP = {
    "fuel": "fuel",
    "charging": "charging",
    "camping": "campsite",
    "campsite": "campsite",
    "stellplatz": "motorhome_site",
    "motorhome_site": "motorhome_site",
    "parking": "parking",
    "restaurant": "restaurant",
    "snack": "snack",
    "imbiss": "snack",
    "groceries": "groceries",
    "shopping": "groceries",
    "ferry": "ferry",
    "transport": "transport",
    "admission": "other",
    "fishing": "other",
    "laundry": "other",
    "water": "other",
    "service": "other",
    "repair": "other",
    "accommodation": "other",
    "other": "other",
}


def normalize_expense_category(value: Any) -> str:
    """Return one canonical category while preserving legacy expenses."""
    source = str(value or "other").strip().casefold().replace(" ", "_")
    source = {
        "campground": "camping",
        "campingplatz": "camping",
        "motorhome": "motorhome_site",
        "motorhome-site": "motorhome_site",
        "grocery": "groceries",
        "lebensmittel": "groceries",
        "fast_food": "snack",
        "food_stand": "snack",
        "transportmittel": "transport",
    }.get(source, source)
    category = _LEGACY_EXPENSE_CATEGORY_MAP.get(source, source)
    return category if category in EXPENSE_CATEGORIES else "other"

_DOCUMENT_STATUS = frozenset(
    {
        "draft",
        "analysis_pending",
        "analyzed",
        "confirmed",
        "cancelled",
        "expired",
        "file_removed",
    }
)
_EXPENSE_STATUS = frozenset({"planned", "paid", "refundable", "refunded", "cancelled", "unknown"})
_TODO_STATUS = frozenset({"open", "done", "dismissed"})
_TODO_PRIORITY = frozenset({"low", "normal", "high"})
_CLASSIFICATIONS = frozenset({"document", "expense", "document_expense"})
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")


def utc_now_iso() -> str:
    """Return an ISO UTC timestamp without microseconds."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _clean_text(value: Any, *, maximum: int = 4_000) -> str:
    text = str(value or "").strip()
    if len(text) > maximum:
        text = text[:maximum]
    return text


def _safe_filename(filename: str) -> str:
    name = Path(str(filename or "document")).name.strip().replace("\x00", "")
    name = _SAFE_FILENAME_RE.sub("_", name)
    name = " ".join(name.split())
    if not name or name in {".", ".."}:
        name = "document"
    return name[:180]


def _json_safe(value: Any, *, maximum_depth: int = 10, maximum_items: int = 10_000) -> Any:
    """Return a deep-copied JSON-compatible value with bounded complexity."""
    copied = deepcopy(value)
    count = 0

    def walk(node: Any, depth: int) -> None:
        nonlocal count
        count += 1
        if count > maximum_items:
            raise ValidationError("Archivdaten enthalten zu viele Werte")
        if depth > maximum_depth:
            raise ValidationError("Archivdaten sind zu tief verschachtelt")
        if node is None or isinstance(node, (bool, int)):
            return
        if isinstance(node, float):
            if node != node or node in (float("inf"), float("-inf")):
                raise ValidationError("Archivdaten enthalten ungültige Zahlen")
            return
        if isinstance(node, str):
            if len(node) > MAX_TEXT:
                raise ValidationError("Archivdaten enthalten zu langen Text")
            return
        if isinstance(node, list):
            for child in node:
                walk(child, depth + 1)
            return
        if isinstance(node, dict):
            for key, child in node.items():
                if not isinstance(key, str) or len(key) > 500:
                    raise ValidationError("Archivdaten enthalten ungültige Schlüssel")
                walk(child, depth + 1)
            return
        raise ValidationError("Archivdaten enthalten nicht JSON-kompatible Werte")

    walk(copied, 0)
    return copied


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw)
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as err:
        raise StorageError(f"Archivdatei konnte nicht gelesen werden: {path}") from err
    if not isinstance(value, dict):
        raise StorageError(f"Archivdatei enthält kein JSON-Objekt: {path}")
    return value


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except OSError as err:
        try:
            temp_path.unlink(missing_ok=True)  # type: ignore[possibly-undefined]
        except OSError:
            pass
        raise StorageError(f"Archivdatei konnte nicht atomar geschrieben werden: {path}") from err


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_mime_type(path: Path, declared: str, filename: str) -> str:
    """Detect an allow-listed MIME type without trusting the browser alone."""
    try:
        prefix = path.read_bytes()[:32]
    except OSError as err:
        raise StorageError("Die hochgeladene Datei konnte nicht geprüft werden") from err

    detected = ""
    if prefix.startswith(b"%PDF-"):
        detected = "application/pdf"
    elif prefix.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
    elif prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    elif prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        detected = "image/webp"
    elif len(prefix) >= 12 and prefix[4:8] == b"ftyp" and prefix[8:12] in {
        b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1",
    }:
        detected = "image/heic"
    elif prefix.startswith(b"PK\x03\x04") or prefix.startswith(b"PK\x05\x06"):
        detected = "application/zip"
    elif prefix.lstrip().startswith(b"<?xml") or prefix.lstrip().startswith(b"<gpx"):
        suffix = Path(filename).suffix.casefold()
        detected = "application/gpx+xml" if suffix == ".gpx" else "application/xml"
    else:
        suffix_guess = mimetypes.guess_type(filename)[0] or ""
        candidate = str(declared or "").split(";", 1)[0].strip().casefold()
        if candidate in ALLOWED_MIME_TYPES:
            detected = candidate
        elif suffix_guess in ALLOWED_MIME_TYPES:
            detected = suffix_guess

    if detected not in ALLOWED_MIME_TYPES:
        raise ValidationError(
            "Nicht unterstütztes Dateiformat. Erlaubt sind PDF, JPEG, PNG, "
            "WebP, HEIC, Markdown, Text, CSV, JSON, GPX, ICS und ZIP."
        )
    return detected


def _normalize_links(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    day_ids: list[str] = []
    for item in source.get("day_ids", []):
        try:
            normalized = validate_identifier(item, "links.day_ids")
        except ValidationError:
            continue
        if normalized not in day_ids:
            day_ids.append(normalized)
    stop_links: list[dict[str, str]] = []
    for raw in source.get("stop_links", []):
        if not isinstance(raw, dict):
            continue
        try:
            day_id = validate_identifier(raw.get("day_id"), "links.stop_links.day_id")
            stop_id = validate_identifier(raw.get("stop_id"), "links.stop_links.stop_id")
        except ValidationError:
            continue
        item = {"day_id": day_id, "stop_id": stop_id}
        if item not in stop_links:
            stop_links.append(item)
    people = []
    for raw in source.get("people", []):
        name = _clean_text(raw, maximum=200)
        if name and name not in people:
            people.append(name)
    return {
        "day_ids": day_ids[:100],
        "stop_links": stop_links[:200],
        "people": people[:100],
    }


def _normalize_document(raw: dict[str, Any]) -> dict[str, Any]:
    doc_id = validate_identifier(raw.get("id"), "document.id")
    trip_id = validate_identifier(raw.get("trip_id"), "document.trip_id")
    created_at = _clean_text(raw.get("created_at"), maximum=100) or utc_now_iso()
    status = str(raw.get("status") or "draft")
    if status not in _DOCUMENT_STATUS:
        status = "draft"
    classification = str(raw.get("classification") or "document")
    if classification not in _CLASSIFICATIONS:
        classification = "document"
    document_type = str(raw.get("document_type") or "other")
    if document_type not in DOCUMENT_TYPES:
        document_type = "other"
    return {
        "id": doc_id,
        "trip_id": trip_id,
        "title": _clean_text(raw.get("title"), maximum=500)
        or _clean_text(raw.get("original_filename"), maximum=500)
        or "Reisedokument",
        "classification": classification,
        "document_type": document_type,
        "provider": _clean_text(raw.get("provider"), maximum=500),
        "status": status,
        "source": _clean_text(raw.get("source"), maximum=100) or "upload",
        "original_filename": _safe_filename(str(raw.get("original_filename") or "document")),
        "mime_type": _clean_text(raw.get("mime_type"), maximum=200),
        "size_bytes": max(0, int(raw.get("size_bytes") or 0)),
        "sha256": _clean_text(raw.get("sha256"), maximum=128),
        "stored_relpath": _clean_text(raw.get("stored_relpath"), maximum=500),
        "file_retained": bool(raw.get("file_retained", bool(raw.get("stored_relpath")))),
        "keep_original": bool(raw.get("keep_original", True)),
        "offline_priority": bool(raw.get("offline_priority", False)),
        "sensitive": bool(raw.get("sensitive", False)),
        "summary": _clean_text(raw.get("summary"), maximum=8_000),
        "links": _normalize_links(raw.get("links")),
        "extracted": _json_safe(raw.get("extracted") if isinstance(raw.get("extracted"), dict) else {}),
        "analysis": _json_safe(raw.get("analysis") if isinstance(raw.get("analysis"), dict) else {}),
        "warnings": [
            _clean_text(item, maximum=2_000)
            for item in list(raw.get("warnings") or [])[:MAX_ANALYSIS_ITEMS]
            if _clean_text(item, maximum=2_000)
        ],
        "created_at": created_at,
        "updated_at": _clean_text(raw.get("updated_at"), maximum=100) or created_at,
        "confirmed_at": _clean_text(raw.get("confirmed_at"), maximum=100) or None,
        "created_by": _clean_text(raw.get("created_by"), maximum=300),
        "updated_by": _clean_text(raw.get("updated_by"), maximum=300),
    }


def _normalize_expense(raw: dict[str, Any]) -> dict[str, Any]:
    expense_id = validate_identifier(raw.get("id"), "expense.id")
    trip_id = validate_identifier(raw.get("trip_id"), "expense.trip_id")
    amount = raw.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
        raise ValidationError("Ausgabenbetrag muss eine nicht-negative Zahl sein")
    currency = _clean_text(raw.get("currency"), maximum=3).upper() or "EUR"
    if len(currency) != 3 or not currency.isalpha():
        raise ValidationError("Währung muss ein dreistelliger ISO-Code sein")
    category = normalize_expense_category(raw.get("category"))
    status = str(raw.get("status") or "paid")
    if status not in _EXPENSE_STATUS:
        status = "unknown"
    expense_date = _clean_text(raw.get("date"), maximum=10)
    if expense_date:
        try:
            expense_date = date.fromisoformat(expense_date).isoformat()
        except ValueError as err:
            raise ValidationError("Ausgabedatum muss YYYY-MM-DD sein") from err
    day_id = raw.get("day_id")
    stop_id = raw.get("stop_id")
    if day_id not in (None, ""):
        day_id = validate_identifier(day_id, "expense.day_id")
    else:
        day_id = None
    if stop_id not in (None, ""):
        stop_id = validate_identifier(stop_id, "expense.stop_id")
    else:
        stop_id = None
    created_at = _clean_text(raw.get("created_at"), maximum=100) or utc_now_iso()
    return {
        "id": expense_id,
        "trip_id": trip_id,
        "document_id": (
            validate_identifier(raw.get("document_id"), "expense.document_id")
            if raw.get("document_id")
            else None
        ),
        "day_id": day_id,
        "stop_id": stop_id,
        "date": expense_date or None,
        "merchant": _clean_text(raw.get("merchant"), maximum=500),
        "category": category,
        "amount": round(float(amount), 2),
        "currency": currency,
        "status": status,
        "payment_method": _clean_text(raw.get("payment_method"), maximum=100),
        "notes": _clean_text(raw.get("notes"), maximum=8_000),
        "source": _clean_text(raw.get("source"), maximum=100) or "manual",
        "created_at": created_at,
        "updated_at": _clean_text(raw.get("updated_at"), maximum=100) or created_at,
        "created_by": _clean_text(raw.get("created_by"), maximum=300),
    }


def _normalize_todo(raw: dict[str, Any]) -> dict[str, Any]:
    todo_id = validate_identifier(raw.get("id"), "todo.id")
    trip_id = validate_identifier(raw.get("trip_id"), "todo.trip_id")
    title = _clean_text(raw.get("title"), maximum=1_000)
    if not title:
        raise ValidationError("Aufgabe benötigt einen Titel")
    status = str(raw.get("status") or "open")
    if status not in _TODO_STATUS:
        status = "open"
    priority = str(raw.get("priority") or "normal")
    if priority not in _TODO_PRIORITY:
        priority = "normal"
    day_id = raw.get("day_id")
    stop_id = raw.get("stop_id")
    if day_id not in (None, ""):
        day_id = validate_identifier(day_id, "todo.day_id")
    else:
        day_id = None
    if stop_id not in (None, ""):
        stop_id = validate_identifier(stop_id, "todo.stop_id")
    else:
        stop_id = None
    due_at = _clean_text(raw.get("due_at"), maximum=100) or None
    created_at = _clean_text(raw.get("created_at"), maximum=100) or utc_now_iso()
    return {
        "id": todo_id,
        "trip_id": trip_id,
        "document_id": (
            validate_identifier(raw.get("document_id"), "todo.document_id")
            if raw.get("document_id")
            else None
        ),
        "day_id": day_id,
        "stop_id": stop_id,
        "title": title,
        "due_at": due_at,
        "status": status,
        "priority": priority,
        "notes": _clean_text(raw.get("notes"), maximum=8_000),
        "source": _clean_text(raw.get("source"), maximum=100) or "manual",
        "created_at": created_at,
        "updated_at": _clean_text(raw.get("updated_at"), maximum=100) or created_at,
        "created_by": _clean_text(raw.get("created_by"), maximum=300),
    }


class TravelArchiveStore:
    """Synchronous, per-trip private sidecar store."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.trips_dir = root_dir / "trips"
        self.files_dir = root_dir / "files"
        self.temp_dir = root_dir / "tmp"

    def initialize(self) -> None:
        for path in (self.root_dir, self.trips_dir, self.files_dir, self.temp_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.cleanup_orphan_temp_files()

    def cleanup_orphan_temp_files(self) -> int:
        removed = 0
        if not self.temp_dir.exists():
            return 0
        cutoff = datetime.now(timezone.utc).timestamp() - 24 * 60 * 60
        for path in self.temp_dir.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    def new_temp_path(self) -> Path:
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        return self.temp_dir / f"upload-{uuid.uuid4().hex}.tmp"

    def _trip_index_path(self, trip_id: str) -> Path:
        trip_id = validate_identifier(trip_id, "trip_id")
        return self.trips_dir / trip_id / "archive.json"

    def _resolve_stored_path(self, stored_relpath: Any) -> Path:
        """Resolve one archive blob without allowing path traversal."""
        relative = Path(str(stored_relpath or ""))
        if not str(relative) or relative.is_absolute() or ".." in relative.parts:
            raise StorageError("Ungültiger Dokumentpfad im Archiv")
        path = (self.root_dir / relative).resolve(strict=False)
        root = self.root_dir.resolve()
        try:
            path.relative_to(root)
        except ValueError as err:
            raise StorageError("Dokumentpfad verlässt das private Archiv") from err
        return path

    def _default_trip(self, trip_id: str) -> dict[str, Any]:
        now = utc_now_iso()
        return {
            "schema_version": ARCHIVE_SCHEMA_VERSION,
            "trip_id": trip_id,
            "updated_at": now,
            "documents": [],
            "expenses": [],
            "todos": [],
        }

    def load_trip(self, trip_id: str) -> dict[str, Any]:
        trip_id = validate_identifier(trip_id, "trip_id")
        path = self._trip_index_path(trip_id)
        if not path.exists():
            return self._default_trip(trip_id)
        raw = _read_json(path)
        if int(raw.get("schema_version") or 0) > ARCHIVE_SCHEMA_VERSION:
            raise StorageError("Das Archivschema ist neuer als diese Roadplanner-Version")
        if raw.get("trip_id") != trip_id:
            raise StorageError("Archivdatei gehört zu einer anderen Reise")
        documents = [_normalize_document(item) for item in raw.get("documents", []) if isinstance(item, dict)]
        expenses = [_normalize_expense(item) for item in raw.get("expenses", []) if isinstance(item, dict)]
        todos = [_normalize_todo(item) for item in raw.get("todos", []) if isinstance(item, dict)]
        return {
            "schema_version": ARCHIVE_SCHEMA_VERSION,
            "trip_id": trip_id,
            "updated_at": _clean_text(raw.get("updated_at"), maximum=100) or utc_now_iso(),
            "documents": documents[:MAX_DOCUMENTS_PER_TRIP],
            "expenses": expenses[:MAX_EXPENSES_PER_TRIP],
            "todos": todos[:MAX_TODOS_PER_TRIP],
        }

    def _write_trip(self, value: dict[str, Any]) -> None:
        value = deepcopy(value)
        value["schema_version"] = ARCHIVE_SCHEMA_VERSION
        value["updated_at"] = utc_now_iso()
        _atomic_write_json(self._trip_index_path(value["trip_id"]), value)

    @staticmethod
    def _find(items: list[dict[str, Any]], item_id: str, label: str) -> dict[str, Any]:
        item_id = validate_identifier(item_id, f"{label}_id")
        for item in items:
            if item.get("id") == item_id:
                return item
        raise ValidationError(f"{label.capitalize()} nicht gefunden: {item_id}")

    def create_uploaded_document(
        self,
        *,
        trip_id: str,
        temp_path: Path,
        original_filename: str,
        declared_mime: str,
        source: str,
        created_by: str,
        keep_original: bool,
        links: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trip_id = validate_identifier(trip_id, "trip_id")
        state = self.load_trip(trip_id)
        if len(state["documents"]) >= MAX_DOCUMENTS_PER_TRIP:
            raise ValidationError("Diese Reise enthält bereits zu viele Dokumente")
        filename = _safe_filename(original_filename)
        mime_type = detect_mime_type(temp_path, declared_mime, filename)
        size_bytes = temp_path.stat().st_size
        document_id = _new_id("doc")
        suffix = {
            "application/pdf": ".pdf",
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/heic": ".heic",
            "image/heif": ".heif",
            "text/plain": ".txt",
            "text/markdown": ".md",
            "text/csv": ".csv",
            "text/calendar": ".ics",
            "application/gpx+xml": ".gpx",
            "application/xml": Path(filename).suffix[:12] or ".xml",
            "text/xml": Path(filename).suffix[:12] or ".xml",
            "application/zip": ".zip",
            "application/x-zip-compressed": ".zip",
            "application/json": ".json",
        }.get(mime_type, Path(filename).suffix[:12] or ".bin")
        destination_dir = self.files_dir / trip_id
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{document_id}{suffix}"
        try:
            os.replace(temp_path, destination)
        except OSError:
            try:
                shutil.copyfile(temp_path, destination)
                temp_path.unlink(missing_ok=True)
            except OSError as err:
                raise StorageError("Dokument konnte nicht in das private Archiv verschoben werden") from err
        now = utc_now_iso()
        document = _normalize_document(
            {
                "id": document_id,
                "trip_id": trip_id,
                "title": Path(filename).stem or filename,
                "classification": "document",
                "document_type": "other",
                "status": "draft",
                "source": source,
                "original_filename": filename,
                "mime_type": mime_type,
                "size_bytes": size_bytes,
                "sha256": sha256_file(destination),
                "stored_relpath": destination.relative_to(self.root_dir).as_posix(),
                "file_retained": True,
                "keep_original": keep_original,
                "links": links or {},
                "created_at": now,
                "updated_at": now,
                "created_by": created_by,
                "updated_by": created_by,
            }
        )
        state["documents"].insert(0, document)
        try:
            self._write_trip(state)
        except Exception:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return deepcopy(document)

    def get_document(self, trip_id: str, document_id: str) -> dict[str, Any]:
        state = self.load_trip(trip_id)
        return deepcopy(self._find(state["documents"], document_id, "document"))

    def get_document_file(self, trip_id: str, document_id: str) -> tuple[Path, dict[str, Any]]:
        document = self.get_document(trip_id, document_id)
        if not document.get("file_retained") or not document.get("stored_relpath"):
            raise ValidationError("Für diesen Eintrag ist keine Originaldatei gespeichert")
        path = self._resolve_stored_path(document["stored_relpath"])
        if not path.is_file():
            raise StorageError("Die Originaldatei fehlt im privaten Archiv")
        return path, document

    def set_document_analysis(
        self,
        *,
        trip_id: str,
        document_id: str,
        analysis: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        state = self.load_trip(trip_id)
        document = self._find(state["documents"], document_id, "document")
        normalized = _json_safe(analysis)
        document["analysis"] = normalized
        document["classification"] = (
            str(normalized.get("classification"))
            if str(normalized.get("classification")) in _CLASSIFICATIONS
            else document.get("classification", "document")
        )
        candidate_type = str(normalized.get("document_type") or "other")
        document["document_type"] = candidate_type if candidate_type in DOCUMENT_TYPES else "other"
        document["title"] = _clean_text(normalized.get("title"), maximum=500) or document["title"]
        document["provider"] = _clean_text(normalized.get("provider"), maximum=500)
        document["summary"] = _clean_text(normalized.get("summary"), maximum=8_000)
        document["warnings"] = [
            _clean_text(item, maximum=2_000)
            for item in list(normalized.get("warnings") or [])[:MAX_ANALYSIS_ITEMS]
            if _clean_text(item, maximum=2_000)
        ]
        document["status"] = "analyzed"
        document["updated_at"] = utc_now_iso()
        document["updated_by"] = actor
        self._write_trip(state)
        return deepcopy(document)

    def confirm_document(
        self,
        *,
        trip_id: str,
        document_id: str,
        patch: dict[str, Any],
        actor: str,
        default_currency: str,
    ) -> dict[str, Any]:
        state = self.load_trip(trip_id)
        document = self._find(state["documents"], document_id, "document")
        now = utc_now_iso()
        classification = str(patch.get("classification") or document.get("classification") or "document")
        if classification not in _CLASSIFICATIONS:
            raise ValidationError("Ungültige Dokumentklassifikation")
        document_type = str(patch.get("document_type") or document.get("document_type") or "other")
        if document_type not in DOCUMENT_TYPES:
            document_type = "other"
        document.update(
            {
                "title": _clean_text(patch.get("title"), maximum=500) or document["title"],
                "classification": classification,
                "document_type": document_type,
                "provider": _clean_text(patch.get("provider"), maximum=500),
                "summary": _clean_text(patch.get("summary"), maximum=8_000),
                "status": "confirmed",
                "links": _normalize_links(patch.get("links") or document.get("links")),
                "keep_original": bool(patch.get("keep_original", document.get("keep_original", True))),
                "offline_priority": bool(patch.get("offline_priority", document.get("offline_priority", False))),
                "sensitive": bool(patch.get("sensitive", document.get("sensitive", False))),
                "extracted": _json_safe(patch.get("extracted") if isinstance(patch.get("extracted"), dict) else document.get("extracted", {})),
                "updated_at": now,
                "confirmed_at": now,
                "updated_by": actor,
            }
        )

        existing_expenses = [
            item for item in state["expenses"] if item.get("document_id") == document_id
        ]
        existing_todos = [
            item for item in state["todos"] if item.get("document_id") == document_id
        ]

        created_expenses: list[dict[str, Any]] = []
        expense_raw = patch.get("expense")
        if isinstance(expense_raw, dict) and expense_raw.get("enabled") and not existing_expenses:
            if len(state["expenses"]) >= MAX_EXPENSES_PER_TRIP:
                raise ValidationError("Diese Reise enthält bereits zu viele Ausgaben")
            stop_links = document["links"].get("stop_links", [])
            day_ids = document["links"].get("day_ids", [])
            raw = {
                **expense_raw,
                "id": _new_id("expense"),
                "trip_id": trip_id,
                "document_id": document_id,
                "day_id": expense_raw.get("day_id") or (day_ids[0] if day_ids else None),
                "stop_id": expense_raw.get("stop_id") or (stop_links[0].get("stop_id") if stop_links else None),
                "currency": expense_raw.get("currency") or default_currency,
                "source": "document_analysis",
                "created_at": now,
                "updated_at": now,
                "created_by": actor,
            }
            expense = _normalize_expense(raw)
            state["expenses"].insert(0, expense)
            created_expenses.append(expense)
        elif isinstance(expense_raw, dict) and expense_raw.get("enabled"):
            created_expenses.extend(existing_expenses)

        created_todos: list[dict[str, Any]] = []
        existing_todo_keys = {
            (str(item.get("title") or "").casefold(), str(item.get("due_at") or ""))
            for item in existing_todos
        }
        for todo_raw in list(patch.get("todos") or [])[:MAX_ANALYSIS_ITEMS]:
            if not isinstance(todo_raw, dict) or todo_raw.get("enabled") is False:
                continue
            todo_key = (
                _clean_text(todo_raw.get("title"), maximum=1_000).casefold(),
                _clean_text(todo_raw.get("due_at"), maximum=100),
            )
            if todo_key in existing_todo_keys:
                created_todos.extend(
                    item
                    for item in existing_todos
                    if (
                        str(item.get("title") or "").casefold(),
                        str(item.get("due_at") or ""),
                    ) == todo_key
                )
                continue
            if len(state["todos"]) >= MAX_TODOS_PER_TRIP:
                raise ValidationError("Diese Reise enthält bereits zu viele Aufgaben")
            stop_links = document["links"].get("stop_links", [])
            day_ids = document["links"].get("day_ids", [])
            todo = _normalize_todo(
                {
                    **todo_raw,
                    "id": _new_id("todo"),
                    "trip_id": trip_id,
                    "document_id": document_id,
                    "day_id": todo_raw.get("day_id") or (day_ids[0] if day_ids else None),
                    "stop_id": todo_raw.get("stop_id") or (stop_links[0].get("stop_id") if stop_links else None),
                    "status": "open",
                    "source": "document_analysis",
                    "created_at": now,
                    "updated_at": now,
                    "created_by": actor,
                }
            )
            state["todos"].insert(0, todo)
            created_todos.append(todo)
            existing_todo_keys.add(todo_key)

        file_to_delete: Path | None = None
        if not document["keep_original"] and document.get("stored_relpath"):
            file_to_delete = self._resolve_stored_path(document["stored_relpath"])
            document["stored_relpath"] = ""
            document["file_retained"] = False
            document["status"] = "file_removed"

        self._write_trip(state)
        if file_to_delete is not None:
            try:
                file_to_delete.unlink(missing_ok=True)
            except OSError:
                # The metadata already protects the file from being served. A
                # later maintenance pass can remove an orphaned blob.
                pass
        return {
            "document": deepcopy(document),
            "expenses": deepcopy(created_expenses),
            "todos": deepcopy(created_todos),
        }

    def update_document(
        self,
        *,
        trip_id: str,
        document_id: str,
        patch: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        state = self.load_trip(trip_id)
        document = self._find(state["documents"], document_id, "document")
        if "title" in patch:
            title = _clean_text(patch.get("title"), maximum=500)
            if not title:
                raise ValidationError("Dokumenttitel darf nicht leer sein")
            document["title"] = title
        if "document_type" in patch:
            value = str(patch.get("document_type") or "other")
            document["document_type"] = value if value in DOCUMENT_TYPES else "other"
        if "provider" in patch:
            document["provider"] = _clean_text(patch.get("provider"), maximum=500)
        if "summary" in patch:
            document["summary"] = _clean_text(patch.get("summary"), maximum=8_000)
        if "links" in patch:
            document["links"] = _normalize_links(patch.get("links"))
        if "offline_priority" in patch:
            document["offline_priority"] = bool(patch.get("offline_priority"))
        if "sensitive" in patch:
            document["sensitive"] = bool(patch.get("sensitive"))
        document["updated_at"] = utc_now_iso()
        document["updated_by"] = actor
        self._write_trip(state)
        return deepcopy(document)

    def discard_document(self, *, trip_id: str, document_id: str) -> dict[str, Any]:
        state = self.load_trip(trip_id)
        document = self._find(state["documents"], document_id, "document")
        state["documents"] = [item for item in state["documents"] if item["id"] != document_id]
        state["expenses"] = [item for item in state["expenses"] if item.get("document_id") != document_id]
        state["todos"] = [item for item in state["todos"] if item.get("document_id") != document_id]
        self._write_trip(state)
        if document.get("stored_relpath"):
            try:
                self._resolve_stored_path(document["stored_relpath"]).unlink(missing_ok=True)
            except (OSError, StorageError):
                pass
        return {"deleted": document_id}

    def delete_document(
        self,
        *,
        trip_id: str,
        document_id: str,
        delete_linked_records: bool,
    ) -> dict[str, Any]:
        state = self.load_trip(trip_id)
        document = self._find(state["documents"], document_id, "document")
        state["documents"] = [item for item in state["documents"] if item["id"] != document_id]
        if delete_linked_records:
            state["expenses"] = [item for item in state["expenses"] if item.get("document_id") != document_id]
            state["todos"] = [item for item in state["todos"] if item.get("document_id") != document_id]
        else:
            for item in state["expenses"]:
                if item.get("document_id") == document_id:
                    item["document_id"] = None
            for item in state["todos"]:
                if item.get("document_id") == document_id:
                    item["document_id"] = None
        self._write_trip(state)
        if document.get("stored_relpath"):
            try:
                self._resolve_stored_path(document["stored_relpath"]).unlink(missing_ok=True)
            except (OSError, StorageError):
                pass
        return {"deleted": document_id, "linked_records_deleted": bool(delete_linked_records)}

    def create_expense(self, *, trip_id: str, value: dict[str, Any], actor: str, default_currency: str) -> dict[str, Any]:
        state = self.load_trip(trip_id)
        if len(state["expenses"]) >= MAX_EXPENSES_PER_TRIP:
            raise ValidationError("Diese Reise enthält bereits zu viele Ausgaben")
        now = utc_now_iso()
        expense = _normalize_expense(
            {
                **value,
                "id": _new_id("expense"),
                "trip_id": trip_id,
                "currency": value.get("currency") or default_currency,
                "source": value.get("source") or "manual",
                "created_at": now,
                "updated_at": now,
                "created_by": actor,
            }
        )
        state["expenses"].insert(0, expense)
        self._write_trip(state)
        return deepcopy(expense)

    def update_expense(self, *, trip_id: str, expense_id: str, patch: dict[str, Any], actor: str, default_currency: str) -> dict[str, Any]:
        state = self.load_trip(trip_id)
        current = self._find(state["expenses"], expense_id, "expense")
        normalized = _normalize_expense(
            {
                **current,
                **patch,
                "id": current["id"],
                "trip_id": trip_id,
                "currency": patch.get("currency") or current.get("currency") or default_currency,
                "updated_at": utc_now_iso(),
                "created_by": current.get("created_by") or actor,
            }
        )
        current.clear()
        current.update(normalized)
        self._write_trip(state)
        return deepcopy(current)

    def delete_expense(self, *, trip_id: str, expense_id: str) -> dict[str, Any]:
        state = self.load_trip(trip_id)
        self._find(state["expenses"], expense_id, "expense")
        state["expenses"] = [item for item in state["expenses"] if item["id"] != expense_id]
        self._write_trip(state)
        return {"deleted": expense_id}

    def create_todo(self, *, trip_id: str, value: dict[str, Any], actor: str) -> dict[str, Any]:
        state = self.load_trip(trip_id)
        if len(state["todos"]) >= MAX_TODOS_PER_TRIP:
            raise ValidationError("Diese Reise enthält bereits zu viele Aufgaben")
        now = utc_now_iso()
        todo = _normalize_todo(
            {
                **value,
                "id": _new_id("todo"),
                "trip_id": trip_id,
                "source": value.get("source") or "manual",
                "created_at": now,
                "updated_at": now,
                "created_by": actor,
            }
        )
        state["todos"].insert(0, todo)
        self._write_trip(state)
        return deepcopy(todo)

    def update_todo(self, *, trip_id: str, todo_id: str, patch: dict[str, Any], actor: str) -> dict[str, Any]:
        state = self.load_trip(trip_id)
        current = self._find(state["todos"], todo_id, "todo")
        normalized = _normalize_todo(
            {
                **current,
                **patch,
                "id": current["id"],
                "trip_id": trip_id,
                "updated_at": utc_now_iso(),
                "created_by": current.get("created_by") or actor,
            }
        )
        current.clear()
        current.update(normalized)
        self._write_trip(state)
        return deepcopy(current)

    def delete_todo(self, *, trip_id: str, todo_id: str) -> dict[str, Any]:
        state = self.load_trip(trip_id)
        self._find(state["todos"], todo_id, "todo")
        state["todos"] = [item for item in state["todos"] if item["id"] != todo_id]
        self._write_trip(state)
        return {"deleted": todo_id}

    def panel_payload(self, trip_id: str) -> dict[str, Any]:
        state = self.load_trip(trip_id)
        documents = sorted(state["documents"], key=lambda item: item.get("updated_at") or "", reverse=True)
        expenses = sorted(state["expenses"], key=lambda item: (item.get("date") or "", item.get("created_at") or ""), reverse=True)
        todos = sorted(
            state["todos"],
            key=lambda item: (
                0 if item.get("status") == "open" else 1,
                item.get("due_at") or "9999",
                item.get("created_at") or "",
            ),
        )
        totals: dict[str, float] = {}
        category_totals: dict[str, dict[str, float]] = {}
        for expense in expenses:
            if expense.get("status") == "cancelled":
                continue
            currency = str(expense.get("currency") or "EUR")
            amount = float(expense.get("amount") or 0)
            totals[currency] = round(totals.get(currency, 0.0) + amount, 2)
            category = str(expense.get("category") or "other")
            category_totals.setdefault(category, {})[currency] = round(
                category_totals.setdefault(category, {}).get(currency, 0.0) + amount,
                2,
            )
        by_day: dict[str, dict[str, list[str]]] = {}
        by_stop: dict[str, dict[str, list[str]]] = {}

        def day_bucket(day_id: str) -> dict[str, list[str]]:
            return by_day.setdefault(day_id, {"documents": [], "expenses": [], "todos": []})

        def stop_bucket(day_id: str, stop_id: str) -> dict[str, list[str]]:
            return by_stop.setdefault(f"{day_id}/{stop_id}", {"documents": [], "expenses": [], "todos": []})

        for document in documents:
            for day_id in document.get("links", {}).get("day_ids", []):
                day_bucket(day_id)["documents"].append(document["id"])
            for link in document.get("links", {}).get("stop_links", []):
                stop_bucket(link["day_id"], link["stop_id"])["documents"].append(document["id"])
        for expense in expenses:
            if expense.get("day_id"):
                day_bucket(expense["day_id"])["expenses"].append(expense["id"])
            if expense.get("day_id") and expense.get("stop_id"):
                stop_bucket(expense["day_id"], expense["stop_id"])["expenses"].append(expense["id"])
        for todo in todos:
            if todo.get("day_id"):
                day_bucket(todo["day_id"])["todos"].append(todo["id"])
            if todo.get("day_id") and todo.get("stop_id"):
                stop_bucket(todo["day_id"], todo["stop_id"])["todos"].append(todo["id"])

        return {
            "schema_version": ARCHIVE_SCHEMA_VERSION,
            "documents": deepcopy(documents[:500]),
            "expenses": deepcopy(expenses[:1000]),
            "todos": deepcopy(todos[:1000]),
            "by_day": by_day,
            "by_stop": by_stop,
            "stats": {
                "document_count": len(documents),
                "retained_document_count": sum(1 for item in documents if item.get("file_retained")),
                "expense_count": len(expenses),
                "todo_open_count": sum(1 for item in todos if item.get("status") == "open"),
                "todo_done_count": sum(1 for item in todos if item.get("status") == "done"),
                "storage_bytes": sum(int(item.get("size_bytes") or 0) for item in documents if item.get("file_retained")),
                "totals_by_currency": totals,
                "category_totals": category_totals,
            },
        }

    def assistant_context(self, trip_id: str) -> dict[str, Any]:
        payload = self.panel_payload(trip_id)
        confirmed_docs = []
        for doc in payload["documents"]:
            if doc.get("status") not in {"confirmed", "file_removed"}:
                continue
            confirmed_docs.append(
                {
                    "id": doc["id"],
                    "title": doc["title"],
                    "document_type": doc["document_type"],
                    "provider": doc["provider"],
                    "summary": doc["summary"],
                    "links": doc["links"],
                    "sensitive": bool(doc.get("sensitive")),
                    "extracted": {} if doc.get("sensitive") else doc["extracted"],
                    "file_retained": doc["file_retained"],
                }
            )
        return {
            "documents": confirmed_docs[:100],
            "expenses": [
                {
                    key: expense.get(key)
                    for key in (
                        "id", "document_id", "day_id", "stop_id", "date", "merchant",
                        "category", "amount", "currency", "status", "notes",
                    )
                }
                for expense in payload["expenses"][:300]
            ],
            "todos": [
                {
                    key: todo.get(key)
                    for key in (
                        "id", "document_id", "day_id", "stop_id", "title", "due_at",
                        "status", "priority", "notes",
                    )
                }
                for todo in payload["todos"][:300]
            ],
            "stats": payload["stats"],
        }
