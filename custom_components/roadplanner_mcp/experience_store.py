"""Persistent decisions and external-media references for Roadplanner.

The canonical Roadbook remains the only route source of truth. Decision cards
and media assignments live in a private per-trip sidecar below the configured
Roadplanner archive directory. Original OneDrive files are never copied into
Home Assistant by this module.
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

from .roadplanner import StorageError, ValidationError, validate_identifier

EXPERIENCE_SCHEMA_VERSION = 2
MAX_DECISIONS_PER_TRIP = 250
MAX_MEDIA_PER_TRIP = 20_000
MAX_OPTIONS_PER_DECISION = 5
MAX_DESTINATION_GALLERIES_PER_TRIP = 2_000
MAX_DESTINATION_IMAGES_PER_GALLERY = 3

_DECISION_STATUS = frozenset({"draft", "open", "selected", "transferred", "archived"})
_ASSIGNMENT_STATUS = frozenset({"automatic", "suggested", "manual", "unassigned"})
_MEDIA_TYPES = frozenset({"photo", "video"})
_GALLERY_STATUS = frozenset({"ready", "empty", "partial", "error"})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _clean(value: Any, maximum: int = 4_000) -> str:
    text = str(value or "").strip()
    return text[:maximum]


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 12:
        raise ValidationError("Erlebnisdaten sind zu tief verschachtelt")
    if value is None or isinstance(value, (bool, int, str)):
        if isinstance(value, str) and len(value) > 100_000:
            raise ValidationError("Erlebnisdaten enthalten zu langen Text")
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValidationError("Erlebnisdaten enthalten ungültige Zahlen")
        return value
    if isinstance(value, list):
        if len(value) > 25_000:
            raise ValidationError("Erlebnisdaten enthalten zu viele Einträge")
        return [_json_safe(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > 5_000:
            raise ValidationError("Erlebnisdaten enthalten zu viele Felder")
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 500:
                raise ValidationError("Erlebnisdaten enthalten ungültige Schlüssel")
            result[key] = _json_safe(item, depth=depth + 1)
        return result
    raise ValidationError("Erlebnisdaten sind nicht JSON-kompatibel")


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8")
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
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
        raise StorageError(f"Erlebnisdaten konnten nicht geschrieben werden: {path}") from err


def _normalize_location(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    latitude = source.get("latitude", source.get("lat"))
    longitude = source.get("longitude", source.get("lon", source.get("lng")))
    result: dict[str, Any] = {}
    if isinstance(latitude, (int, float)) and not isinstance(latitude, bool) and -90 <= float(latitude) <= 90:
        result["latitude"] = round(float(latitude), 7)
    if isinstance(longitude, (int, float)) and not isinstance(longitude, bool) and -180 <= float(longitude) <= 180:
        result["longitude"] = round(float(longitude), 7)
    for key in ("label", "address", "city", "country_code"):
        text = _clean(source.get(key), 1_000)
        if text:
            result[key] = text
    return result


def _normalize_image(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    result = {
        key: text
        for key in (
            "id",
            "media_id",
            "image_url",
            "thumbnail_url",
            "original_url",
            "source_url",
            "alt",
            "attribution",
            "provider",
            "author",
            "license",
            "license_url",
        )
        if (text := _clean(source.get(key), 2_000))
    }
    for key in ("width", "height"):
        raw = source.get(key)
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            result[key] = raw
    return result


def normalize_destination_gallery(raw: dict[str, Any]) -> dict[str, Any]:
    stop_id = validate_identifier(raw.get("stop_id"), "destination_gallery.stop_id")
    day_id = validate_identifier(raw.get("day_id"), "destination_gallery.day_id")
    images: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw.get("images", []):
        image = _normalize_image(item)
        identity = str(
            image.get("id")
            or image.get("source_url")
            or image.get("image_url")
            or ""
        )
        if not (image.get("image_url") or image.get("media_id")) or not identity or identity in seen:
            continue
        seen.add(identity)
        if not image.get("id"):
            image["id"] = f"image-{len(images) + 1}"
        images.append(image)
        if len(images) >= MAX_DESTINATION_IMAGES_PER_GALLERY:
            break
    status = str(raw.get("status") or ("ready" if images else "empty"))
    if status not in _GALLERY_STATUS:
        status = "ready" if images else "empty"
    primary_image_id = _clean(raw.get("primary_image_id"), 500)
    if not any(item.get("id") == primary_image_id for item in images):
        primary_image_id = str(images[0].get("id") or "") if images else ""
    provider_errors = raw.get("provider_errors")
    if not isinstance(provider_errors, dict):
        provider_errors = {}
    return {
        "stop_id": stop_id,
        "day_id": day_id,
        "query": _clean(raw.get("query"), 1_000),
        "query_fingerprint": _clean(raw.get("query_fingerprint"), 200),
        "status": status,
        "images": images,
        "primary_image_id": primary_image_id or None,
        "provider_errors": _json_safe(provider_errors),
        "attempted_at": _clean(raw.get("attempted_at"), 100) or utc_now_iso(),
        "updated_at": _clean(raw.get("updated_at"), 100) or utc_now_iso(),
    }


def resolve_decision_media_references(
    decisions: list[dict[str, Any]],
    media: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve persisted OneDrive media IDs to fresh panel URLs.

    Decision sidecars must not persist short-lived signed Home Assistant URLs.
    They store only ``media_id`` and receive current URLs whenever a panel
    payload is built.
    """
    resolved = deepcopy(decisions)
    media_by_id = {
        str(item.get("id") or ""): item
        for item in media
        if isinstance(item, dict) and item.get("id")
    }
    for decision in resolved:
        if not isinstance(decision, dict):
            continue
        for option in list(decision.get("options") or []):
            if not isinstance(option, dict):
                continue
            images: list[dict[str, Any]] = []
            for raw_image in list(option.get("images") or [])[:3]:
                if not isinstance(raw_image, dict):
                    continue
                image = deepcopy(raw_image)
                media_id = str(image.get("media_id") or "")
                media_item = media_by_id.get(media_id) if media_id else None
                if media_item is not None:
                    image.update({
                        "id": image.get("id") or f"media-{media_id}",
                        "provider": "onedrive",
                        "image_url": media_item.get("thumbnail_url"),
                        "thumbnail_url": media_item.get("thumbnail_url"),
                        "original_url": media_item.get("original_url"),
                        "source_url": media_item.get("original_url"),
                        "alt": image.get("alt")
                        or media_item.get("caption")
                        or media_item.get("name")
                        or option.get("title"),
                        "attribution": image.get("attribution") or "Eigenes Reisefoto",
                    })
                if image.get("image_url"):
                    images.append(image)
            option["images"] = images
            primary = option.get("image") if isinstance(option.get("image"), dict) else {}
            primary_media_id = str(primary.get("media_id") or "")
            primary_item = media_by_id.get(primary_media_id) if primary_media_id else None
            if primary_item is not None:
                option["image"] = {
                    **deepcopy(primary),
                    "id": primary.get("id") or f"media-{primary_media_id}",
                    "provider": "onedrive",
                    "image_url": primary_item.get("thumbnail_url"),
                    "thumbnail_url": primary_item.get("thumbnail_url"),
                    "original_url": primary_item.get("original_url"),
                    "source_url": primary_item.get("original_url"),
                    "alt": primary.get("alt")
                    or primary_item.get("caption")
                    or primary_item.get("name")
                    or option.get("title"),
                    "attribution": primary.get("attribution") or "Eigenes Reisefoto",
                }
            elif images:
                option["image"] = deepcopy(images[0])
            else:
                option["image"] = {}
    return resolved


