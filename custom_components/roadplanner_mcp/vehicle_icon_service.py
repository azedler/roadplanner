"""Generate a small icon-style illustration of the crew's vehicle.

Roadplanner keeps no dedicated vehicle/crew data model - crew details
(including the vehicle) live in free text on the trip. This service takes a
short free-text description (e.g. "cremefarbener Kastenwagen mit orangem
Streifen") and asks Gemini's image model for a flat, line-art icon that
matches the same visual style already used elsewhere (trip summary PDF,
crew cards): plain background, single accent color, no text baked into the
image, no photorealism. Callers own storing/caching the returned bytes.
"""

from __future__ import annotations

from .assistant_provider import AssistantImageResult, AssistantProvider
from .roadplanner import ValidationError

_MAX_DESCRIPTION_LENGTH = 500

_PROMPT_TEMPLATE = (
    "Erzeuge ein einzelnes, flaches Icon im Stil eines minimalistischen "
    "Liniensymbols (wie ein modernes App-Icon), das folgendes Fahrzeug "
    "darstellt: {description}\n\n"
    "Stilvorgaben:\n"
    "- Nur weiße/helle Umrisslinien auf einem einfarbigen, ruhigen "
    "Hintergrund - keine Farbverläufe, keine Fotorealistik.\n"
    "- Frontal- oder Seitenansicht des Fahrzeugs, zentriert, mit Rand "
    "(Icon füllt nicht das ganze Bild).\n"
    "- Kein Text, keine Buchstaben, kein Wasserzeichen im Bild.\n"
    "- Quadratisches Format."
)


def build_vehicle_icon_prompt(description: str) -> str:
    """Build the deterministic prompt for a vehicle icon request."""
    clean = str(description or "").strip()
    if not clean:
        raise ValidationError("Für das Fahrzeug-Icon fehlt eine Beschreibung")
    if len(clean) > _MAX_DESCRIPTION_LENGTH:
        clean = clean[:_MAX_DESCRIPTION_LENGTH]
    return _PROMPT_TEMPLATE.format(description=clean)


async def async_generate_vehicle_icon(
    provider: AssistantProvider,
    *,
    vehicle_description: str,
) -> AssistantImageResult:
    """Generate one vehicle icon image from a short free-text description."""
    if provider is None or not provider.configured:
        raise ValidationError(
            "Für die Icon-Generierung ist kein KI-Anbieter konfiguriert"
        )
    prompt = build_vehicle_icon_prompt(vehicle_description)
    return await provider.async_generate_image(prompt=prompt)
