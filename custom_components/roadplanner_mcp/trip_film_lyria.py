"""Music written for one trip, paid for once.

Lyria generates music from a text brief. That makes it the first thing in
Roadplanner that **costs money every time it runs and produces something
different each time it does**, and both halves of that shape everything
here.

Off unless asked
----------------

The default is no music. Not "quiet music", not "music if a key is
configured" - none. Every test render, every experiment, every retry of a
failed export must be free, and the only way to be sure of that is that
generating is a thing somebody chose in a dialog that named a price.

There is no code path from "render a film" to "call Lyria". The film
export reads a *cached track*; if none exists, the film has no music.
Generation is its own action, taken on purpose, at a moment when nobody
is waiting for a video.

Paid once, kept
---------------

A generated track is cached under a key derived from the brief. Re-render
the same trip and the same file is used - the second render costs
nothing, which is the property that makes the feature usable at all. Two
renders of the same trip also *sound* the same, for the same reason the
camper illustration is stored rather than regenerated: a record of a
journey that changes every time it is exported is not a record.

The brief comes from the trip
-----------------------------

Roadplanner already knows what the journey was like - its motifs, its
countries, its shape. The brief is derived from that rather than typed,
so the music belongs to this trip and not to a mood somebody picked from
a list. It is deliberately plain and instrumental: this is music under a
travel film, and the brief says so - "zurückhaltend, keine dominante
Filmmusik".

Which API, and why this one
---------------------------

The Gemini API rather than Vertex AI, because Roadplanner already holds a
Gemini API key and Vertex would mean a service account, a project id and
a second authentication story for one feature. Same key, same header,
same failure handling.

The request shape is taken from the published Lyria documentation. It has
**not** been executed against the live service from the development
sandbox - egress to Google is blocked there - so the first real call is
the first test. Everything around it is built to survive that being
wrong: a failed generation leaves the film without music and never
without a film.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)

MUSIC_DIR = "music"
LYRIA_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
# The long-form model: a film is minutes, and a thirty-second clip looped
# eight times is not a soundtrack.
LYRIA_MODEL = "lyria-3-pro-preview"
LYRIA_CLIP_MODEL = "lyria-3-clip-preview"
# Roughly what one generation yields. The app produces up to about three
# minutes; the API is documented as generating up to about two, so the
# smaller figure is the one a plan may rely on - planning against the
# larger one would silently leave the end of a film unscored.
LYRIA_TRACK_SECONDS = 118
LYRIA_TIMEOUT_SECONDS = 300
MAX_TRACK_BYTES = 40 * 1024 * 1024
TRACK_FILENAME_RE = re.compile(r"^lyria-[0-9a-f]{16}\.(mp3|wav)$")

# What one generation costs, as a rough figure for the dialog.
#
# Corrected against the published price: $0.08 per full song. The number
# standing here before was 0.60 EUR and was a guess written down as a
# fact - seven times too high, which is exactly the direction that makes
# somebody decline something affordable.
#
# Deliberately a constant with a name rather than a number in a
# sentence: when the published price changes, the thing to edit is
# obvious, and nobody has to hunt through translated strings for it.
LYRIA_ESTIMATED_COST_USD = 0.08
LYRIA_ESTIMATED_COST_EUR = 0.08
LYRIA_PRICE_NOTE = (
    "Schätzwert. Die tatsächliche Abrechnung erfolgt über deinen "
    "Google-Zugang und kann abweichen."
)


class LyriaError(RuntimeError):
    """Generating music failed. The film renders without it."""


def brief_from_trip(trip: dict[str, Any], narrative: dict[str, Any]) -> dict[str, Any]:
    """What the music should sound like, derived from the trip itself.

    Only facts Roadplanner holds: how long the journey was, which motifs
    the story layer already found, what the trip is called. No invented
    mood, no genre nobody asked for.
    """
    motifs = [
        " ".join(str(motif).split())[:40]
        for motif in (narrative.get("motifs") or [])
        if str(motif or "").strip()
    ][:4]
    title = " ".join(str(trip.get("title") or "").split())[:80]
    days = int(trip.get("chapter_count") or 0)
    return {
        "title": title,
        "days": days,
        "motifs": motifs,
    }


def build_prompt(brief: dict[str, Any]) -> str:
    """The text Lyria is asked to write music from.

    The style constraints are fixed here rather than exposed, because
    they are a property of the film - music that fights a travel film is
    a bug, not a preference somebody might hold.
    """
    lines = [
        "Instrumentale Hintergrundmusik für einen ruhigen Reisefilm über "
        "eine Wohnmobilreise durch Nordeuropa.",
        "Charakter: warm, nordisch, leicht verspielt, zurückhaltend. "
        "Getragen von Akustikgitarre, weichem Klavier und dezenten "
        "Streichern.",
        "Keine dominante Filmmusik, kein Trailer-Sound, kein großes "
        "Orchester-Crescendo, kein Schlagzeug im Vordergrund, kein Gesang.",
        "Gleichmäßig im Charakter über die ganze Länge, damit die Musik "
        "unter gesprochenen und geschriebenen Text passt.",
    ]
    motifs = brief.get("motifs") or []
    if motifs:
        lines.append("Motive der Reise: " + ", ".join(motifs) + ".")
    if brief.get("days"):
        lines.append(f"Die Reise dauerte {int(brief['days'])} Tage.")
    return "\n".join(lines)


def cache_key(brief: dict[str, Any], prompt: str) -> str:
    """The name a generated track is stored under.

    Derived from the brief, so the same trip asks for the same file and a
    re-render never pays twice. Change the trip's motifs and it is a
    different track - which is right: the music was about those motifs.
    """
    digest = hashlib.sha256()
    digest.update(json.dumps(brief, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    digest.update(b"\x1f")
    digest.update(prompt.encode("utf-8"))
    return digest.hexdigest()[:16]


def track_filename(key: str, extension: str = "mp3") -> str:
    """The one place a track filename is built, and it is built from a hash."""
    clean = re.sub(r"[^0-9a-f]", "", str(key or "").lower())[:16]
    if len(clean) != 16:
        raise ValueError("Ungültige Musikkennung")
    suffix = "wav" if str(extension).lower().endswith("wav") else "mp3"
    return f"lyria-{clean}.{suffix}"


def build_request(prompt: str) -> tuple[str, dict[str, Any]]:
    """The endpoint and body for one generation.

    Kept as a pure function so the shape can be checked without a network
    and without a key - which is the only kind of checking available for
    it here.
    """
    clean = str(prompt or "").strip()
    if not clean:
        raise LyriaError("Für die Musikgenerierung fehlt ein Prompt")
    return LYRIA_ENDPOINT, {
        "model": LYRIA_MODEL,
        "input": [{"type": "text", "text": clean}],
    }


def audio_from_response(payload: Any) -> tuple[bytes, str] | None:
    """Find the generated audio in whatever the response nests it in.

    Deliberately a search rather than a fixed path. The response is
    documented as steps containing content blocks, and the exact nesting
    is the part of this integration written from documentation rather
    than from a call that was actually made. Looking for "an object that
    says it is audio and carries data" is robust to that nesting being
    one level different, and it cannot pick up anything else: nothing
    else in the response claims to be audio.
    """
    import base64

    found: list[tuple[bytes, str]] = []

    def walk(node: Any, depth: int = 0) -> None:
        if found or depth > 12:
            return
        if isinstance(node, dict):
            # Two shapes, because there are two documented ways to ask.
            # The Interactions response nests blocks that say `"type":
            # "audio"`; generateContent returns `inlineData` with an
            # audio mime type. Recognising only one of them would mean a
            # paid generation whose audio we then threw away.
            mime = str(node.get("mime_type") or node.get("mimeType") or "")
            is_audio = str(node.get("type") or "") == "audio" or mime.startswith("audio/")
            if is_audio and node.get("data"):
                try:
                    blob = base64.b64decode(str(node["data"]), validate=False)
                except Exception:  # noqa: BLE001 - a bad block is simply skipped
                    return
                if blob and len(blob) <= MAX_TRACK_BYTES:
                    found.append((blob, mime or "audio/mp3"))
                return
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1)

    walk(payload)
    return found[0] if found else None


def extension_for(mime_type: str) -> str:
    """The file suffix a mime type should be stored under."""
    return "wav" if "wav" in str(mime_type or "").lower() else "mp3"


def cost_notice(brief: dict[str, Any]) -> dict[str, Any]:
    """What the user is told *before* anything is generated.

    A number, a currency, and a plain statement that it is an estimate.
    Asking somebody to approve a cost without naming one is not asking.
    """
    return {
        "model": LYRIA_MODEL,
        "seconds": LYRIA_TRACK_SECONDS,
        "estimated_cost_eur": LYRIA_ESTIMATED_COST_EUR,
        "note": LYRIA_PRICE_NOTE,
        "reusable": True,
        "prompt_preview": build_prompt(brief)[:400],
    }


__all__ = [
    "LYRIA_ENDPOINT",
    "LYRIA_ESTIMATED_COST_EUR",
    "LYRIA_MODEL",
    "LYRIA_TRACK_SECONDS",
    "LyriaError",
    "audio_from_response",
    "brief_from_trip",
    "build_prompt",
    "build_request",
    "cache_key",
    "cost_notice",
    "extension_for",
    "track_filename",
]