def normalize_decision(raw: dict[str, Any]) -> dict[str, Any]:
    decision_id = validate_identifier(raw.get("id"), "decision.id")
    trip_id = validate_identifier(raw.get("trip_id"), "decision.trip_id")
    status = str(raw.get("status") or "open")
    if status not in _DECISION_STATUS:
        status = "open"
    options: list[dict[str, Any]] = []
    for index, option_raw in enumerate(raw.get("options", [])):
        if not isinstance(option_raw, dict):
            continue
        option_id = _clean(option_raw.get("id"), 200) or f"option-{index + 1}"
        option = {
            "id": option_id,
            "title": _clean(option_raw.get("title"), 500) or f"Option {index + 1}",
            "summary": _clean(option_raw.get("summary"), 4_000),
            "place_query": _clean(option_raw.get("place_query"), 1_000),
            "stop_type": _clean(option_raw.get("stop_type"), 100) or "waypoint",
            "pros": [_clean(item, 500) for item in list(option_raw.get("pros") or [])[:8] if _clean(item, 500)],
            "cons": [_clean(item, 500) for item in list(option_raw.get("cons") or [])[:8] if _clean(item, 500)],
            "location": _normalize_location(option_raw.get("location")),
            "image": _normalize_image(option_raw.get("image")),
            "images": [
                image
                for item in list(option_raw.get("images") or [])[:3]
                if (image := _normalize_image(item)).get("image_url") or image.get("media_id")
            ],
            "route_metrics": _json_safe(option_raw.get("route_metrics") if isinstance(option_raw.get("route_metrics"), dict) else {}),
            "estimated_cost": _json_safe(option_raw.get("estimated_cost") if isinstance(option_raw.get("estimated_cost"), dict) else {}),
            "details": _json_safe(option_raw.get("details") if isinstance(option_raw.get("details"), dict) else {}),
            "is_current_plan": bool(option_raw.get("is_current_plan", False)),
            "change_type": _clean(option_raw.get("change_type"), 80) or "choose",
            "existing_stop_id": _clean(option_raw.get("existing_stop_id"), 200) or None,
        }
        if option["images"] and not (option["image"].get("image_url") or option["image"].get("media_id")):
            option["image"] = deepcopy(option["images"][0])
        elif (option["image"].get("image_url") or option["image"].get("media_id")) and not option["images"]:
            option["images"] = [deepcopy(option["image"])]
        options.append(option)
        if len(options) >= MAX_OPTIONS_PER_DECISION:
            break
    if len(options) < 2:
        raise ValidationError("Eine Entscheidungsvorlage benötigt mindestens zwei Optionen")
    linked_day_id = _clean(raw.get("linked_day_id"), 200)
    return {
        "id": decision_id,
        "trip_id": trip_id,
        "title": _clean(raw.get("title"), 500) or "Entscheidung",
        "question": _clean(raw.get("question"), 2_000),
        "status": status,
        "linked_day_id": linked_day_id or None,
        "source_message_id": _clean(raw.get("source_message_id"), 200) or None,
        "baseline_required": bool(raw.get("baseline_required", False)),
        "current_plan_option_id": _clean(raw.get("current_plan_option_id"), 200) or None,
        "options": options,
        "selected_option_id": _clean(raw.get("selected_option_id"), 200) or None,
        "transferred_draft_id": _clean(raw.get("transferred_draft_id"), 200) or None,
        "created_at": _clean(raw.get("created_at"), 100) or utc_now_iso(),
        "updated_at": _clean(raw.get("updated_at"), 100) or utc_now_iso(),
        "notes": _clean(raw.get("notes"), 4_000),
    }


