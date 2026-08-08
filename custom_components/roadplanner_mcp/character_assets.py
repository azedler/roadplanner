"""The cast of a film, as pictures that are made once and then kept.

A film draws two kinds of character: the vehicle, and the people. Both
have the same problem and therefore the same answer here.

Why generated once and stored, never per render
-----------------------------------------------

A picture asked for at render time would be slow, would cost money on
every attempt, and - the part that actually matters - **would make two
renders of the same trip two different films**. A generative model does
not return the same image twice. A journey that looks different every
time it is exported is not a record of anything.

So the flow is: generate **once**, have a human look at it, store the
confirmed bytes, and from then on every render reads the same file. The
model is involved in making the asset, never in using it.

Why a photograph is the reference
---------------------------------

The brief is that the camper should be recognisable as *ours*. A prompt
alone cannot do that: "high-roof camper van" describes ten thousand
vehicles. Roadplanner already holds a photograph of the real one, so the
photograph goes into the request as a reference and the prompt asks for
an illustration *of that vehicle* rather than of the category.

The style is fixed here rather than left to whoever types the request,
because the asset has to sit on a dark map beside a route line and a
caption. That is a constraint of the film, not a matter of taste, and
the one place it can be stated is where the prompt is built.

Two variants, and why exactly two
---------------------------------

- ``map`` - a three-quarter view, small, seen from slightly above. This
  is the one that drives; it lives at about forty pixels wide.
- ``side`` - a clean side elevation for the intro, the crew line-up and
  the closing frame, where the vehicle is large enough to be looked at.

Nothing here animates. A character is a picture with a known anchor
point, and the composition places it - which is also what keeps the door
open for crew figures later without touching the map at all.

What this module does not do
----------------------------

It does not call anything and it does not store anything. It builds
prompts, validates what came back, and shrinks it - so all of that is
testable without a model and without a filesystem.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)

CHARACTER_DIR = "characters"

# The two things a film draws as a character. Kept as data rather than as
# branching, because the crew arriving later must not need a new code
# path - only a new entry.
KIND_VEHICLE = "vehicle"
KIND_CREW = "crew"
CHARACTER_KINDS = (KIND_VEHICLE, KIND_CREW)

VARIANT_MAP = "map"
VARIANT_SIDE = "side"
CHARACTER_VARIANTS = (VARIANT_MAP, VARIANT_SIDE)

# A character is never shown larger than a third of a 1280-pixel frame,
# so twice that is generous and keeps the package small.
ASSET_EDGE = 512
ASSET_QUALITY = 88
MAX_ASSET_BYTES = 260 * 1024
ASSET_FILENAME_RE = re.compile(r"^(vehicle|crew)-(map|side)-[0-9a-f]{16}\.png$")

MAX_DESCRIPTION_LENGTH = 400

# The style the film needs, stated once. Every variant inherits it.
_STYLE = (
    "Stil: freundliche, klare Illustration mit weichen Konturen - wie aus "
    "einem hochwertigen Reisebilderbuch. Flache Farbflächen mit dezenter "
    "Schattierung, keine Fotorealistik, keine harte Comic-Outline, keine "
    "Airbrush-Effekte.\n"
    "Hintergrund vollständig transparent. Kein Boden, kein Schatten, keine "
    "Szene, keine Landschaft, keine Straße.\n"
    "Kein Text, keine Buchstaben, keine Zahlen, kein Wasserzeichen, kein "
    "Nummernschild mit lesbarer Schrift.\n"
    "Das Motiv ist zentriert und vollständig im Bild, mit etwas Rand.\n"
    "Quadratisches Format."
)

_VEHICLE_VIEW = {
    VARIANT_MAP: (
        "Ansicht: leichte Dreiviertelansicht von schräg vorn und leicht "
        "oberhalb, nach rechts fahrend. Das Fahrzeug muss auch bei sehr "
        "kleiner Darstellung (etwa 40 Pixel breit) an seiner Silhouette "
        "erkennbar bleiben - deshalb klare Umrisse und kräftige "
        "Farbkontraste zwischen Aufbau, Dach und Fenstern."
    ),
    VARIANT_SIDE: (
        "Ansicht: exakte Seitenansicht von links, nach rechts fahrend, "
        "ohne Perspektive. Sauber und ruhig, für eine große Darstellung "
        "im Vor- und Abspann."
    ),
}

_CREW_VIEW = {
    VARIANT_MAP: (
        "Ansicht: Kopf und Schultern, frontal, freundlicher Ausdruck. "
        "Muss klein erkennbar bleiben."
    ),
    VARIANT_SIDE: (
        "Ansicht: Halbfigur, frontal, entspannte Haltung, freundlicher "
        "Ausdruck."
    ),
}


def asset_filename(kind: str, variant: str, fingerprint: str) -> str:
    """The one place a character filename is built.

    Built from a fingerprint of what the picture was made *from*, so a
    changed reference photograph or a changed description is a different
    file. Nothing that anybody typed reaches a path join - the string
    simply never contains it.
    """
    if kind not in CHARACTER_KINDS:
        raise ValueError("Unbekannte Figurenart")
    if variant not in CHARACTER_VARIANTS:
        raise ValueError("Unbekannte Ansicht")
    digest = re.sub(r"[^0-9a-f]", "", str(fingerprint or "").lower())[:16]
    if len(digest) != 16:
        raise ValueError("Ungültige Figurenkennung")
    return f"{kind}-{variant}-{digest}.png"


def fingerprint(*parts: Any) -> str:
    """A stable short digest of everything an asset was derived from."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part or "").encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()[:16]


