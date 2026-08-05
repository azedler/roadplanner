"""Prompts, fact sheets and normalisation for the trip's written summaries.

Pure: no Home Assistant, no network, no provider. Everything here is string
work, which makes the part that decides WHAT the model is told - and what it
is forbidden to invent - testable on its own. The calls themselves live in
trip_summary_service.py.

Three kinds of summary, all requested from the live trip ("Außerdem würde
ich gern zu jeder Person eine kleine lustige Zusammenfassung haben. ... Dann
zu jedem Tag eine Zusammenfassung was wir da gemacht haben und natürlich für
die gesamte Reise eine Zusammenfassung"):

- per day: what actually happened, from the day's OWN photos together with
  the planned facts (stops, distance, driving time). Photos alone invent
  place names; facts alone only describe what was planned, not what was
  experienced. Both together is what the user asked for.
- per person: what one crew member got up to, recognised on the photos via
  their reference portrait.
- per trip: the whole journey, written from the day summaries rather than
  from the photos again - by then the facts are already condensed, and
  re-reading 200 photos would buy nothing.

The tone is warm and wry, never at somebody's expense: this ends up in a
printed keepsake that the people in it will read.
"""

from __future__ import annotations

import re
from typing import Any

# The models are told to answer as JSON so an empty answer stays
# distinguishable from a failed one.
# The details key that the generator writes and the PDF reads. It lives in
# the pure module so neither side has to import the other's service.
SUMMARY_DETAIL_KEY = "ai_summary"

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}

_TONE = (
    "Schreib auf Deutsch, warmherzig und mit einem Augenzwinkern - lustig "
    "auf Kosten der Situation, nie auf Kosten der Personen. Keine "
    "Anführungszeichen, keine Emojis, keine Überschrift."
)

_NO_INVENTION = (
    "Nutze ausschließlich die genannten Fakten und das, was auf den Bildern "
    "wirklich zu sehen ist. Erfinde keine Orte, Namen, Uhrzeiten, "
    "Wetterangaben oder Ereignisse. Wenn du dir bei etwas nicht sicher bist, "
    "lass es weg."
)

DAY_SUMMARY_SYSTEM_PROMPT = (
    "Du schreibst für einen gedruckten Reise-Rückblick zusammen, was an "
    "EINEM Reisetag passiert ist. Du bekommst die Planungsdaten des Tages "
    "und die Fotos dieses Tages. Die Fotos zeigen, was tatsächlich erlebt "
    "wurde; die Planungsdaten liefern Orte, Datum und Fahrstrecke. "
    f"{_NO_INVENTION} Maximal 3 kurze Sätze. {_TONE}"
)

DAY_SUMMARY_TEXT_ONLY_PROMPT = (
    "Du schreibst für einen gedruckten Reise-Rückblick zusammen, was an "
    "EINEM Reisetag passiert ist. Für diesen Tag gibt es keine Fotos, nur "
    "die Planungsdaten. Schreib entsprechend zurückhaltend darüber, wohin "
    "es ging - behaupte nicht, was dort erlebt wurde. "
    f"{_NO_INVENTION} Maximal 2 kurze Sätze. {_TONE}"
)

PERSON_SUMMARY_SYSTEM_PROMPT = (
    "Das erste Bild ist ein Referenzfoto einer Person. Prüfe, auf welchen "
    "der übrigen Reisefotos dieselbe Person eindeutig zu erkennen ist, und "
    "was sie dort gerade macht. Fasse zusammen, was diese Person auf der "
    "Reise erlebt hat. Ist die Person auf keinem Foto sicher zu erkennen, "
    "gib einen leeren Text zurück - rate nicht. "
    f"{_NO_INVENTION} Maximal 2 kurze Sätze in der dritten Person. {_TONE}"
)

PERSON_SUMMARY_TEXT_PROMPT = (
    "Du fasst für einen gedruckten Reise-Rückblick zusammen, was EINE Person "
    "laut den Bildunterschriften ihrer Fotos erlebt hat. "
    f"{_NO_INVENTION} Maximal 2 kurze Sätze in der dritten Person. {_TONE}"
)