def normalize_media(raw: dict[str, Any]) -> dict[str, Any]:
    media_id = validate_identifier(raw.get("id"), "media.id")
    trip_id = validate_identifier(raw.get("trip_id"), "media.trip_id")
    provider_item_id = _clean(raw.get("provider_item_id"), 500)
    if not provider_item_id:
        raise ValidationError("Medieneintrag ohne Provider-ID")
    media_type = str(raw.get("media_type") or "photo")
    if media_type not in _MEDIA_TYPES:
        media_type = "photo"
    assignment = str(raw.get("assignment_status") or "unassigned")
    if assignment not in _ASSIGNMENT_STATUS:
        assignment = "unassigned"
    confidence_raw = raw.get("confidence")
    confidence = float(confidence_raw) if isinstance(confidence_raw, (int, float)) and not isinstance(confidence_raw, bool) else 0.0
    confidence = max(0.0, min(confidence, 1.0))
    distance_raw = raw.get("distance_m")
    distance = float(distance_raw) if isinstance(distance_raw, (int, float)) and not isinstance(distance_raw, bool) else None
    return {
        "id": media_id,
        "trip_id": trip_id,
        "provider": "onedrive",
        "provider_item_id": provider_item_id,
        "drive_id": _clean(raw.get("drive_id"), 500) or None,
        "name": _clean(raw.get("name"), 1_000) or "Foto",
        "mime_type": _clean(raw.get("mime_type"), 200),
        "media_type": media_type,
        "size_bytes": max(0, int(raw.get("size_bytes") or 0)),
        "taken_at": _clean(raw.get("taken_at"), 100) or None,
        "created_at": _clean(raw.get("created_at"), 100) or None,
        "modified_at": _clean(raw.get("modified_at"), 100) or None,
        "web_url": _clean(raw.get("web_url"), 2_000) or None,
        "location": _normalize_location(raw.get("location")),
        "file_hash": _clean(raw.get("file_hash"), 500) or None,
        "width": int(raw.get("width") or 0) if isinstance(raw.get("width"), int) and not isinstance(raw.get("width"), bool) and raw.get("width") > 0 else None,
        "height": int(raw.get("height") or 0) if isinstance(raw.get("height"), int) and not isinstance(raw.get("height"), bool) and raw.get("height") > 0 else None,
        "linked_day_id": _clean(raw.get("linked_day_id"), 200) or None,
        "linked_stop_id": _clean(raw.get("linked_stop_id"), 200) or None,
        "assignment_status": assignment,
        "confidence": round(confidence, 4),
        "distance_m": round(distance, 1) if distance is not None and distance >= 0 else None,
        "caption": _clean(raw.get("caption"), 2_000),
        "is_cover": bool(raw.get("is_cover", False)),
        "thumbnail_available": bool(raw.get("thumbnail_available", True)),
        "last_seen_at": _clean(raw.get("last_seen_at"), 100) or utc_now_iso(),
    }


