"""Pure logic for the per-day overnight plan (Stellplatz-Optionen).

The overnight options of a day are planning data of the DAY, stored in
``day.details.overnight_plan`` - not on the active overnight stop. The stop
is only the currently chosen materialization; it keeps a back-reference to
the option it was activated from. This module owns reading, validating and
transforming that structure; no other module touches the raw format.

Everything here is pure (no Home Assistant, no I/O). The revision-checked
commit around these transformations lives in trip_mutations.py.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .assistant_shared import OVERNIGHT_STOP_TYPES
from .identifiers import _new_id
from .json_io import ValidationError

PLAN_KEY = "overnight_plan"
PLAN_SCHEMA_VERSION = 1
PREFERENCES_KEY = "pitch_preferences"
PREFERENCES_SCHEMA_VERSION = 1

MAX_OVERNIGHT_OPTIONS = 6
OVERNIGHT_STRATEGIES = ("route_optimal", "best_first", "early_arrival")
DEFAULT_OVERNIGHT_STRATEGY = "route_optimal"
OPTION_STATUSES = ("backup", "rejected")
_SOURCE_TYPES = ("user", "assistant", "roadbook")

_MAX_TEXT = {"name": 200, "place_query": 300, "notes": 2000, "url": 500}
_MAX_PROS_CONS = 4
_MAX_FEATURES = 20


def _clean_str(value: Any, field: str, *, max_length: int, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"'{field}' muss ein Text sein")
    value = value.strip()
    if required and not value:
        raise ValidationError(f"'{field}' darf nicht leer sein")
    if len(value) > max_length:
        raise ValidationError(f"'{field}' ist zu lang (maximal {max_length} Zeichen)")
    return value


def _clean_location(raw: Any) -> dict[str, Any]:
    if raw in (None, {}, ""):
        return {}
    if not isinstance(raw, dict):
        raise ValidationError("'location' muss ein JSON-Objekt sein")
    latitude = raw.get("latitude")
    longitude = raw.get("longitude")
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        raise ValidationError("Ungültige Koordinaten in 'location'")
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        raise ValidationError("'location' benötigt latitude und longitude als Zahlen")
    if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
        raise ValidationError("Koordinaten in 'location' liegen außerhalb des gültigen Bereichs")
    return {"latitude": float(latitude), "longitude": float(longitude)}


def _clean_string_list(raw: Any, field: str) -> list[str]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValidationError(f"'{field}' muss eine Liste sein")
    result: list[str] = []
    for item in raw[:_MAX_PROS_CONS]:
        text = _clean_str(item, field, max_length=200)
        if text:
            result.append(text)
    return result


def _clean_price(raw: Any) -> dict[str, Any]:
    if raw in (None, {}, ""):
        return {}
    if not isinstance(raw, dict):
        raise ValidationError("'price' muss ein JSON-Objekt sein")
    amount = raw.get("amount")
    if amount is not None:
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
            raise ValidationError("'price.amount' muss eine Zahl >= 0 sein")
        amount = float(amount)
    currency = _clean_str(raw.get("currency"), "price.currency", max_length=10)
    if amount is None and not currency:
        return {}
    return {"amount": amount, "currency": currency or "EUR"}


def _clean_features(raw: Any) -> dict[str, bool]:
    if raw in (None, {}, ""):
        return {}
    if not isinstance(raw, dict):
        raise ValidationError("'features' muss ein JSON-Objekt sein")
    result: dict[str, bool] = {}
    for index, (key, value) in enumerate(raw.items()):
        if index >= _MAX_FEATURES:
            break
        name = _clean_str(key, "features", max_length=50)
        if not name:
            continue
        if not isinstance(value, bool):
            raise ValidationError(f"Merkmal '{name}' muss true oder false sein")
        result[name] = value
    return result


def _clean_source(raw: Any) -> dict[str, Any]:
    if raw in (None, {}, ""):
        return {"type": "user", "provider": None, "provider_id": None, "url": None}
    if not isinstance(raw, dict):
        raise ValidationError("'source' muss ein JSON-Objekt sein")
    source_type = _clean_str(raw.get("type") or "user", "source.type", max_length=30)
    if source_type not in _SOURCE_TYPES:
        raise ValidationError(f"Unbekannte Optionsquelle: {source_type}")
    return {
        "type": source_type,
        "provider": _clean_str(raw.get("provider"), "source.provider", max_length=100) or None,
        "provider_id": _clean_str(raw.get("provider_id"), "source.provider_id", max_length=200) or None,
        "url": _clean_str(raw.get("url"), "source.url", max_length=_MAX_TEXT["url"]) or None,
    }


def validate_option_input(raw: Any, *, now: str, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate one user- or assistant-supplied overnight option strictly."""
    if not isinstance(raw, dict):
        raise ValidationError("Die Stellplatz-Option muss ein JSON-Objekt sein")
    status = _clean_str(raw.get("status") or (existing or {}).get("status") or "backup", "status", max_length=20)
    if status not in OPTION_STATUSES:
        raise ValidationError(f"Unbekannter Optionsstatus: {status}")
    option = {
        "id": (existing or {}).get("id") or _new_id("opt"),
        "name": _clean_str(raw.get("name"), "name", max_length=_MAX_TEXT["name"], required=True),
        "place_query": _clean_str(raw.get("place_query"), "place_query", max_length=_MAX_TEXT["place_query"]),
        "location": _clean_location(raw.get("location")),
        "source": _clean_source(raw.get("source") or (existing or {}).get("source")),
        "features": _clean_features(raw.get("features")),
        "price": _clean_price(raw.get("price")),
        "notes": _clean_str(raw.get("notes"), "notes", max_length=_MAX_TEXT["notes"]),
        "pros": _clean_string_list(raw.get("pros"), "pros"),
        "cons": _clean_string_list(raw.get("cons"), "cons"),
        "status": status,
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }
    return option


