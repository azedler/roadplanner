"""Hybrid local-first and Gemini vision media curation for Roadplanner.

Roadplanner always performs deterministic local filtering first.  This module
only validates and applies a semantic selection returned by a multimodal
provider.  It never deletes provider files, never identifies people, and never
allows the model to invent media IDs.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable

from .roadplanner import ValidationError

VISION_SELECTION_VERSION = 1
VISION_MAX_CANDIDATES = 15
VISION_MAX_HIGHLIGHTS = 8

VISION_SELECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cover_image_id": {"type": "string"},
        "highlight_image_ids": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": VISION_MAX_HIGHLIGHTS,
        },
        "selections": {
            "type": "array",
            "maxItems": VISION_MAX_CANDIDATES,
            "items": {
                "type": "object",
                "properties": {
                    "image_id": {"type": "string"},
                    "role": {
                        "type": "string",
                        "enum": ["cover", "highlight", "reject"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["image_id", "role", "reason"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["cover_image_id", "highlight_image_ids", "selections", "summary"],
}


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _text(value: Any, maximum: int = 2_000) -> str:
    return str(value or "").strip()[:maximum]


def _candidate_identity(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _text(candidate.get("id"), 300),
        "provider_item_id": _text(candidate.get("provider_item_id"), 500),
        "source_url": _text(candidate.get("source_url"), 2_000),
        "file_hash": _text(candidate.get("file_hash"), 500),
        "taken_at": _text(candidate.get("taken_at"), 100),
        "width": candidate.get("width"),
        "height": candidate.get("height"),
        "selection_score": candidate.get("selection_score"),
    }


def selection_fingerprint(
    *,
    kind: str,
    context: dict[str, Any],
    candidates: Iterable[dict[str, Any]],
    model: str,
) -> str:
    """Return a stable fingerprint for one semantic selection request."""
    value = {
        "version": VISION_SELECTION_VERSION,
        "kind": _text(kind, 50),
        "model": _text(model, 200),
        "context": deepcopy(context),
        "candidates": [_candidate_identity(item) for item in candidates],
    }
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_vision_prompt(
    *,
    kind: str,
    context: dict[str, Any],
    candidate_ids: list[str],
    max_highlights: int,
) -> tuple[str, str]:
    """Return system and user prompts for semantic image curation."""
    stop_name = _text(context.get("stop_name"), 500) or "Reisestopp"
    place = _text(context.get("place"), 1_000)
    category = _text(context.get("category"), 200)
    description = _text(context.get("description"), 2_000)
    max_highlights = max(1, min(int(max_highlights), VISION_MAX_HIGHLIGHTS))
    allowed = ", ".join(candidate_ids)

    system = (
        "Du kuratierst Bilder für einen privaten Reiseplaner. Die Bilder wurden "
        "bereits lokal nach Zuordnung, Metadaten, Dubletten, Serien und grober "
        "technischer Eignung vorgefiltert. Wähle ausschließlich aus den gelieferten "
        "Bild-IDs. Identifiziere keine Personen und leite keine sensiblen Merkmale "
        "ab. Lösche nichts. Liefere nur die strukturierte Auswahl."
    )
    if kind == "planning":
        objective = (
            "Prüfe, welche Bilder den konkreten Ort tatsächlich und repräsentativ "
            "zeigen. Bevorzuge einen typischen Blick, hilfreiche Orientierung und "
            "abwechslungsreiche Motive. Vermeide Logos, Karten, Plakate, Schilder, "
            "Tickets und nahezu identische Ansichten."
        )
    else:
        objective = (
            "Wähle aus den eigenen Reisefotos ein repräsentatives Titelbild und "
            "abwechslungsreiche Erinnerungen. Bevorzuge unterschiedliche Momente, "
            "Landschaft, Atmosphäre, Aktivität und persönliche Reisemomente, ohne "
            "Personen zu identifizieren. Vermeide Serien-Dubletten und Screenshots."
        )
    prompt = (
        f"Stopp: {stop_name}\n"
        f"Ort: {place or 'nicht näher angegeben'}\n"
        f"Kategorie: {category or 'nicht angegeben'}\n"
        f"Beschreibung: {description or 'keine'}\n\n"
        f"{objective}\n\n"
        f"Wähle genau ein Titelbild und höchstens {max_highlights} Highlights. "
        "Das Titelbild muss auch in highlight_image_ids enthalten sein. "
        f"Zulässige Bild-IDs: {allowed}."
    )
    return system, prompt


def normalize_vision_selection(
    value: Any,
    *,
    allowed_ids: list[str],
    local_order: list[str],
    max_highlights: int,
    manual_cover_id: str | None = None,
) -> dict[str, Any]:
    """Validate one model selection and fill gaps from the local ranking.

    The model may only reorder known IDs.  Invalid IDs are discarded.  A manual
    cover always wins.  If the provider response is incomplete, the deterministic
    local order fills the remaining slots.
    """
    if not isinstance(value, dict):
        raise ValidationError("Vision-Auswahl muss ein JSON-Objekt sein")
    allowed = [item for item in allowed_ids if isinstance(item, str) and item]
    allowed_set = set(allowed)
    if not allowed:
        raise ValidationError("Vision-Auswahl enthält keine zulässigen Bild-IDs")
    limit = max(1, min(int(max_highlights), VISION_MAX_HIGHLIGHTS, len(allowed)))

    raw_highlights = value.get("highlight_image_ids")
    if not isinstance(raw_highlights, list):
        raw_highlights = []
    highlights: list[str] = []
    for raw in raw_highlights:
        image_id = _text(raw, 300)
        if image_id in allowed_set and image_id not in highlights:
            highlights.append(image_id)
        if len(highlights) >= limit:
            break

    cover = _text(manual_cover_id, 300) if manual_cover_id else ""
    if cover not in allowed_set:
        cover = _text(value.get("cover_image_id"), 300)
    if cover not in allowed_set:
        cover = ""

    if cover and cover in highlights:
        highlights.remove(cover)
    if cover:
        highlights.insert(0, cover)
    for image_id in local_order:
        if image_id in allowed_set and image_id not in highlights:
            highlights.append(image_id)
        if len(highlights) >= limit:
            break
    if not highlights:
        highlights = allowed[:limit]
    if not cover:
        cover = highlights[0]
    if cover not in highlights:
        highlights.insert(0, cover)
        highlights = highlights[:limit]

    reasons: dict[str, str] = {}
    rejected: list[str] = []
    raw_selections = value.get("selections")
    if isinstance(raw_selections, list):
        for raw in raw_selections[:VISION_MAX_CANDIDATES]:
            if not isinstance(raw, dict):
                continue
            image_id = _text(raw.get("image_id"), 300)
            if image_id not in allowed_set:
                continue
            role = _text(raw.get("role"), 50)
            reason = _text(raw.get("reason"), 1_000)
            if reason:
                reasons[image_id] = reason
            if role == "reject" and image_id not in rejected:
                rejected.append(image_id)

    return {
        "cover_id": cover,
        "highlight_ids": highlights[:limit],
        "rejected_ids": rejected,
        "reasons": reasons,
        "summary": _text(value.get("summary"), 2_000),
    }
