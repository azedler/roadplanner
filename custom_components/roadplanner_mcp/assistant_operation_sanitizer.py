"""Turn one compiled+alias-normalized assistant operation into a strictly
validated ChangeSet operation.

This is the final gate before an operation is accepted into a ChangeSet:
resolve day/stop IDs from context and relative-day/overnight-continuity
markers in free text, then validate the operation envelope, allowed fields,
and stop-ordering position bookkeeping. Nothing here talks to a provider.
"""

from __future__ import annotations

from copy import deepcopy
import json
import logging
import re
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import uuid4

from .assistant_basket import _deep_without_none
from .assistant_compile import (
    _ALLOWED_ACTIONS,
    _ALLOWED_CHANGE_FIELDS,
    _ALLOWED_OPERATION_FIELDS,
    _ALLOWED_STOP_TYPES,
    _CHANGE_FIELDS_BY_ENTITY,
    _SERVER_CONTROLLED_OPERATION_FIELDS,
    _normalize_compiled_day_reference,
    _normalize_compiled_operation_aliases,
)
from .assistant_shared import (
    OVERNIGHT_STOP_TYPES,
    _utc_now_iso,
    _ALLOWED_ENTITY_TYPES,
    _GERMAN_DATE_IN_TEXT,
    _ISO_DATE_IN_TEXT,
    _clean_text,
)
from .canonical_day import canonical_roadbook_stops
from .pitch_options import get_overnight_plan, merge_assistant_overnight_plan

# Candidate links inside model output / basket decisions: Park4Night place
# pages and Google-Maps links (incl. share short links).
_P4N_CANDIDATE_RE = re.compile(
    r"park4night\.com/(?:[a-z]{2}/)?(?:place|lieu)/(\d+)", re.IGNORECASE
)
_MAPS_CANDIDATE_RE = re.compile(
    r"https://(?:maps\.app\.goo\.gl|goo\.gl/maps|(?:www\.)?google\.[a-z.]+/maps|maps\.google\.[a-z.]+)[^\s\"'<>]*",
    re.IGNORECASE,
)
_ALTERNATIVE_INTENT_RE = re.compile(
    r"alternativ|zweite\s+option|plan\s*b|backup|ausweich", re.IGNORECASE
)
# "... Übernachtungsoption ...", "... schlafen ...", "... Stellplatz ..." -
# what makes an alternative an OVERNIGHT alternative rather than any other
# second choice. Deliberately tolerant of the typos a phone keyboard
# produces ("übernachrungsoption").
_OVERNIGHT_INTENT_RE = re.compile(
    r"übernach|ubernach|übernacht|overnight|schlaf|nacht|stellplatz|"
    r"campingplatz|camping|wildcamp|platz\s+für\s+die\s+nacht",
    re.IGNORECASE,
)
_GENERIC_URL_CANDIDATE_RE = re.compile(r"https://[^\s\"'<>\)\]]+")
# A typed coordinate pair with real precision (>= 3 decimals) - version
# numbers and prices never look like this.
_COORD_CANDIDATE_RE = re.compile(
    r"(-?\d{1,3}\.\d{3,8})\s*,\s*(-?\d{1,3}\.\d{3,8})"
)


def _name_from_url_slug(url: str) -> str:
    """Derive a readable placeholder name from a page URL's last segment."""
    path = urlparse(url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1] if path else ""
    if not slug or slug.isdigit():
        return ""
    words = re.sub(r"[-_]+", " ", unquote(slug)).strip()
    if not words or len(words) < 3:
        return ""
    return words[:200].title()


def _overnight_candidates_from_text(material: str) -> list[dict[str, Any]]:
    """Deterministically extract overnight candidates from free text."""
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p4n_id in _P4N_CANDIDATE_RE.findall(material or ""):
        if p4n_id in seen:
            continue
        seen.add(p4n_id)
        candidates.append({"url": f"https://park4night.com/lieu/{p4n_id}/"})
    for url in _MAPS_CANDIDATE_RE.findall(material or ""):
        cleaned = url.rstrip(".,;:)")
        if cleaned in seen:
            continue
        seen.add(cleaned)
        candidates.append({"url": cleaned})
    for url in _GENERIC_URL_CANDIDATE_RE.findall(material or ""):
        # Any other place page the text names (naturkartan.se, campsite
        # website ...) is a candidate too - live report: a naturkartan
        # alternative was silently dropped because only Park4Night and
        # Google-Maps links were salvaged.
        cleaned = url.rstrip(".,;:)")
        if cleaned in seen or _P4N_CANDIDATE_RE.search(cleaned) or _MAPS_CANDIDATE_RE.match(cleaned):
            continue
        seen.add(cleaned)
        candidate: dict[str, Any] = {"url": cleaned}
        slug_name = _name_from_url_slug(cleaned)
        if slug_name:
            candidate["name"] = slug_name
        candidates.append(candidate)
    coordinate_pairs = [
        (latitude, longitude)
        for latitude, longitude in _COORD_CANDIDATE_RE.findall(material or "")
        if -90 <= float(latitude) <= 90
        and -180 <= float(longitude) <= 180
        and float(latitude) != 0
    ]
    if coordinate_pairs:
        # Coordinates named in the decision text (typically the user's own
        # numbers) become the candidate's place_query - and its reviewable
        # position (live report: "Koordinaten: 59.924128, 15.284795" was
        # acknowledged in chat and then lost entirely).
        latitude, longitude = coordinate_pairs[0]
        pair_query = f"{latitude},{longitude}"
        location = {"latitude": float(latitude), "longitude": float(longitude)}
        if candidates:
            candidates[0].setdefault("place_query", pair_query)
            candidates[0].setdefault("location", location)
        else:
            candidates.append(
                {
                    "name": "Stellplatz (Koordinaten)",
                    "place_query": pair_query,
                    "location": location,
                }
            )
    return candidates[:3]