def get_overnight_plan(day: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized copy of a day's overnight plan (lenient read)."""
    details = day.get("details") if isinstance(day.get("details"), dict) else {}
    raw = details.get(PLAN_KEY) if isinstance(details.get(PLAN_KEY), dict) else {}
    strategy = raw.get("strategy")
    if strategy not in OVERNIGHT_STRATEGIES:
        strategy = DEFAULT_OVERNIGHT_STRATEGY
    options = [
        deepcopy(option)
        for option in (raw.get("options") or [])
        if isinstance(option, dict) and option.get("id") and option.get("name")
    ]
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "strategy": strategy,
        "options": options[:MAX_OVERNIGHT_OPTIONS],
    }


def _write_plan(day: dict[str, Any], plan: dict[str, Any]) -> None:
    details = day.get("details")
    if not isinstance(details, dict):
        details = {}
        day["details"] = details
    details[PLAN_KEY] = plan


def find_active_overnight_stop(stops: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the day's overnight stop (the last one, matching day-end logic)."""
    for stop in reversed(stops):
        if str(stop.get("type") or "").casefold() in OVERNIGHT_STOP_TYPES:
            return stop
    return None


def apply_save_option(day_document: dict[str, Any], raw_option: Any, *, now: str) -> dict[str, Any]:
    """Insert or update one option in the day's overnight plan."""
    day = day_document["day"]
    plan = get_overnight_plan(day)
    option_id = raw_option.get("id") if isinstance(raw_option, dict) else None
    existing = next((item for item in plan["options"] if item["id"] == option_id), None)
    option = validate_option_input(raw_option, now=now, existing=existing)
    if existing is None:
        if len(plan["options"]) >= MAX_OVERNIGHT_OPTIONS:
            raise ValidationError(
                f"Ein Reisetag darf maximal {MAX_OVERNIGHT_OPTIONS} Stellplatz-Optionen haben"
            )
        plan["options"].append(option)
    else:
        plan["options"][plan["options"].index(existing)] = option
    _write_plan(day, plan)
    return option


def apply_delete_option(day_document: dict[str, Any], option_id: str) -> None:
    day = day_document["day"]
    plan = get_overnight_plan(day)
    remaining = [item for item in plan["options"] if item["id"] != option_id]
    if len(remaining) == len(plan["options"]):
        raise ValidationError(f"Stellplatz-Option nicht gefunden: {option_id}")
    plan["options"] = remaining
    _write_plan(day, plan)


def apply_set_option_status(day_document: dict[str, Any], option_id: str, status: str, *, now: str) -> dict[str, Any]:
    if status not in OPTION_STATUSES:
        raise ValidationError(f"Unbekannter Optionsstatus: {status}")
    day = day_document["day"]
    plan = get_overnight_plan(day)
    option = next((item for item in plan["options"] if item["id"] == option_id), None)
    if option is None:
        raise ValidationError(f"Stellplatz-Option nicht gefunden: {option_id}")
    option["status"] = status
    option["updated_at"] = now
    _write_plan(day, plan)
    return option


def apply_set_strategy(day_document: dict[str, Any], strategy: str) -> str:
    if strategy not in OVERNIGHT_STRATEGIES:
        raise ValidationError(f"Unbekannte Übernachtungs-Strategie: {strategy}")
    day = day_document["day"]
    plan = get_overnight_plan(day)
    plan["strategy"] = strategy
    _write_plan(day, plan)
    return strategy


def _backup_option_from_stop(raw: dict[str, Any], *, now: str) -> dict[str, Any]:
    """Validate a demotion candidate leniently - demotion must never fail.

    The candidate carries fields sourced from a live roadbook stop, which is
    normalized far more loosely than an overnight option: names up to 500
    chars, free-form location objects (address-only, no coordinates), notes
    up to several thousand chars. Strict validation would abort the whole
    activation over a field the user never entered ("Plan B" impossible
    until the OLD stop is edited) - so clamp instead of reject, and move an
    address-only location into the notes rather than silently dropping it.
    """
    candidate = deepcopy(raw)
    name = str(candidate.get("name") or "").strip()
    candidate["name"] = name[: _MAX_TEXT["name"]] or "Bisheriger Übernachtungsplatz"
    notes = str(candidate.get("notes") or "")
    location = candidate.get("location")
    if isinstance(location, dict):
        latitude = location.get("latitude")
        longitude = location.get("longitude")
        has_coordinates = (
            isinstance(latitude, (int, float))
            and not isinstance(latitude, bool)
            and isinstance(longitude, (int, float))
            and not isinstance(longitude, bool)
            and -90 <= latitude <= 90
            and -180 <= longitude <= 180
        )
        address_parts = [
            str(location.get(key)).strip()
            for key in ("address", "label", "city")
            if isinstance(location.get(key), str) and str(location.get(key)).strip()
        ]
        address_line = ", ".join(dict.fromkeys(address_parts))
        if address_line and address_line not in notes:
            notes = f"Adresse: {address_line}\n{notes}" if notes else f"Adresse: {address_line}"
        candidate["location"] = (
            {"latitude": float(latitude), "longitude": float(longitude)}
            if has_coordinates
            else {}
        )
    elif location:
        candidate["location"] = {}
    candidate["notes"] = notes[: _MAX_TEXT["notes"]]
    place_query = candidate.get("place_query")
    if isinstance(place_query, str):
        candidate["place_query"] = place_query[: _MAX_TEXT["place_query"]]
    return validate_option_input({**candidate, "status": "backup", "id": None}, now=now)


def apply_activate_option(
    day_document: dict[str, Any],
    option_id: str,
    *,
    now: str,
    actor: str,
) -> dict[str, Any]:
    """Atomically make one option the day's overnight stop.

    The previous active place (if any) is demoted back into the options list
    as a backup, so "Platz voll -> Plan B -> doch wieder Plan A" never loses
    a candidate. Returns the raw (not yet normalized) patched stop plus what
    happened, for trip_mutations to normalize and commit.
    """
    day = day_document["day"]
    plan = get_overnight_plan(day)
    option = next((item for item in plan["options"] if item["id"] == option_id), None)
    if option is None:
        raise ValidationError(f"Stellplatz-Option nicht gefunden: {option_id}")
    if option.get("status") == "rejected":
        raise ValidationError("Eine verworfene Option muss zuerst wiederhergestellt werden")
    plan["options"] = [item for item in plan["options"] if item["id"] != option_id]

    stop = find_active_overnight_stop(day_document["stops"])
    materialized = stop is None
    if stop is not None:
        details = stop.get("details") if isinstance(stop.get("details"), dict) else {}
        snapshot = details.get("active_overnight_option")
        if not isinstance(snapshot, dict):
            snapshot = {}
        if not snapshot:
            snapshot = {"source": {"type": "roadbook"}}
        # The stop's LIVE fields win over the activation-time snapshot:
        # manual edits made after activation (fixed GPS, renamed stop,
        # rewritten notes) must survive into the demoted backup - the
        # snapshot only contributes metadata the stop doesn't carry
        # (pros/cons, price, features, source).
        previous_option = {
            **snapshot,
            "name": str(
                stop.get("name") or snapshot.get("name") or "Bisheriger Übernachtungsplatz"
            ),
            "location": stop.get("location") or snapshot.get("location") or {},
            "notes": str(stop.get("notes") or snapshot.get("notes") or ""),
        }
        demoted = _backup_option_from_stop(previous_option, now=now)
        if len(plan["options"]) >= MAX_OVERNIGHT_OPTIONS:
            raise ValidationError(
                "Kein Platz für den bisherigen Übernachtungsplatz als Backup - "
                "bitte zuerst eine Option löschen"
            )
        plan["options"].append(demoted)
    else:
        demoted = None
        stop = {
            "id": _new_id("stop"),
            "type": "overnight",
            "arrival_time": None,
            "departure_time": None,
            "created_at": now,
        }
        day_document["stops"].append(stop)

    stop_details = stop.get("details") if isinstance(stop.get("details"), dict) else {}
    stop_details = deepcopy(stop_details)
    stop_details["active_overnight_option"] = deepcopy(option)
    stop_details["overnight_option_id"] = option["id"]
    stop_details["overnight_selection"] = {"selected_at": now, "selected_by": actor}
    stop.update(
        {
            "name": option["name"],
            "location": deepcopy(option.get("location") or {}),
            "notes": option.get("notes") or "",
            "details": stop_details,
            "updated_at": now,
        }
    )
    if str(stop.get("type") or "").casefold() not in OVERNIGHT_STOP_TYPES:
        stop["type"] = "overnight"

    _write_plan(day, plan)
    return {
        "stop_id": stop["id"],
        "activated_option": option,
        "demoted_option": demoted,
        "materialized": materialized,
    }


def validate_preferences_input(raw: Any) -> dict[str, Any]:
    """Validate the trip-level pitch preferences object strictly."""
    if not isinstance(raw, dict):
        raise ValidationError("Die Stellplatz-Präferenzen müssen ein JSON-Objekt sein")
    required = _clean_features(raw.get("required"))
    preferred_raw = raw.get("preferred")
    preferred: dict[str, int] = {}
    if preferred_raw not in (None, {}, ""):
        if not isinstance(preferred_raw, dict):
            raise ValidationError("'preferred' muss ein JSON-Objekt sein")
        for index, (key, value) in enumerate(preferred_raw.items()):
            if index >= 30:
                break
            name = _clean_str(key, "preferred", max_length=50)
            if not name:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= 5):
                raise ValidationError(f"Gewichtung für '{name}' muss eine ganze Zahl von 0 bis 5 sein")
            preferred[name] = value
    limits_raw = raw.get("limits")
    limits: dict[str, Any] = {}
    if limits_raw not in (None, {}, ""):
        if not isinstance(limits_raw, dict):
            raise ValidationError("'limits' muss ein JSON-Objekt sein")
        for field in ("max_price_per_night", "max_detour_km", "max_detour_minutes"):
            value = limits_raw.get(field)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValidationError(f"'{field}' muss eine Zahl >= 0 sein")
            limits[field] = float(value)
    avoid_raw = raw.get("avoid")
    avoid: list[str] = []
    if avoid_raw not in (None, "", []):
        if not isinstance(avoid_raw, list):
            raise ValidationError("'avoid' muss eine Liste sein")
        for item in avoid_raw[:20]:
            text = _clean_str(item, "avoid", max_length=100)
            if text:
                avoid.append(text)
    vehicle_raw = raw.get("vehicle")
    vehicle: dict[str, Any] = {}
    if vehicle_raw not in (None, {}, ""):
        if not isinstance(vehicle_raw, dict):
            raise ValidationError("'vehicle' muss ein JSON-Objekt sein")
        for field in ("height_m", "length_m"):
            value = vehicle_raw.get(field)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 < value < 30):
                raise ValidationError(f"'{field}' muss eine plausible Zahl in Metern sein")
            vehicle[field] = float(value)
    return {
        "schema_version": PREFERENCES_SCHEMA_VERSION,
        "required": required,
        "preferred": preferred,
        "limits": limits,
        "avoid": avoid,
        "vehicle": vehicle,
        "free_text": _clean_str(raw.get("free_text"), "free_text", max_length=2000),
    }


def get_pitch_preferences(trip: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized copy of the trip's pitch preferences (lenient read)."""
    details = trip.get("details") if isinstance(trip.get("details"), dict) else {}
    raw = details.get(PREFERENCES_KEY)
    if not isinstance(raw, dict):
        return validate_preferences_input({})
    try:
        return validate_preferences_input(raw)
    except ValidationError:
        return validate_preferences_input({})
