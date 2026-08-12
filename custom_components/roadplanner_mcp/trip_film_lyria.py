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

# Lyria lives on Vertex AI and nowhere else.
#
# This module used to call `generativelanguage.googleapis.com` - the
# Gemini Developer API, which is what the rest of the integration talks
# to and which does not serve Lyria at all. The call could never have
# succeeded; it had simply never been made. That is what a note written
# from memory looks like once somebody checks it.
#
# Vertex wants three things this integration did not have: a project, a
# region, and an OAuth2 bearer token from a service account. The API key
# is refused there.
LYRIA_REGION = "us-central1"
# The long-form model: a film is minutes, and a thirty-second clip looped
# eight times is not a soundtrack.
LYRIA_MODEL = "lyria-3-pro-preview"
LYRIA_CLIP_MODEL = "lyria-3-clip-preview"

# The most audio one call yields.
#
# 118 stood here, with a comment calling the API "documented as about two
# minutes". It is three. The number mattered more than it looks: the
# planner cuts the film into sections and then orders one generation per
# section, so a track length that is too small buys more calls than the
# film needs - and the section length was never checked against it at
# all, which left four and a half minutes of silence in a twelve-minute
# film. Both are fixed; this is the figure a plan may rely on.
LYRIA_TRACK_SECONDS = 180
LYRIA_TIMEOUT_SECONDS = 300
MAX_TRACK_BYTES = 40 * 1024 * 1024
TRACK_FILENAME_RE = re.compile(r"^lyria-[0-9a-f]{16}\.(mp3|wav)$")

# What one generation costs.
#
# Billed PER REQUEST rather than per second: one call to Lyria 3 Pro is
# one charge whether it returns thirty seconds or three minutes. So the
# thing that decides the price of a film's music is how many sections it
# is planned in, which is exactly why the section arithmetic above is
# worth getting right.
LYRIA_ESTIMATED_COST_USD = 0.08
LYRIA_ESTIMATED_COST_EUR = 0.08
LYRIA_PRICE_NOTE = (
    "Schätzwert je Generierung. Die tatsächliche Abrechnung erfolgt über "
    "dein Google-Cloud-Projekt und kann abweichen."
)


def lyria_endpoint(project: str, *, region: str = LYRIA_REGION, model: str = LYRIA_MODEL) -> str:
    """Where a generation is asked for.

    Built from a project and a region rather than being a constant,
    because on Vertex those are part of the address. Both are checked
    against a narrow pattern first: they end up in a URL, and a project
    id is not somewhere to accept free text.
    """
    name = str(project or "").strip()
    if not _PROJECT_RE.match(name):
        raise LyriaError(
            "Für Vertex AI fehlt eine gültige Google-Cloud-Projekt-ID"
        )
    place = str(region or "").strip() or LYRIA_REGION
    if not _REGION_RE.match(place):
        raise LyriaError(f"Ungültige Vertex-Region: {place!r}")
    chosen = str(model or "").strip() or LYRIA_MODEL
    if chosen not in (LYRIA_MODEL, LYRIA_CLIP_MODEL):
        raise LyriaError(f"Unbekanntes Lyria-Modell: {chosen!r}")
    return (
        f"https://{place}-aiplatform.googleapis.com/v1/projects/{name}"
        f"/locations/{place}/publishers/google/models/{chosen}:predict"
    )


_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_REGION_RE = re.compile(r"^[a-z]+-[a-z]+[0-9]$")


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


# The AI Studio route: the Gemini Developer API's Interactions endpoint,
# authenticated with the key this integration already has.
LYRIA_INTERACTIONS_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"


def build_gemini_request(prompt: str, *, model: str = LYRIA_MODEL) -> tuple[str, dict[str, Any]]:
    """The AI Studio form: one key, one endpoint, no project."""
    clean = str(prompt or "").strip()
    if not clean:
        raise LyriaError("Für die Musikgenerierung fehlt ein Prompt")
    return LYRIA_INTERACTIONS_ENDPOINT, {"model": model, "input": clean}