class ExperienceStore:
    """Synchronous private sidecar store for decisions and media metadata."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.trips_dir = root_dir / "trips"
        self._lock = RLock()

    def initialize(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.trips_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, trip_id: str) -> Path:
        trip_id = validate_identifier(trip_id, "trip_id")
        return self.trips_dir / trip_id / "experience.json"

    def _default(self, trip_id: str) -> dict[str, Any]:
        return {
            "schema_version": EXPERIENCE_SCHEMA_VERSION,
            "trip_id": trip_id,
            "updated_at": utc_now_iso(),
            "decisions": [],
            "media": [],
            "media_sync": {},
            "destination_galleries": {},
        }

    def load(self, trip_id: str) -> dict[str, Any]:
        trip_id = validate_identifier(trip_id, "trip_id")
        path = self._path(trip_id)
        if not path.exists():
            return self._default(trip_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as err:
            raise StorageError(f"Erlebnisdaten konnten nicht gelesen werden: {path}") from err
        if not isinstance(raw, dict) or raw.get("trip_id") != trip_id:
            raise StorageError("Erlebnisdaten gehören zu einer anderen Reise")
        if int(raw.get("schema_version") or 0) > EXPERIENCE_SCHEMA_VERSION:
            raise StorageError("Das Erlebnisdatenschema ist neuer als diese Roadplanner-Version")
        decisions: list[dict[str, Any]] = []
        for item in raw.get("decisions", []):
            if not isinstance(item, dict):
                continue
            try:
                decisions.append(normalize_decision(item))
            except ValidationError:
                continue
        media: list[dict[str, Any]] = []
        for item in raw.get("media", []):
            if not isinstance(item, dict):
                continue
            try:
                media.append(normalize_media(item))
            except ValidationError:
                continue
        galleries: dict[str, dict[str, Any]] = {}
        raw_galleries = raw.get("destination_galleries")
        if isinstance(raw_galleries, dict):
            for stop_id, item in raw_galleries.items():
                if not isinstance(item, dict):
                    continue
                try:
                    gallery = normalize_destination_gallery({**item, "stop_id": stop_id})
                except ValidationError:
                    continue
                galleries[gallery["stop_id"]] = gallery
                if len(galleries) >= MAX_DESTINATION_GALLERIES_PER_TRIP:
                    break
        return {
            "schema_version": EXPERIENCE_SCHEMA_VERSION,
            "trip_id": trip_id,
            "updated_at": _clean(raw.get("updated_at"), 100) or utc_now_iso(),
            "decisions": decisions[:MAX_DECISIONS_PER_TRIP],
            "media": media[:MAX_MEDIA_PER_TRIP],
            "media_sync": _json_safe(raw.get("media_sync") if isinstance(raw.get("media_sync"), dict) else {}),
            "destination_galleries": galleries,
        }

    def write(self, state: dict[str, Any]) -> None:
        value = deepcopy(state)
        value["schema_version"] = EXPERIENCE_SCHEMA_VERSION
        value["updated_at"] = utc_now_iso()
        _atomic_write(self._path(value["trip_id"]), value)

    def create_decision(self, trip_id: str, value: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            state = self.load(trip_id)
            if len(state["decisions"]) >= MAX_DECISIONS_PER_TRIP:
                raise ValidationError("Diese Reise enthält bereits zu viele Entscheidungsvorlagen")
            decision = normalize_decision({**value, "trip_id": trip_id, "id": value.get("id") or new_id("decision")})
            state["decisions"].insert(0, decision)
            self.write(state)
            return deepcopy(decision)

    def update_decision(self, trip_id: str, decision_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            state = self.load(trip_id)
            decision_id = validate_identifier(decision_id, "decision_id")
            for index, item in enumerate(state["decisions"]):
                if item["id"] != decision_id:
                    continue
                merged = {**item, **deepcopy(patch), "id": decision_id, "trip_id": trip_id, "updated_at": utc_now_iso()}
                state["decisions"][index] = normalize_decision(merged)
                self.write(state)
                return deepcopy(state["decisions"][index])
            raise ValidationError(f"Entscheidung nicht gefunden: {decision_id}")

    def delete_decision(self, trip_id: str, decision_id: str) -> None:
        with self._lock:
            state = self.load(trip_id)
            before = len(state["decisions"])
            state["decisions"] = [item for item in state["decisions"] if item.get("id") != decision_id]
            if len(state["decisions"]) == before:
                raise ValidationError(f"Entscheidung nicht gefunden: {decision_id}")
            self.write(state)

    def upsert_media(self, trip_id: str, items: list[dict[str, Any]], *, sync_state: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            state = self.load(trip_id)
            by_provider = {item["provider_item_id"]: index for index, item in enumerate(state["media"])}
            added = 0
            updated = 0
            for raw in items:
                provider_item_id = _clean(raw.get("provider_item_id"), 500)
                if not provider_item_id:
                    continue
                if provider_item_id in by_provider:
                    index = by_provider[provider_item_id]
                    existing = state["media"][index]
                    # Recalculate automatic/suggested assignments after route or GPS
                    # changes.  A manual user assignment, caption and cover choice
                    # always win over the next provider sync.
                    preserve: dict[str, Any] = {
                        key: existing.get(key)
                        for key in ("caption", "is_cover")
                        if existing.get(key) not in (None, "", False)
                    }
                    if existing.get("assignment_status") == "manual":
                        preserve.update(
                            {
                                key: existing.get(key)
                                for key in (
                                    "linked_day_id",
                                    "linked_stop_id",
                                    "assignment_status",
                                    "confidence",
                                    "distance_m",
                                )
                            }
                        )
                    merged = {**existing, **raw, **preserve, "id": existing["id"], "trip_id": trip_id}
                    state["media"][index] = normalize_media(merged)
                    updated += 1
                elif len(state["media"]) < MAX_MEDIA_PER_TRIP:
                    item = normalize_media({**raw, "trip_id": trip_id, "id": raw.get("id") or new_id("media")})
                    state["media"].append(item)
                    by_provider[provider_item_id] = len(state["media"]) - 1
                    added += 1
            if sync_state is not None:
                state["media_sync"] = _json_safe(sync_state)
            state["media"].sort(key=lambda item: item.get("taken_at") or item.get("created_at") or "", reverse=True)
            self.write(state)
            return {"added": added, "updated": updated, "total": len(state["media"])}

    def remove_media_by_provider_ids(self, trip_id: str, provider_item_ids: set[str], *, sync_state: dict[str, Any] | None = None) -> int:
        with self._lock:
            state = self.load(trip_id)
            before = len(state["media"])
            state["media"] = [item for item in state["media"] if item.get("provider_item_id") not in provider_item_ids]
            if sync_state is not None:
                state["media_sync"] = _json_safe(sync_state)
            self.write(state)
            return before - len(state["media"])

    def upsert_destination_galleries(
        self,
        trip_id: str,
        galleries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            state = self.load(trip_id)
            stored = dict(state.get("destination_galleries") or {})
            updated = 0
            for raw in galleries:
                if not isinstance(raw, dict):
                    continue
                gallery = normalize_destination_gallery(raw)
                stored[gallery["stop_id"]] = gallery
                updated += 1
                if len(stored) >= MAX_DESTINATION_GALLERIES_PER_TRIP:
                    break
            state["destination_galleries"] = stored
            self.write(state)
            return {"updated": updated, "total": len(stored)}

    def update_destination_gallery(
        self,
        trip_id: str,
        stop_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            state = self.load(trip_id)
            stop_id = validate_identifier(stop_id, "stop_id")
            existing = (state.get("destination_galleries") or {}).get(stop_id)
            if not isinstance(existing, dict):
                raise ValidationError(f"Bildergalerie nicht gefunden: {stop_id}")
            gallery = normalize_destination_gallery({**existing, **deepcopy(patch), "stop_id": stop_id})
            state["destination_galleries"][stop_id] = gallery
            self.write(state)
            return deepcopy(gallery)

    def delete_destination_gallery(self, trip_id: str, stop_id: str) -> None:
        with self._lock:
            state = self.load(trip_id)
            stop_id = validate_identifier(stop_id, "stop_id")
            if stop_id not in state.get("destination_galleries", {}):
                return
            state["destination_galleries"].pop(stop_id, None)
            self.write(state)

    def update_media(self, trip_id: str, media_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            state = self.load(trip_id)
            media_id = validate_identifier(media_id, "media_id")
            for index, item in enumerate(state["media"]):
                if item["id"] != media_id:
                    continue
                merged = {**item, **deepcopy(patch), "id": media_id, "trip_id": trip_id}
                updated = normalize_media(merged)
                if updated.get("is_cover") and updated.get("linked_stop_id"):
                    for other in state["media"]:
                        if other["id"] != media_id and other.get("linked_stop_id") == updated.get("linked_stop_id"):
                            other["is_cover"] = False
                state["media"][index] = updated
                self.write(state)
                return deepcopy(updated)
            raise ValidationError(f"Foto nicht gefunden: {media_id}")

    def delete_media(self, trip_id: str, media_id: str) -> None:
        with self._lock:
            state = self.load(trip_id)
            before = len(state["media"])
            state["media"] = [item for item in state["media"] if item.get("id") != media_id]
            if len(state["media"]) == before:
                raise ValidationError(f"Foto nicht gefunden: {media_id}")
            self.write(state)