from .destination_intelligence import (
    _PARK4NIGHT_ID_RE,
    _URL_RE,
    _clean_park4night_suffix,
    _google_maps_host,
)
from .roadplanner import ValidationError
from .structured_output import StructuredOutputError, normalize_changes_mapping

_LOGGER = logging.getLogger(__name__)


class NoOperationChange(Exception):
    """An update operation that would change nothing at all.

    Not a validation failure: the operation is simply empty and gets dropped,
    while every other operation of the same draft survives. Carries enough
    context for a human-readable note in the draft's assumptions.
    """

    def __init__(self, *, entity_type: str, entity_id: str, reason: str) -> None:
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.reason = reason
        super().__init__(
            f"Operation ohne Änderung ({entity_type} {entity_id or 'ohne ID'})"
        )

    @property
    def note(self) -> str:
        """One German line for the Änderungsübersicht."""
        label = {
            "trip": "Reise",
            "day": "Reisetag",
            "stop": "Stopp",
            "preference": "Präferenz",
        }.get(self.entity_type, self.entity_type)
        target = f"{label} {self.entity_id}" if self.entity_id else label
        detail = _clean_text(self.reason, maximum=300)
        suffix = f" ({detail})" if detail else ""
        return (
            f"Übersprungen: Der Assistent hat für {target} eine Aktualisierung "
            f"ohne konkrete Änderung geliefert.{suffix}"
        )