def build_vertex_request(
    prompt: str,
    *,
    project: str,
    region: str = LYRIA_REGION,
    model: str = LYRIA_MODEL,
    seconds: float | None = None,
) -> tuple[str, dict[str, Any]]:
    """The Vertex form: `instances` and `parameters`, bearer token."""
    clean = str(prompt or "").strip()
    if not clean:
        raise LyriaError("Für die Musikgenerierung fehlt ein Prompt")
    instance: dict[str, Any] = {"prompt": clean}
    if seconds is not None:
        wanted = max(1.0, min(float(seconds), float(LYRIA_TRACK_SECONDS)))
        instance["duration_seconds"] = round(wanted)
    return lyria_endpoint(project, region=region, model=model), {
        "instances": [instance],
        "parameters": {"sampleCount": 1},
    }


def build_request(
    prompt: str,
    *,
    project: str = "",
    region: str = LYRIA_REGION,
    model: str = LYRIA_MODEL,
    seconds: float | None = None,
) -> tuple[str, dict[str, Any]]:
    """Where this generation goes, and in what shape.

    BOTH routes exist, and deliberately so. Google's own accounts of
    where Lyria lives disagree: one says the Gemini Developer API does
    not serve it and Vertex is the only way, another says Lyria 3 is
    reachable from AI Studio's Interactions endpoint with an ordinary
    API key. The documentation that would settle it is not reachable
    from this environment, and picking one on the strength of the most
    recent sentence somebody read is how the wrong endpoint got shipped
    and defended by a test in the first place.

    So the choice is configuration, not a guess: with a project
    configured this goes to Vertex, otherwise to AI Studio. The one
    piece of evidence that actually decides it is a call that succeeds,
    and whichever route works is the one that stays.
    """
    if str(project or "").strip():
        return build_vertex_request(
            prompt, project=project, region=region, model=model, seconds=seconds
        )
    return build_gemini_request(prompt, model=model)


def audio_from_response(payload: Any) -> tuple[bytes, str] | None:
    """Find the generated audio in whatever the response nests it in.

    Deliberately a search rather than a fixed path. The exact nesting is
    the part of this integration written from documentation rather than
    from a call that was actually made, so looking for "an object that
    carries audio bytes" survives that nesting being one level different
    - and it cannot pick up anything else, because nothing else in the
    response carries base64 under an audio name.

    Vertex answers `predict` with a `predictions` array whose entries
    carry `bytesBase64Encoded`. The walker only knew `data`, which is the
    Gemini shape - so against the endpoint this module now actually calls
    it would have found nothing, thrown away a generation that had
    already been paid for, and reported "keine Audiodaten".
    """
    import base64

    found: list[tuple[bytes, str]] = []

    def walk(node: Any, depth: int = 0, under: str = "") -> None:
        if found or depth > 12:
            return
        if isinstance(node, dict):
            # Two shapes, because there are two documented ways to ask.
            # The Interactions response nests blocks that say `"type":
            # "audio"`; generateContent returns `inlineData` with an
            # audio mime type. Recognising only one of them would mean a
            # paid generation whose audio we then threw away.
            mime = str(node.get("mime_type") or node.get("mimeType") or "")
            # Vertex names it `bytesBase64Encoded` and often omits the
            # mime type entirely; Gemini names it `data` and says what it
            # is. Either counts, and a Vertex prediction counts even
            # unlabelled - there is nothing else in that response it
            # could be.
            encoded = node.get("bytesBase64Encoded") or node.get("data")
            is_audio = (
                str(node.get("type") or "") == "audio"
                or mime.startswith("audio/")
                or bool(node.get("bytesBase64Encoded"))
                # `{"output_audio": {"data": ...}}` - the shape the
                # google-genai SDK reads. The block itself says nothing
                # about being audio; the key that holds it does. Without
                # this the walker skips it, and a generation that was
                # billed comes back as "keine Audiodaten".
                or "audio" in under.lower()
            )
            if is_audio and encoded:
                try:
                    blob = base64.b64decode(str(encoded), validate=False)
                except Exception:  # noqa: BLE001 - a bad block is simply skipped
                    return
                if blob and len(blob) <= MAX_TRACK_BYTES:
                    found.append((blob, mime or "audio/mp3"))
                return
            for key, value in node.items():
                walk(value, depth + 1, str(key))
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1, under)

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
