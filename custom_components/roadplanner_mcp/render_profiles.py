"""How large a film is rendered - and nothing else.

A render profile decides pixels, not content. The same story, the same
scene plan, the same photographs and clips and seconds, at whatever size
somebody needs: a small one to look at twelve times during development,
a large one to keep. Those are not two films.

The frame rate is deliberately not a setting. Thirty everywhere, so a
profile cannot quietly change the timing of a plan - and so this does not
turn into an export matrix while the actual question was file size.

The renderer carries the same table in `render_profiles.mjs`, and a test
reads both files and compares them. A profile that means 1440p on one
side and 1080p on the other is this project's oldest bug wearing a new
hat, and it has cost four releases already.
"""

from __future__ import annotations

from typing import Any

# The surface every layout is authored against. Never rendered directly:
# a profile scales this, so no component has to know which one is running.
DESIGN_WIDTH = 1280
DESIGN_HEIGHT = 720

# One frame rate for every profile.
FILM_FPS = 30

RENDER_PROFILES: dict[str, dict[str, Any]] = {
    "review_480": {
        "id": "review_480",
        "width": 854,
        "height": 480,
        "fps": FILM_FPS,
        "label": "Review schnell · 480p",
        "description": "Schnellste Abnahmeversion für Entwicklung und Filmabnahme.",
        "suffix": "review-480p",
        "experimental": False,
        "recommended": False,
    },
    "review_720": {
        "id": "review_720",
        "width": 1280,
        "height": 720,
        "fps": FILM_FPS,
        "label": "Review detailliert · 720p",
        "description": "Kleine Datei mit guter Lesbarkeit für die feinere Abnahme.",
        "suffix": "review-720p",
        "experimental": False,
        "recommended": False,
    },
    "full_hd": {
        "id": "full_hd",
        "width": 1920,
        "height": 1080,
        "fps": FILM_FPS,
        "label": "Full HD · 1080p",
        "description": "Normale hochwertige Ausgabe.",
        "suffix": "1080p",
        "experimental": False,
        "recommended": False,
    },
    "high_quality": {
        "id": "high_quality",
        "width": 2560,
        "height": 1440,
        "fps": FILM_FPS,
        "label": "Hohe Qualität · 1440p",
        "description": "Für Archiv, Tablet und Fernseher. Empfohlen für finale Filme.",
        "suffix": "1440p",
        "experimental": False,
        "recommended": True,
    },
    "uhd_4k": {
        "id": "uhd_4k",
        "width": 3840,
        "height": 2160,
        "fps": FILM_FPS,
        "label": "4K · experimentell",
        "description": (
            "Sehr lange Renderzeit, deutlich mehr Speicher, hoher RAM- und "
            "CPU-Bedarf. Ob das auf einer bestimmten Home-Assistant-Hardware "
            "sinnvoll läuft, ist offen."
        ),
        "suffix": "4k",
        "experimental": True,
        "recommended": False,
    },
}

# What a job means when it says nothing: the size rendered until now, so
# an old client that never learned about profiles gets what it always got.
DEFAULT_RENDER_PROFILE = "review_720"

# What a review copy is, when nobody says otherwise.
DEFAULT_REVIEW_PROFILE = "review_720"

# Which profiles a review copy may be made in. A copy exists to be small
# and looked at; making a "review copy" in 4K would be a re-encode with no
# purpose.
REVIEW_COPY_PROFILES = ("review_480", "review_720")


def render_profile(profile_id: str | None) -> dict[str, Any]:
    """One profile, or the default. Never a KeyError on user input."""
    return RENDER_PROFILES.get(
        str(profile_id or ""), RENDER_PROFILES[DEFAULT_RENDER_PROFILE]
    )


def profile_choices(default: str = DEFAULT_RENDER_PROFILE) -> list[dict[str, Any]]:
    """What the panel offers, in the order it should read.

    Each entry says whether it is the default, because a picker that shows
    the first option while the backend renders another one is a film that
    came out at a size nobody chose - and nothing about the panel would
    reveal it. The flag travels with the list so the two cannot disagree.
    """
    return [
        {
            "id": entry["id"],
            "label": entry["label"],
            "description": entry["description"],
            "width": entry["width"],
            "height": entry["height"],
            "fps": entry["fps"],
            "experimental": entry["experimental"],
            "recommended": entry["recommended"],
            "default": entry["id"] == default,
        }
        for entry in RENDER_PROFILES.values()
    ]


def review_choices() -> list[dict[str, Any]]:
    """The sizes a copy of a finished film may be made in."""
    return [
        entry
        for entry in profile_choices(DEFAULT_REVIEW_PROFILE)
        if entry["id"] in REVIEW_COPY_PROFILES
    ]


def film_filename(trip_slug: str, profile_id: str, *, source_suffix: str = "") -> str:
    """A name that says what the file is, in the language of the product.

    `reise-2026-1440p.mp4`, and for a copy made from one:
    `reise-2026-1440p-review-720p.mp4`. Nobody should have to open a file
    to find out which version of a film it is.
    """
    slug = "".join(
        char if char.isalnum() or char in "-_" else "-" for char in str(trip_slug or "")
    ).strip("-")[:60] or "reise"
    profile = render_profile(profile_id)
    if source_suffix:
        return f"{slug}-{source_suffix}-{profile['suffix']}.mp4"
    return f"{slug}-{profile['suffix']}.mp4"


__all__ = [
    "DEFAULT_RENDER_PROFILE",
    "DEFAULT_REVIEW_PROFILE",
    "DESIGN_HEIGHT",
    "DESIGN_WIDTH",
    "FILM_FPS",
    "RENDER_PROFILES",
    "REVIEW_COPY_PROFILES",
    "film_filename",
    "profile_choices",
    "render_profile",
    "review_choices",
]