def build_prompt(kind: str, variant: str, description: str, *, from_photo: bool) -> str:
    """The request for one character asset.

    ``from_photo`` changes what the prompt *claims*, not just how it is
    worded: with a reference image it asks for an illustration of that
    specific vehicle, without one it can only ask for the category. A
    prompt that says "unser Fahrzeug" with nothing attached would be
    asking the model to invent a fact.
    """
    if kind not in CHARACTER_KINDS:
        raise ValueError("Unbekannte Figurenart")
    if variant not in CHARACTER_VARIANTS:
        raise ValueError("Unbekannte Ansicht")
    clean = " ".join(str(description or "").split())[:MAX_DESCRIPTION_LENGTH]
    views = _VEHICLE_VIEW if kind == KIND_VEHICLE else _CREW_VIEW
    subject = (
        "Zeichne eine Illustration **genau des Fahrzeugs auf dem beigefügten "
        "Foto**. Übernimm Fahrzeugform, Dachaufbau, Fensteraufteilung und "
        "vor allem die Farbgebung von Aufbau und Dach so genau wie möglich. "
        "Es soll dieses eine Fahrzeug sein, kein ähnliches."
        if kind == KIND_VEHICLE and from_photo
        else "Zeichne eine Illustration der Person **auf dem beigefügten Foto**. "
        "Übernimm Frisur, Haarfarbe und Kleidung; es soll diese Person sein."
        if from_photo
        else "Zeichne eine Illustration des folgenden Motivs."
    )
    lines = [subject]
    if clean:
        lines.append(f"Zusätzliche Beschreibung: {clean}")
    lines.append(views[variant])
    lines.append(_STYLE)
    return "\n\n".join(lines)


def shrink_asset(data: bytes) -> bytes | None:
    """Re-encode a generated asset at film size, keeping transparency.

    PNG rather than JPEG, and that is not a preference: the asset is
    drawn over a map, so it needs an alpha channel. A JPEG would arrive
    with a white box around the camper.
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow ships with Home Assistant
        return None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        image = image.convert("RGBA")
        image.thumbnail((ASSET_EDGE, ASSET_EDGE), Image.LANCZOS)
        image = _trim_transparent(image)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
    except Exception as err:  # noqa: BLE001 - a broken asset is simply refused
        _LOGGER.debug("Figurenbild nicht verwendbar: %s", type(err).__name__)
        return None
    shrunk = buffer.getvalue()
    return shrunk if len(shrunk) <= MAX_ASSET_BYTES else None


def _trim_transparent(image: Any) -> Any:
    """Crop away fully transparent margins.

    A model asked for "centred with some margin" obliges by different
    amounts every time. Left alone, that margin becomes part of the
    picture, and the camper would sit at a slightly different size on
    the map for every asset ever generated. Trimming makes the bounding
    box the subject, so the composition's own scale is the only one that
    decides how big the vehicle is.
    """
    box = image.getbbox()
    return image.crop(box) if box else image


def build_character_package(assets: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Turn stored assets into the film's character section plus its files.

    ``assets`` is a list of ``{"kind", "variant", "data"}``. Anything that
    cannot be re-encoded is dropped rather than failing the export: a film
    without a confirmed illustration falls back to the drawn camper, which
    is a worse picture and still a complete film.
    """
    entries: list[dict[str, Any]] = []
    files: dict[str, bytes] = {}
    for asset in assets or []:
        kind = str(asset.get("kind") or "")
        variant = str(asset.get("variant") or "")
        if kind not in CHARACTER_KINDS or variant not in CHARACTER_VARIANTS:
            continue
        raw = asset.get("data")
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            continue
        blob = shrink_asset(bytes(raw))
        if not blob:
            continue
        path = f"{CHARACTER_DIR}/{kind}-{variant}.png"
        files[path] = blob
        entries.append(
            {
                "kind": kind,
                "variant": variant,
                "path": path,
                "sha256": hashlib.sha256(blob).hexdigest(),
                "size_bytes": len(blob),
            }
        )
    if not entries:
        return {}, {}
    return {"assets": entries}, files


def validate_characters(payload: Any) -> dict[str, Any] | None:
    """Read a character section with the rules that produced it."""
    if payload in (None, {}):
        return None
    if not isinstance(payload, dict):
        raise ValueError("Figurenangaben sind kein Objekt")
    assets = payload.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("Figurenangaben ohne Bilder")
    if len(assets) > len(CHARACTER_KINDS) * len(CHARACTER_VARIANTS):
        raise ValueError("Zu viele Figurenbilder")
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError("Figurenbild ist kein Objekt")
        kind = asset.get("kind")
        variant = asset.get("variant")
        if kind not in CHARACTER_KINDS or variant not in CHARACTER_VARIANTS:
            raise ValueError("Figurenbild mit unbekannter Art")
        if asset.get("path") != f"{CHARACTER_DIR}/{kind}-{variant}.png":
            raise ValueError("Figurenbild an der falschen Stelle")
        # Same rule as the crew portraits, and for the same reason: a
        # render package is written into a shared folder, and a link in
        # it would be a capability sitting on disk.
        for value in asset.values():
            text = str(value)
            if "://" in text or text.startswith("/api/"):
                raise ValueError("Figurenangaben dürfen keine Adressen enthalten")
    return payload


__all__ = [
    "ASSET_FILENAME_RE",
    "CHARACTER_DIR",
    "CHARACTER_KINDS",
    "CHARACTER_VARIANTS",
    "KIND_CREW",
    "KIND_VEHICLE",
    "VARIANT_MAP",
    "VARIANT_SIDE",
    "asset_filename",
    "build_character_package",
    "build_prompt",
    "fingerprint",
    "shrink_asset",
    "validate_characters",
]
