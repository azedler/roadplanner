"""Persistent, cross-trip crew master data: people and vehicles.

Unlike the Roadbook itself (one document per trip), the crew registry is
deliberately trip-independent: the same people and the same camper travel
together across many trips, so their details are maintained once here and a
trip only ever stores a point-in-time snapshot reference (see
`crew_manager.py`). A vehicle or person is never hard-deleted - retiring one
(e.g. selling a camper) only hides it from selection for new trips; past
trips that already embedded a snapshot are unaffected.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Any
import uuid

from .roadplanner import StorageError, ValidationError

CREW_SCHEMA_VERSION = 1
MAX_PEOPLE = 200
MAX_VEHICLES = 50
_PERSON_KINDS = frozenset({"person", "dog"})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _clean(value: Any, maximum: int = 2_000) -> str:
    return str(value or "").strip()[:maximum]


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ).encode("utf-8")
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except OSError as err:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise StorageError(f"Crew-Daten konnten nicht geschrieben werden: {path}") from err


def normalize_person(value: dict[str, Any]) -> dict[str, Any]:
    person_id = _clean(value.get("id"), 100) or new_id("person")
    name = _clean(value.get("name"), 200)
    if not name:
        raise ValidationError("Name der Person fehlt")
    kind = _clean(value.get("kind"), 20).casefold() or "person"
    if kind not in _PERSON_KINDS:
        raise ValidationError(f"Nicht unterstützte Art: {kind}")
    return {
        "id": person_id,
        "name": name,
        "kind": kind,
        "note": _clean(value.get("note"), 500),
        "active": bool(value.get("active", True)),
        "created_at": _clean(value.get("created_at"), 100) or utc_now_iso(),
        "updated_at": utc_now_iso(),
    }


def normalize_vehicle(value: dict[str, Any]) -> dict[str, Any]:
    vehicle_id = _clean(value.get("id"), 100) or new_id("vehicle")
    name = _clean(value.get("name"), 200)
    if not name:
        raise ValidationError("Name des Fahrzeugs fehlt")
    return {
        "id": vehicle_id,
        "name": name,
        "description": _clean(value.get("description"), 1_000),
        "active": bool(value.get("active", True)),
        "created_at": _clean(value.get("created_at"), 100) or utc_now_iso(),
        "updated_at": utc_now_iso(),
    }


class CrewStore:
    """Synchronous store for the cross-trip people/vehicle registries."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self._lock = RLock()

    def initialize(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _people_path(self) -> Path:
        return self.root_dir / "people.json"

    def _vehicles_path(self) -> Path:
        return self.root_dir / "vehicles.json"

    @staticmethod
    def _read(path: Path, *, list_key: str) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as err:
            raise StorageError(f"Crew-Daten konnten nicht gelesen werden: {path}") from err
        if not isinstance(raw, dict):
            raise StorageError(f"Crew-Daten sind beschädigt: {path}")
        items = raw.get(list_key)
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    def load_people(self) -> list[dict[str, Any]]:
        with self._lock:
            people = []
            for item in self._read(self._people_path(), list_key="people"):
                try:
                    people.append(normalize_person(item))
                except ValidationError:
                    continue
            return people[:MAX_PEOPLE]

    def load_vehicles(self) -> list[dict[str, Any]]:
        with self._lock:
            vehicles = []
            for item in self._read(self._vehicles_path(), list_key="vehicles"):
                try:
                    vehicles.append(normalize_vehicle(item))
                except ValidationError:
                    continue
            return vehicles[:MAX_VEHICLES]

    def _write_people(self, people: list[dict[str, Any]]) -> None:
        _atomic_write(
            self._people_path(),
            {
                "schema_version": CREW_SCHEMA_VERSION,
                "updated_at": utc_now_iso(),
                "people": people,
            },
        )

    def _write_vehicles(self, vehicles: list[dict[str, Any]]) -> None:
        _atomic_write(
            self._vehicles_path(),
            {
                "schema_version": CREW_SCHEMA_VERSION,
                "updated_at": utc_now_iso(),
                "vehicles": vehicles,
            },
        )

    def create_person(self, value: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            people = self.load_people()
            if len(people) >= MAX_PEOPLE:
                raise ValidationError("Es sind bereits zu viele Personen angelegt")
            person = normalize_person({**value, "id": None})
            people.insert(0, person)
            self._write_people(people)
            return deepcopy(person)

    def update_person(self, person_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            people = self.load_people()
            for index, item in enumerate(people):
                if item["id"] != person_id:
                    continue
                merged = normalize_person({**item, **deepcopy(patch), "id": person_id})
                people[index] = merged
                self._write_people(people)
                return deepcopy(merged)
            raise ValidationError(f"Person nicht gefunden: {person_id}")

    def set_person_active(self, person_id: str, active: bool) -> dict[str, Any]:
        return self.update_person(person_id, {"active": active})

    def create_vehicle(self, value: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            vehicles = self.load_vehicles()
            if len(vehicles) >= MAX_VEHICLES:
                raise ValidationError("Es sind bereits zu viele Fahrzeuge angelegt")
            vehicle = normalize_vehicle({**value, "id": None})
            vehicles.insert(0, vehicle)
            self._write_vehicles(vehicles)
            return deepcopy(vehicle)

    def update_vehicle(self, vehicle_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            vehicles = self.load_vehicles()
            for index, item in enumerate(vehicles):
                if item["id"] != vehicle_id:
                    continue
                merged = normalize_vehicle({**item, **deepcopy(patch), "id": vehicle_id})
                vehicles[index] = merged
                self._write_vehicles(vehicles)
                return deepcopy(merged)
            raise ValidationError(f"Fahrzeug nicht gefunden: {vehicle_id}")

    def set_vehicle_active(self, vehicle_id: str, active: bool) -> dict[str, Any]:
        return self.update_vehicle(vehicle_id, {"active": active})