def _basket_text(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    return " ".join(
        str(item.get(key) or "") for key in ("title", "text", "detail", "note")
    ).strip()


def overnight_alternative_gap(
    operations: list[dict[str, Any]],
    *,
    basket: list[dict[str, Any]] | None,
    day_ids: set[str],
    current_day_id: str = "",
) -> tuple[str, list[dict[str, Any]]] | None:
    """Find an overnight alternative the user asked for that no operation parks.

    Live report: "Das ist die Alternative Übernachtungsoption heute Nacht" +
    a Google-Maps link produced a draft that applied cleanly - and then the
    option was nowhere under "Stellplätze". Every earlier salvage sat INSIDE
    the day's overnight_plan branch, so it could only repair a plan the
    model had already emitted; a model that answered with something else
    entirely (or with nothing for that day) slipped past all of them.

    Returns ``(day_id, candidates)`` when the request is unmistakable - the
    decision text names an ALTERNATIVE and an OVERNIGHT, and carries a link
    or coordinates - and the batch contains no overnight option for that
    day. Never guesses the day: without a single unambiguous target the
    request is left alone.
    """
    material = " ".join(
        text
        for text in (_basket_text(item) for item in basket or [])
        if _ALTERNATIVE_INTENT_RE.search(text) and _OVERNIGHT_INTENT_RE.search(text)
    ).strip()
    if not material:
        return None
    candidates = _overnight_candidates_from_text(material)
    if not candidates:
        return None

    referenced: set[str] = set()
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        if operation.get("entity_type") == "day":
            entity_id = str(operation.get("entity_id") or "")
            if entity_id in day_ids:
                referenced.add(entity_id)
        else:
            day_id = str(operation.get("day_id") or "")
            if day_id in day_ids:
                referenced.add(day_id)
    if len(referenced) == 1:
        target = next(iter(referenced))
    elif current_day_id in day_ids and not referenced:
        target = current_day_id
    else:
        return None

    for operation in operations:
        if (
            isinstance(operation, dict)
            and operation.get("entity_type") == "day"
            and str(operation.get("entity_id") or "") == target
        ):
            details = operation.get("changes", {}).get("details")
            plan = details.get("overnight_plan") if isinstance(details, dict) else None
            if isinstance(plan, dict) and plan.get("options"):
                return None
    return target, candidates


_PAST_OVERNIGHT_MARKERS = (
    "letzte nacht",
    "letzten nacht",
    "vergangene nacht",
    "gestern nacht",
    "gestern übernachtet",
    "gestern uebernachtet",
    "hier geschlafen",
    "heute hier geschlafen",
    "tatsächlicher übernachtungsort",
    "tatsaechlicher uebernachtungsort",
    "actual overnight",
    "last night",
)

_CURRENT_OVERNIGHT_MARKERS = (
    "heute nacht",
    "für heute nacht",
    "fuer heute nacht",
    "heute übernachten",
    "heute uebernachten",
    "heute schlafen",
    "tonight",
)

_CURRENT_DAY_MARKERS = ("heute", "today", "aktuell", "jetzt")
_PREVIOUS_DAY_MARKERS = ("gestern", "yesterday")
_NEXT_DAY_MARKERS = ("morgen", "tomorrow")


def _strip_park4night_from_name(changes: dict[str, Any]) -> None:
    """Keep Park4Night IDs out of stop names without ever losing them.

    The assistant regularly writes names like "Parkplatz am Angelteich
    (p4n #506374)". The ID is a valuable provider identity (enrichment
    classifies such stops as camping and links the p4n page from it), but
    it is metadata, not part of the place's name. Strip it from the name at
    ingestion and guarantee the reference survives in the notes as a real
    URL - _source_hints() scans name AND notes, so detection keeps working
    everywhere downstream.
    """
    name = str(changes.get("name") or "")
    match = _PARK4NIGHT_ID_RE.search(name)
    if match is None:
        return
    cleaned = _clean_park4night_suffix(name)
    if not cleaned:
        # The name IS only the reference - keep it, an empty name is worse.
        return
    changes["name"] = cleaned
    identifier = match.group("id")
    other_text = " ".join(
        str(changes.get(field) or "") for field in ("notes", "place_query")
    )
    if identifier in other_text:
        return
    notes = str(changes.get("notes") or "").rstrip()
    reference = f"Park4Night: https://park4night.com/lieu/{identifier}/"
    changes["notes"] = (f"{notes}\n{reference}" if notes else reference)[:4_000]


def _basket_item_text(item: dict[str, Any]) -> str:
    values = item.get("values") if isinstance(item.get("values"), dict) else {}
    return " ".join(
        str(part)
        for part in (
            item.get("place_query"),
            item.get("summary"),
            item.get("reason"),
            values.get("notes"),
            values.get("text"),
        )
        if part
    )


def _mentions_unresolved_link(item: dict[str, Any]) -> bool:
    """A pasted link (booking site, Park4Night, ...) needs the model to fetch
    it via url_context before place_query can be produced. A Google Maps link
    doesn't - it's resolved deterministically from its own URL structure
    without ever needing a fetch, so it never triggers this."""
    for raw_url in _URL_RE.findall(_basket_item_text(item)):
        url = raw_url.rstrip(".,;:")
        try:
            host = (urlparse(url).hostname or "").casefold()
        except ValueError:
            continue
        if host and not _google_maps_host(host):
            return True
    return False


def _needs_research(basket: list[dict[str, Any]]) -> bool:
    return any(
        item.get("action") == "plan"
        or (
            item.get("entity_type") == "stop"
            and item.get("action") in {"add", "update"}
            and not item.get("place_query")
        )
        or _mentions_unresolved_link(item)
        for item in basket
    )


def _known_ids(context: dict[str, Any]) -> tuple[set[str], dict[str, set[str]], set[str]]:
    catalog = context.get("id_catalog") if isinstance(context.get("id_catalog"), dict) else {}
    if catalog:
        day_ids = {str(value) for value in catalog.get("day_ids", []) if value}
        stop_ids = {
            str(day_id): {str(value) for value in values if value}
            for day_id, values in (catalog.get("stop_ids_by_day") or {}).items()
            if isinstance(values, list)
        }
        preference_ids = {
            str(value) for value in catalog.get("preference_ids", []) if value
        }
        return day_ids, stop_ids, preference_ids

    day_ids: set[str] = set()
    stop_ids: dict[str, set[str]] = {}
    preference_ids: set[str] = set()
    trip_details = context.get("trip", {}).get("details")
    if isinstance(trip_details, dict):
        preferences = trip_details.get("planning_preferences")
        if isinstance(preferences, list):
            preference_ids.update(
                str(item.get("id"))
                for item in preferences
                if isinstance(item, dict) and item.get("id")
            )
    for day in context.get("days", []):
        day_id = str(day.get("id") or "")
        if not day_id:
            continue
        day_ids.add(day_id)
        stop_ids[day_id] = {
            str(stop.get("id"))
            for stop in canonical_roadbook_stops(day)
            if isinstance(stop, dict) and stop.get("id")
        }
        details = day.get("details")
        if isinstance(details, dict):
            preferences = details.get("planning_preferences")
            if isinstance(preferences, list):
                preference_ids.update(
                    str(item.get("id"))
                    for item in preferences
                    if isinstance(item, dict) and item.get("id")
                )
    return day_ids, stop_ids, preference_ids

def seed_position_state(days: Any) -> dict[str, list[dict[str, str]]]:
    """Seed the stop-position bookkeeping from the FULL trip day list.

    Position bookkeeping must cover every day an operation may legally
    reference. The compile context's ``days`` list is only a bounded detail
    window (basket-target days plus neighbours), while ID validation accepts
    any day of the trip - seeding from the window therefore left every
    out-of-window day looking EMPTY, so an add on such a day was forced to
    position 1 (in front of the day's real stops) and a requested move
    position was silently clamped to 1. Callers must pass the full stored
    day list (``payload["days"]["days"]``), never the context window.
    """
    state: dict[str, list[dict[str, str]]] = {}
    if not isinstance(days, list):
        return state
    for day in days:
        if not isinstance(day, dict):
            continue
        day_id = _clean_text(day.get("id"), maximum=200)
        if not day_id:
            continue
        state[day_id] = [
            {
                "id": _clean_text(stop.get("id"), maximum=200),
                "type": _clean_text(stop.get("type"), maximum=100).casefold(),
            }
            for stop in canonical_roadbook_stops(day)
            if isinstance(stop, dict)
        ]
    return state

def _context_day_sequence(context: dict[str, Any]) -> list[dict[str, str]]:
    """Return the bounded trip day sequence used for deterministic day inference."""
    sequence: list[dict[str, str]] = []
    for raw in context.get("trip_index", []):
        if not isinstance(raw, dict):
            continue
        day_id = _clean_text(raw.get("id"), maximum=200)
        if not day_id:
            continue
        sequence.append(
            {
                "id": day_id,
                "date": _clean_text(raw.get("date"), maximum=20),
            }
        )
    if sequence:
        return sequence
    for raw in context.get("days", []):
        if not isinstance(raw, dict):
            continue
        day_id = _clean_text(raw.get("id"), maximum=200)
        if day_id:
            sequence.append(
                {
                    "id": day_id,
                    "date": _clean_text(raw.get("date"), maximum=20),
                }
            )
    return sequence

def _relative_day_id(
    context: dict[str, Any],
    offset: int,
) -> str:
    sequence = _context_day_sequence(context)
    if not sequence:
        return ""
    scope = context.get("scope") if isinstance(context.get("scope"), dict) else {}
    current_id = _clean_text(scope.get("current_day_id"), maximum=200)
    current_index: int | None = None
    if current_id:
        current_index = next(
            (index for index, item in enumerate(sequence) if item["id"] == current_id),
            None,
        )
    if current_index is None:
        local_date = _clean_text(context.get("local_date"), maximum=20)
        current_index = next(
            (index for index, item in enumerate(sequence) if item["date"] == local_date),
            None,
        )
    if current_index is None:
        return ""
    target = current_index + offset
    if target < 0 or target >= len(sequence):
        return ""
    return sequence[target]["id"]

def _day_id_for_date(context: dict[str, Any], requested: str) -> str:
    requested = _clean_text(requested, maximum=20)
    if not requested:
        return ""
    for item in _context_day_sequence(context):
        if item["date"] == requested:
            return item["id"]
    return ""

def _operation_context_text(
    raw: dict[str, Any],
    *,
    basket: list[dict[str, Any]] | None,
    strict: bool = False,
) -> str:
    """Build a bounded semantic text used for parent-day/overnight inference.

    When no basket item can be matched to this operation by place_query or
    name, and the basket happens to hold exactly one stop item, that lone
    item's text is used as a last-resort hint (`strict=False`) - reasonable
    for inferring which *day* an operation belongs to, where a wrong guess is
    low-stakes and reviewable. It must NOT feed decisions that silently
    rewrite an unrelated, already-existing stop (`strict=True`): with two
    basket-unrelated compiled operations and a lone differently-themed basket
    item, the lone-item fallback could otherwise attribute e.g. "gestern
    Nacht hier übernachtet" text to a completely different new stop, making
    that stop's `add` silently become an `update` of the existing overnight
    entry and overwrite its name/notes.
    """
    fragments: list[str] = []
    changes = raw.get("changes") if isinstance(raw.get("changes"), dict) else {}
    for value in (
        raw.get("reason"),
        raw.get("place_query"),
        changes.get("name"),
        changes.get("notes"),
        changes.get("type"),
    ):
        cleaned = _clean_text(value, maximum=2_000)
        if cleaned:
            fragments.append(cleaned)

    stop_items = [
        item
        for item in (basket or [])
        if isinstance(item, dict)
        and str(item.get("entity_type") or "").casefold() == "stop"
    ]
    raw_query = _clean_text(raw.get("place_query"), maximum=500).casefold()
    raw_name = _clean_text(changes.get("name"), maximum=500).casefold()
    matching: list[dict[str, Any]] = []
    if raw_query:
        matching = [
            item
            for item in stop_items
            if _clean_text(item.get("place_query"), maximum=500).casefold() == raw_query
        ]
    if not matching and raw_name:
        matching = [
            item
            for item in stop_items
            if raw_name
            in " ".join(
                (
                    _clean_text(item.get("summary"), maximum=500),
                    _clean_text(
                        (item.get("values") or {}).get("name")
                        if isinstance(item.get("values"), dict)
                        else "",
                        maximum=500,
                    ),
                )
            ).casefold()
        ]
    if not matching and not strict and len(stop_items) == 1:
        matching = stop_items

    for item in matching[:3]:
        values = item.get("values") if isinstance(item.get("values"), dict) else {}
        for value in (
            item.get("summary"),
            item.get("reason"),
            item.get("place_query"),
            values.get("name"),
            values.get("notes"),
            values.get("type"),
        ):
            cleaned = _clean_text(value, maximum=2_000)
            if cleaned:
                fragments.append(cleaned)
    return " ".join(fragments).casefold()[:8_000]

def _basket_parent_day(
    raw: dict[str, Any],
    *,
    context: dict[str, Any],
    basket: list[dict[str, Any]] | None,
) -> str:
    """Resolve an explicit parent day stored in the matching draft intent."""
    stop_items = [
        item
        for item in (basket or [])
        if isinstance(item, dict)
        and str(item.get("entity_type") or "").casefold() == "stop"
    ]
    changes = raw.get("changes") if isinstance(raw.get("changes"), dict) else {}
    raw_query = _clean_text(raw.get("place_query"), maximum=500).casefold()
    raw_name = _clean_text(changes.get("name"), maximum=500).casefold()
    matching: list[dict[str, Any]] = []
    if raw_query:
        matching = [
            item
            for item in stop_items
            if _clean_text(item.get("place_query"), maximum=500).casefold() == raw_query
        ]
    if not matching and raw_name:
        matching = [
            item
            for item in stop_items
            if raw_name
            in " ".join(
                (
                    _clean_text(item.get("summary"), maximum=500),
                    _clean_text(
                        (item.get("values") or {}).get("name")
                        if isinstance(item.get("values"), dict)
                        else "",
                        maximum=500,
                    ),
                )
            ).casefold()
        ]
    if not matching and len(stop_items) == 1:
        matching = stop_items
    if len(matching) != 1:
        return ""
    item = matching[0]
    day_id = _clean_text(item.get("day_id"), maximum=200)
    if day_id:
        return day_id
    return _day_id_for_date(
        context,
        _clean_text(item.get("day_date"), maximum=20),
    )

def _infer_missing_stop_day_id(
    raw: dict[str, Any],
    *,
    context: dict[str, Any],
    basket: list[dict[str, Any]] | None,
    entity_id: str | None,
    stop_ids: dict[str, set[str]],
) -> str:
    """Infer a missing stop parent conservatively from authoritative context."""
    # Existing stop IDs already uniquely identify their canonical parent day.
    if entity_id:
        owners = [day_id for day_id, ids in stop_ids.items() if entity_id in ids]
        if len(owners) == 1:
            return owners[0]

    explicit = _basket_parent_day(
        raw,
        context=context,
        basket=basket,
    )
    if explicit:
        return explicit

    text = _operation_context_text(raw, basket=basket)
    for match in _ISO_DATE_IN_TEXT.findall(text):
        day_id = _day_id_for_date(context, match)
        if day_id:
            return day_id
    for day_value, month_value, year_value in _GERMAN_DATE_IN_TEXT.findall(text):
        try:
            normalized = f"{int(year_value):04d}-{int(month_value):02d}-{int(day_value):02d}"
        except ValueError:
            continue
        day_id = _day_id_for_date(context, normalized)
        if day_id:
            return day_id

    changes = raw.get("changes") if isinstance(raw.get("changes"), dict) else {}
    stop_type = _clean_text(changes.get("type"), maximum=100).casefold()
    has_past_overnight_marker = any(
        marker in text for marker in _PAST_OVERNIGHT_MARKERS
    )
    has_current_overnight_marker = any(
        marker in text for marker in _CURRENT_OVERNIGHT_MARKERS
    )
    is_overnight = (
        stop_type in OVERNIGHT_STOP_TYPES
        or has_past_overnight_marker
        or has_current_overnight_marker
    )
    if is_overnight and has_past_overnight_marker:
        previous_id = _relative_day_id(context, -1)
        if previous_id:
            return previous_id
    if is_overnight and has_current_overnight_marker:
        current_id = _relative_day_id(context, 0)
        if current_id:
            return current_id
    if any(marker in text for marker in _PREVIOUS_DAY_MARKERS):
        previous_id = _relative_day_id(context, -1)
        if previous_id:
            return previous_id
    if any(marker in text for marker in _NEXT_DAY_MARKERS):
        next_id = _relative_day_id(context, 1)
        if next_id:
            return next_id
    if any(marker in text for marker in _CURRENT_DAY_MARKERS):
        current_id = _relative_day_id(context, 0)
        if current_id:
            return current_id

    detailed_ids = [
        _clean_text(day.get("id"), maximum=200)
        for day in context.get("days", [])
        if isinstance(day, dict) and _clean_text(day.get("id"), maximum=200)
    ]
    if len(set(detailed_ids)) == 1:
        return detailed_ids[0]
    return ""

def _day_detail(
    context: dict[str, Any],
    day_id: str,
    *,
    full_days: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    for day in context.get("days", []):
        if isinstance(day, dict) and str(day.get("id") or "") == day_id:
            return day
    # context["days"] is only the bounded compile detail window, but day_id
    # can legally point at ANY trip day (trip_index covers the whole trip).
    # Without this fallback the past-overnight dedup saw no overnight stop on
    # an out-of-window day and ADDED a duplicate instead of updating.
    for day in full_days or []:
        if isinstance(day, dict) and str(day.get("id") or "") == day_id:
            return day
    return None

def _is_actual_past_overnight(
    raw: dict[str, Any],
    *,
    basket: list[dict[str, Any]] | None,
) -> bool:
    """Whether this operation genuinely describes last night's overnight.

    Deciding "yes" silently converts a new stop `add` into an `update` of an
    existing overnight entry (see the call site), so this must never rely on
    the lone-basket-item fallback - only an operation whose own text, or a
    basket item actually matched to it by place_query/name, mentions a past
    overnight marker qualifies.
    """
    changes = raw.get("changes") if isinstance(raw.get("changes"), dict) else {}
    stop_type = _clean_text(changes.get("type"), maximum=100).casefold()
    text = _operation_context_text(raw, basket=basket, strict=True)
    has_past_marker = any(marker in text for marker in _PAST_OVERNIGHT_MARKERS)
    return has_past_marker and (
        stop_type in OVERNIGHT_STOP_TYPES or "übernacht" in text or "geschlafen" in text
    )

def _sanitize_operation(
    raw: dict[str, Any],
    *,
    index: int,
    context: dict[str, Any],
    new_day_refs: set[str],
    basket: list[dict[str, Any]] | None = None,
    position_state: dict[str, list[dict[str, str]]] | None = None,
    batch_refs: dict[str, Any] | None = None,
    full_days: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationError(f"Assistenten-Operation {index + 1} ist kein Objekt")

    # Keep the current Home Assistant trip authoritative. A model-generated
    # trip_id is tolerated only as redundant compatibility metadata and is
    # checked before being discarded. All other envelope fields are always
    # discarded because Home Assistant creates them after validation.
    raw = _normalize_compiled_operation_aliases(
        raw,
        index=index,
    )
    supplied_trip_id = _clean_text(raw.pop("trip_id", None), maximum=200)
    trip = context.get("trip") if isinstance(context.get("trip"), dict) else {}
    canonical_trip_id = str(
        trip.get("id") or context.get("selected_trip_id") or ""
    )
    if supplied_trip_id and canonical_trip_id and supplied_trip_id != canonical_trip_id:
        raise ValidationError(
            "Die vom Assistenten gelieferte Reise-ID stimmt nicht mit der "
            f"aktiven Reise überein: {supplied_trip_id} != {canonical_trip_id}"
        )
    for field_name in _SERVER_CONTROLLED_OPERATION_FIELDS - {"trip_id"}:
        raw.pop(field_name, None)

    unknown = set(raw) - _ALLOWED_OPERATION_FIELDS
    if unknown:
        raise ValidationError(
            "Nicht erlaubte Felder in Assistenten-Operation: "
            + ", ".join(sorted(unknown))
        )
    action = str(raw.get("action") or "").casefold()
    entity_type = str(raw.get("entity_type") or "").casefold()
    if action not in _ALLOWED_ACTIONS:
        raise ValidationError(f"Nicht unterstützte Assistentenaktion: {action}")
    if entity_type not in _ALLOWED_ENTITY_TYPES:
        raise ValidationError(f"Nicht unterstützter Assistententyp: {entity_type}")
    try:
        changes, changes_mode = normalize_changes_mapping(
            raw.get("changes"),
            allow_scalar_empty=action in {"remove", "move"},
        )
    except StructuredOutputError as err:
        raise ValidationError(
            f"changes in Assistenten-Operation {index + 1} konnte nicht "
            "sicher als JSON-Objekt gelesen werden"
        ) from err
    if changes_mode != "object":
        _LOGGER.debug(
            "Normalized assistant operation %s changes during validation (%s)",
            index + 1,
            changes_mode,
        )
    unknown_changes = set(changes) - _ALLOWED_CHANGE_FIELDS
    if unknown_changes:
        raise ValidationError(
            "Nicht erlaubte Änderungen im Assistentenvorschlag: "
            + ", ".join(sorted(unknown_changes))
        )
    wrong_entity_changes = set(changes) - _CHANGE_FIELDS_BY_ENTITY[entity_type]
    if wrong_entity_changes:
        raise ValidationError(
            f"Nicht erlaubte Felder für {entity_type}: "
            + ", ".join(sorted(wrong_entity_changes))
        )
    operation: dict[str, Any] = {
        "operation_id": _clean_text(raw.get("operation_id"), maximum=200)
        or f"op-{index + 1}-{uuid4().hex[:10]}",
        "action": action,
        "entity_type": entity_type,
        "changes": _deep_without_none(deepcopy(changes)),
        "reason": _clean_text(raw.get("reason"), maximum=1_000)
        or "Im Roadplanner-Gespräch vom Benutzer vorgemerkt.",
    }
    for field in ("entity_id", "day_id", "day_ref", "place_query"):
        value = _clean_text(raw.get(field), maximum=500)
        if value:
            operation[field] = value
    position = raw.get("position")
    # MIME-only JSON fallbacks emit numbers as strings ("2") or floats (2.0).
    # Silently dropping them misplaced new stops (the requested position
    # vanished) and hard-rejected position-only reorders further down.
    if isinstance(position, str) and position.strip().isdigit():
        position = int(position.strip())
    elif (
        isinstance(position, float)
        and not isinstance(position, bool)
        and position.is_integer()
    ):
        position = int(position)
    if isinstance(position, int) and not isinstance(position, bool) and position > 0:
        operation["position"] = position

    day_ids, stop_ids, preference_ids = _known_ids(context)
    entity_id = operation.get("entity_id")
    day_id = operation.get("day_id")
    day_ref = operation.get("day_ref")

    if entity_type == "trip":
        if action != "update":
            raise ValidationError("Reiseoperationen unterstützen nur update")
        if not operation["changes"]:
            raise ValidationError("Eine Reiseaktualisierung benötigt Änderungen")
        operation.pop("entity_id", None)
        operation.pop("day_id", None)
        operation.pop("day_ref", None)
        operation.pop("position", None)
        operation.pop("place_query", None)
    elif entity_type == "day":
        operation.pop("day_id", None)
        operation.pop("day_ref", None)
        operation.pop("place_query", None)
        if action == "add":
            if not entity_id:
                operation["entity_id"] = f"new-day-{uuid4().hex[:12]}"
            new_day_refs.add(operation["entity_id"])
            if not operation["changes"]:
                raise ValidationError("Ein neuer Reisetag benötigt Tagesdaten")
        elif not entity_id or entity_id not in day_ids:
            raise ValidationError(
                f"Bestehende Tages-ID ist nicht im aktuellen Roadbook vorhanden: {entity_id or 'fehlt'}"
            )
        if action == "remove" and entity_id and batch_refs is not None:
            batch_refs.setdefault("removed_days", set()).add(entity_id)
        if action == "remove" and entity_id:
            # Removing a day that still carries stops is exactly what the
            # user asked for - the executor's remove_stops gate protects
            # scripted callers, not a reviewed draft, and killing the whole
            # draft over the missing flag was a live failure
            # ("Der Reisetag enthält Stopps. Zum Löschen muss
            # remove_stops=true gesetzt sein."). Set it explicitly whenever
            # the day (still) has stops; the draft stays destructive and
            # requires the loud confirmation at apply time as before.
            removed_in_batch = (
                batch_refs.get("removed_stops", set()) if batch_refs else set()
            )
            day_stop_ids = stop_ids.get(str(entity_id))
            if day_stop_ids is None or (set(day_stop_ids) - set(removed_in_batch)):
                operation["changes"]["remove_stops"] = True
            else:
                operation["changes"].pop("remove_stops", None)
        day_changes_details = operation["changes"].get("details")
        if isinstance(day_changes_details, dict) and "overnight_plan" in day_changes_details:
            # The model hands over overnight CANDIDATES (Plan B/C blocks),
            # never the stored structure: details merges only one level deep
            # at apply time, so a raw model plan would replace the day's
            # existing options wholesale. Merge server-side against the
            # stored day instead - existing options survive, new ones are
            # validated leniently, deduped and capped.
            existing_day = None
            if action != "add" and entity_id:
                existing_day = _day_detail(context, entity_id, full_days=full_days)
            incoming_plan = day_changes_details.get("overnight_plan")
            merged_plan = merge_assistant_overnight_plan(
                existing_day,
                incoming_plan,
                now=_utc_now_iso(),
            )
            existing_option_count = len(
                get_overnight_plan(existing_day or {}).get("options") or []
            )
            if len(merged_plan.get("options") or []) <= existing_option_count:
                # The model keeps emitting overnight_plan with an EMPTY
                # options list although the user's request names the
                # candidate ("nimm als Alternative den auf <Link>") - the
                # chat then claimed success while nothing was added (live
                # report, three times). Server-side salvage: pull the
                # candidate links out of the raw plan, the operation reason
                # and - as last resort - basket decisions that talk about
                # an alternative. Every salvaged option stays a reviewable
                # backup with the link as its identity.
                salvaged = _overnight_candidates_from_text(
                    json.dumps(incoming_plan, ensure_ascii=False, default=str)
                    + " "
                    + str(operation.get("reason") or "")
                )
                if not salvaged and basket:
                    salvaged = _overnight_candidates_from_text(
                        " ".join(
                            str(item.get("text") or item.get("title") or "")
                            for item in basket
                            if isinstance(item, dict)
                            and _ALTERNATIVE_INTENT_RE.search(
                                str(item.get("text") or item.get("title") or "")
                            )
                        )
                    )
                if salvaged:
                    merged_plan = merge_assistant_overnight_plan(
                        existing_day,
                        {
                            "strategy": merged_plan.get("strategy"),
                            "options": salvaged,
                        },
                        now=_utc_now_iso(),
                    )
                    # The merge deliberately drops MODEL-provided GPS. The
                    # salvaged coordinates were extracted server-side from
                    # the recorded decision text (typically the user's own
                    # numbers), so they are re-attached to the matching
                    # option - reviewable in the Änderungsübersicht like
                    # every other value.
                    for candidate in salvaged:
                        location = candidate.get("location")
                        if not isinstance(location, dict):
                            continue
                        for option in merged_plan.get("options") or []:
                            same_url = candidate.get("url") and (
                                (option.get("source") or {}).get("url")
                                == candidate.get("url")
                            )
                            same_name = candidate.get("name") and (
                                option.get("name") == candidate.get("name")
                            )
                            if (same_url or same_name) and not (
                                option.get("location") or {}
                            ).get("latitude"):
                                option["location"] = dict(location)
                                option.setdefault(
                                    "place_query",
                                    str(candidate.get("place_query") or ""),
                                )
                                break
            day_changes_details["overnight_plan"] = merged_plan
    elif entity_type == "stop":
        day_id, day_ref = _normalize_compiled_day_reference(
            operation,
            day_ids=day_ids,
            new_day_refs=new_day_refs,
        )
        if day_id and day_ref:
            raise ValidationError("Eine Stoppoperation darf nicht day_id und day_ref mischen")
        if not day_id and not day_ref:
            inferred_day_id = _infer_missing_stop_day_id(
                raw,
                context=context,
                basket=basket,
                entity_id=entity_id,
                stop_ids=stop_ids,
            )
            if inferred_day_id:
                operation["day_id"] = inferred_day_id
                day_id = inferred_day_id
            else:
                raise ValidationError(
                    "Eine Stoppoperation benötigt day_id oder day_ref. "
                    "Der betroffene Reisetag konnte aus Datum, Gespräch und "
                    "aktuellem Roadbook nicht sicher abgeleitet werden."
                )
        if day_id and day_id not in day_ids:
            raise ValidationError(f"Tages-ID der Stoppoperation ist unbekannt: {day_id}")
        if day_ref and day_ref not in new_day_refs:
            raise ValidationError(f"day_ref verweist nicht auf einen neuen Tag: {day_ref}")
        if (
            batch_refs is not None
            and day_id
            and day_id in batch_refs.get("removed_days", set())
        ):
            raise ValidationError(
                "Der Reisetag wurde im selben Entwurf entfernt und kann nicht "
                f"mehr referenziert werden: {day_id}"
            )

        # "Wir haben hier geschlafen" describes the actual overnight point
        # that ended the previous travel day and starts the current one. If
        # that day already has exactly one canonical overnight stop, update
        # it instead of creating a duplicate.
        if (
            action == "add"
            and day_id
            and _is_actual_past_overnight(raw, basket=basket)
        ):
            day_detail = _day_detail(context, day_id, full_days=full_days)
            overnight_stops = [
                stop
                for stop in canonical_roadbook_stops(day_detail or {})
                if isinstance(stop, dict)
                and str(stop.get("type") or "").casefold() in OVERNIGHT_STOP_TYPES
                and stop.get("id")
            ]
            if len(overnight_stops) == 1:
                action = "update"
                operation["action"] = "update"
                entity_id = str(overnight_stops[0]["id"])
                operation["entity_id"] = entity_id
                operation.pop("position", None)

        if isinstance(operation.get("changes"), dict):
            _strip_park4night_from_name(operation["changes"])

        if action == "add":
            if not entity_id:
                operation["entity_id"] = f"new-stop-{uuid4().hex[:12]}"
            if batch_refs is not None:
                batch_refs.setdefault("added_stops", {})[
                    str(operation["entity_id"])
                ] = str(day_id or day_ref or "")
            if not _clean_text(operation["changes"].get("name"), maximum=500):
                raise ValidationError("Ein neuer Stopp benötigt einen Namen")
            stop_type = _clean_text(
                operation["changes"].get("type") or "waypoint",
                maximum=100,
            ).casefold()
            if stop_type not in _ALLOWED_STOP_TYPES:
                raise ValidationError(f"Nicht unterstützter Stopptyp: {stop_type}")
            operation["changes"]["type"] = stop_type
            if not operation.get("place_query"):
                raise ValidationError(
                    "Ein neuer konkreter Stopp benötigt place_query für die GPS-Prüfung"
                )
            if position_state is not None:
                parent_ref = str(day_id or day_ref or "")
                state = position_state.setdefault(parent_ref, [])
                requested = operation.get("position")
                if (
                    isinstance(requested, int)
                    and not isinstance(requested, bool)
                    and requested > 0
                ):
                    insert_at = min(requested - 1, len(state))
                elif stop_type in OVERNIGHT_STOP_TYPES:
                    insert_at = len(state)
                else:
                    overnight_index = next(
                        (
                            state_index
                            for state_index, item in enumerate(state)
                            if str(item.get("type") or "").casefold() in OVERNIGHT_STOP_TYPES
                        ),
                        len(state),
                    )
                    insert_at = overnight_index
                operation["position"] = insert_at + 1
                state.insert(
                    insert_at,
                    {
                        "id": str(operation.get("entity_id") or ""),
                        "type": stop_type,
                    },
                )
        else:
            added_stops = batch_refs.get("added_stops", {}) if batch_refs else {}
            removed_stops = batch_refs.get("removed_stops", set()) if batch_refs else set()
            if not entity_id:
                raise ValidationError(
                    "Bestehende Stopp-ID ist nicht im aktuellen Reisetag vorhanden: fehlt"
                )
            if entity_id in removed_stops:
                raise ValidationError(
                    "Der Stopp wurde im selben Entwurf bereits entfernt und "
                    f"kann nicht mehr geändert werden: {entity_id}"
                )
            if entity_id in added_stops:
                # Reference to a stop ADDED earlier in the same draft (add
                # then move/update/remove is a natural compile shape and the
                # canonical ChangeSet executor resolves just-added stops).
                # Previously this hard-rejected the whole draft as an
                # unknown stop ID. Align the day reference to the add's
                # parent - the add is authoritative about where the stop is.
                parent = added_stops[entity_id]
                if parent in new_day_refs:
                    operation.pop("day_id", None)
                    operation["day_ref"] = parent
                    day_id, day_ref = "", parent
                elif parent:
                    operation.pop("day_ref", None)
                    operation["day_id"] = parent
                    day_id, day_ref = parent, ""
                if action == "remove" and batch_refs is not None:
                    batch_refs["added_stops"].pop(entity_id, None)
                    batch_refs.setdefault("removed_stops", set()).add(entity_id)
            elif not day_id:
                raise ValidationError("Bestehende Stopps müssen über day_id referenziert werden")
            elif entity_id not in stop_ids.get(day_id, set()):
                # The stop may have moved to another day since the draft was
                # written (a handoff applied in between), or the model guessed
                # the wrong day for a stop it referenced correctly. Stop IDs
                # are globally unique, so exactly one match elsewhere
                # identifies the real day deterministically - same principle
                # as _find_stop's stale-day-ID fallback in the panel. Only a
                # stop found NOWHERE stays an error (deleted or invented).
                matching_days = [
                    known_day_id
                    for known_day_id, known_stops in stop_ids.items()
                    if entity_id in known_stops
                ]
                if len(matching_days) == 1:
                    _LOGGER.debug(
                        "Corrected stale day reference for stop %s: %s -> %s",
                        entity_id,
                        day_id,
                        matching_days[0],
                    )
                    day_id = matching_days[0]
                    operation["day_id"] = day_id
                else:
                    raise ValidationError(
                        f"Bestehende Stopp-ID ist nicht im aktuellen Reisetag vorhanden: {entity_id}"
                    )
            if (
                action == "remove"
                and batch_refs is not None
                and entity_id not in added_stops
            ):
                batch_refs.setdefault("removed_stops", set()).add(entity_id)
            if "type" in operation["changes"]:
                stop_type = _clean_text(
                    operation["changes"].get("type"),
                    maximum=100,
                ).casefold()
                if stop_type not in _ALLOWED_STOP_TYPES:
                    raise ValidationError(f"Nicht unterstützter Stopptyp: {stop_type}")
                operation["changes"]["type"] = stop_type
            parent_ref = str(day_id or day_ref or "")
            if position_state is not None and parent_ref:
                state = position_state.setdefault(parent_ref, [])
                current_index = next(
                    (
                        state_index
                        for state_index, item in enumerate(state)
                        if item.get("id") == str(entity_id or "")
                    ),
                    None,
                )
                if action == "remove":
                    if current_index is not None:
                        state.pop(current_index)
                else:
                    item = (
                        state.pop(current_index)
                        if current_index is not None
                        else {"id": str(entity_id or ""), "type": "waypoint"}
                    )
                    if "type" in operation["changes"]:
                        item["type"] = str(operation["changes"]["type"])
                    requested = operation.get("position")
                    if (
                        isinstance(requested, int)
                        and not isinstance(requested, bool)
                        and requested > 0
                    ):
                        insert_at = min(requested - 1, len(state))
                    elif current_index is not None:
                        insert_at = min(current_index, len(state))
                    else:
                        insert_at = len(state)
                    state.insert(insert_at, item)
                    if action == "move" or "position" in operation:
                        operation["position"] = insert_at + 1
    else:  # preference
        operation.pop("position", None)
        operation.pop("place_query", None)
        day_id, day_ref = _normalize_compiled_day_reference(
            operation,
            day_ids=day_ids,
            new_day_refs=new_day_refs,
        )
        if day_id and day_ref:
            raise ValidationError("Eine Präferenz darf nicht day_id und day_ref mischen")
        if day_id and day_id not in day_ids:
            raise ValidationError(f"Tages-ID der Präferenz ist unbekannt: {day_id}")
        if day_ref and day_ref not in new_day_refs:
            raise ValidationError(f"day_ref der Präferenz ist unbekannt: {day_ref}")
        if action == "move":
            raise ValidationError("Präferenzen unterstützen move nicht")
        if action == "add":
            if not entity_id:
                operation["entity_id"] = f"pref-{uuid4().hex[:12]}"
            if not _clean_text(operation["changes"].get("text"), maximum=2_000):
                raise ValidationError("Eine neue Präferenz benötigt text")
        elif not entity_id or entity_id not in preference_ids:
            raise ValidationError(
                f"Bestehende Präferenz-ID ist nicht im aktuellen Roadbook vorhanden: {entity_id or 'fehlt'}"
            )

    if (
        action == "update"
        and not operation["changes"]
        and "position" not in operation
        and not operation.get("place_query")
    ):
        # An update that carries nothing is a no-op, not a broken draft.
        # Gemini regularly echoes an entity it decided NOT to touch (all
        # fields null, an empty changes object) next to perfectly good
        # operations. Rejecting the whole batch threw those away too - live
        # report "Eine Aktualisierung benötigt Änderungen, eine Position oder
        # place_query". The caller drops this single operation instead and
        # records it as an assumption, so the omission stays visible.
        raise NoOperationChange(
            entity_type=entity_type,
            entity_id=str(entity_id or ""),
            reason=operation["reason"],
        )
    if action in {"remove", "move"} and operation["changes"]:
        # Gemini routinely echoes identifying fields (name, type, ...) into
        # changes on delete/move operations. The echo carries no user data -
        # the referenced entity is identified by its ID - so drop it instead
        # of rejecting the whole multi-operation draft over it. The one
        # legitimate remove-payload survives: remove_stops on a day removal
        # (the executor requires it for a day that still carries stops).
        preserved: dict[str, Any] = {}
        if (
            action == "remove"
            and entity_type == "day"
            and operation["changes"].get("remove_stops") is True
        ):
            preserved["remove_stops"] = True
        dropped = sorted(set(operation["changes"]) - set(preserved))
        if dropped:
            _LOGGER.debug(
                "Dropped echoed changes on %s operation %s: %s",
                action,
                index + 1,
                dropped,
            )
        operation["changes"] = preserved
    return operation