TRIP_SUMMARY_SYSTEM_PROMPT = (
    "Du schreibst das Vorwort eines gedruckten Reise-Rückblicks: eine "
    "Zusammenfassung der GESAMTEN Reise. Du bekommst die Eckdaten und die "
    "bereits geschriebenen Tageszusammenfassungen. Fasse den Bogen der "
    "Reise zusammen - wo es hinging, was sie ausgemacht hat - statt Tage "
    f"aufzuzählen. {_NO_INVENTION} Maximal 6 Sätze. {_TONE}"
)


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")
# A model that ignores "no headline" tends to open with one.
_HEADING_RE = re.compile(r"^\s*(#+|\*+)\s*")


def normalize_summary(value: Any, *, max_chars: int) -> str:
    """Collapse a model answer into one clean, bounded block of prose.

    Everything here has to survive being drawn into a PDF, so control
    characters, markdown leftovers and surrounding quotes come out.
    """
    text = _CONTROL_CHARS_RE.sub(" ", str(value or ""))
    text = _HEADING_RE.sub("", text)
    text = " ".join(text.split())
    if len(text) >= 2 and text[0] in "\"'„»" and text[-1] in "\"'“«":
        text = text[1:-1].strip()
    if len(text) <= max_chars:
        return text
    # Cut at a sentence end rather than mid-word - a summary that stops
    # halfway through a thought reads as a bug, not as brevity.
    cut = text[:max_chars]
    for terminator in (". ", "! ", "? "):
        index = cut.rfind(terminator)
        if index > max_chars * 0.5:
            return cut[: index + 1].strip()
    return cut.rsplit(" ", 1)[0].strip()


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def day_facts(day: dict[str, Any], stops: list[dict[str, Any]]) -> str:
    """The planned facts of one day, as a compact fact sheet.

    Deliberately only facts the trip really holds. The day title and the
    stop names are the only place names the model is allowed to use.
    """
    lines = [f"Tag: {_clean(day.get('title'), 200) or 'ohne Titel'}"]
    date = _clean(day.get("date"), 40)
    if date:
        lines.append(f"Datum: {date}")
    distance = day.get("distance_km")
    if isinstance(distance, (int, float)) and distance > 0:
        lines.append(f"Gefahrene Strecke: {distance:.0f} km")
    minutes = day.get("drive_minutes")
    if isinstance(minutes, (int, float)) and minutes > 0:
        hours, rest = divmod(int(minutes), 60)
        lines.append(
            f"Fahrzeit: {hours} h {rest:02d} min" if hours else f"Fahrzeit: {rest} min"
        )
    named: list[str] = []
    for stop in stops:
        if not isinstance(stop, dict):
            continue
        name = _clean(stop.get("name"), 120)
        if not name:
            continue
        kind = _clean(stop.get("type"), 40)
        named.append(f"- {name}" + (f" ({kind})" if kind else ""))
    if named:
        lines.append("Stopps:")
        lines.extend(named[:12])
    notes = _clean(day.get("notes"), 400)
    if notes:
        lines.append(f"Notiz: {notes}")
    return "\n".join(lines)


def trip_facts(
    trip: dict[str, Any],
    *,
    day_summaries: list[tuple[str, str]],
    total_distance_km: float | None = None,
    country_count: int = 0,
) -> str:
    """Eckdaten plus the already-written day summaries."""
    lines = [f"Reise: {_clean(trip.get('title'), 200) or 'ohne Titel'}"]
    start = _clean(trip.get("start_date"), 40)
    end = _clean(trip.get("end_date"), 40)
    if start or end:
        lines.append(f"Zeitraum: {start or '?'} bis {end or '?'}")
    lines.append(f"Reisetage: {len(day_summaries)}")
    if total_distance_km:
        lines.append(f"Gesamtstrecke: {total_distance_km:.0f} km")
    if country_count:
        lines.append(f"Länder: {country_count}")
    written = [
        f"- {_clean(title, 120)}: {_clean(summary, 400)}"
        for title, summary in day_summaries
        if str(summary or "").strip()
    ]
    if written:
        lines.append("Tageszusammenfassungen:")
        lines.extend(written)
    return "\n".join(lines)


def person_facts(name: str, note: str, captions: list[str]) -> str:
    lines = [f"Person: {_clean(name, 120)}"]
    cleaned_note = _clean(note, 300)
    if cleaned_note:
        lines.append(f"Notiz: {cleaned_note}")
    usable = [_clean(caption, 200) for caption in captions if str(caption or "").strip()]
    if usable:
        lines.append("Bildunterschriften:")
        lines.extend(f"- {caption}" for caption in usable[:20])
    return "\n".join(lines)
