"""What pressing a button costs, declared once.

The panel grew nine different ways to say "do that again": auffrischen,
neu berechnen, neu bewerten, neu einlesen, synchronisieren, aktualisieren.
Six verbs, spread over five cards, each invented where it was needed.

The live complaint - "ich finde ja es gibt zu viele Schalter für
Auffrischungen" - is right, and the count is only half of it. The wording
described **what the code does**. What somebody standing in front of the
button needs to know is different and much shorter:

- Does this cost money?
- Does it change something I have already decided?
- Will it take a while?

None of those can be read off "auffrischen". So they are declared here,
next to each other, where they can be compared - and the panel renders
them rather than each card describing its own button in its own words.

The four classes
----------------

``read`` - reads again, changes nothing, costs nothing. Almost always
the wrong thing to offer a button for: if it is free and changes
nothing, it should simply happen. These are listed so that the ones that
remain have to justify themselves.

``recompute`` - rearranges data that is already here. Free and offline,
but it **overwrites decisions**, so it needs saying what survives. Photo
assignment is the example: manual assignments are never touched, and
that sentence is the whole reason the button is safe to press.

``fetch`` - goes out to the network. Free in money, expensive in time,
and it can fail for reasons that have nothing to do with Roadplanner.

``model`` - calls a language or image model, or a music model. This is
the only class that spends money, and it is the reason the file exists:
a paid action must never look like a free one.

Why a module rather than a table in the panel
---------------------------------------------

Because it is checkable here. A contract test reads this and the panel
together, and every declared action has to be a real action. When
somebody adds a paid button, forgetting to declare it is a failing test
rather than a surprise on somebody's bill.
"""

from __future__ import annotations

from typing import Any

COST_READ = "read"
COST_RECOMPUTE = "recompute"
COST_FETCH = "fetch"
COST_MODEL = "model"

COST_CLASSES = (COST_READ, COST_RECOMPUTE, COST_FETCH, COST_MODEL)

# One line per action, in the words a user needs rather than the words
# the code uses. `effect` says what changes; `note` is the caveat that
# makes the button safe - or the warning that makes it deliberate.
ACTION_COSTS: dict[str, dict[str, Any]] = {
    # --- rearranges what is already here -----------------------------
    "media_reassign": {
        "cost": COST_RECOMPUTE,
        "effect": "Ordnet vorhandene Fotos nach der aktuellen Regel neu zu.",
        "note": "Von Hand gesetzte Zuordnungen bleiben unberührt. Es wird nichts nachgeladen.",
    },
    # --- goes out to the network --------------------------------------
    "onedrive_sync": {
        "cost": COST_FETCH,
        "effect": "Holt neue Fotos aus OneDrive.",
        "note": "Nur der Zeitraum der Reise, Originale bleiben in OneDrive.",
    },
    "calculate_day_route": {
        "cost": COST_FETCH,
        "effect": "Berechnet die Straßenroute dieses Tages neu.",
        "note": "Fragt den Routendienst. Dauert wenige Sekunden.",
    },
    "calculate_trip_routes": {
        "cost": COST_FETCH,
        "effect": "Berechnet die Straßenrouten aller Tage neu.",
        "note": "Fragt den Routendienst für jeden Tag. Kann bei langen Reisen dauern.",
    },
    # --- costs money ---------------------------------------------------
    "media_curate_days": {
        "cost": COST_MODEL,
        "effect": "Lässt Gemini die Fotos jedes Reisetags ansehen und bewerten.",
        "note": (
            "Verbraucht Kontingent: einmal je Tag, danach gecacht. Unveränderte Tage werden nicht "
            "erneut analysiert. Es werden nur verkleinerte Vorschaubilder "
            "gesendet, ohne Datum und Ort."
        ),
    },
    "media_curate_trip": {
        "cost": COST_MODEL,
        "effect": "Lässt Gemini die Fotos der Reise noch einmal bewerten.",
        "note": "Verbraucht Kontingent. Unveränderte Fotos werden nicht erneut analysiert.",
    },
    "media_curate_stop": {
        "cost": COST_MODEL,
        "effect": "Lässt Gemini die Fotos dieses Stopps bewerten.",
        "note": "Verbraucht Kontingent.",
    },
    "story_director_run": {
        "cost": COST_MODEL,
        "effect": "Lässt Gemini die Reisegeschichte neu schreiben.",
        "note": "Verbraucht Kontingent. Von Hand bearbeitete Texte bleiben unberührt.",
    },
    "generate_trip_summaries": {
        "cost": COST_MODEL,
        "effect": "Lässt Gemini die Tageszusammenfassungen neu schreiben.",
        "note": "Verbraucht Kontingent.",
    },
    "story_film_music_generate": {
        "cost": COST_MODEL,
        "effect": "Lässt Lyria die Musik für diesen Film erzeugen.",
        "note": (
            "Kostet Geld - der Preis steht vorher im Angebot. "
            "Bereits erzeugte Abschnitte werden wiederverwendet."
        ),
    },
    "story_music_prototype_generate": {
        "cost": COST_MODEL,
        "effect": (
            "Erzeugt die Stücke für den Architekturvergleich A/B/C über "
            "einen Filmausschnitt."
        ),
        "note": (
            "Kostet Geld - der Preis steht vorher daneben. Höchstens drei "
            "Anfragen, weil zwei Fassungen dasselbe Klangbett benutzen; "
            "der Soundtrack des ganzen Films wird hier nicht erzeugt."
        ),
    },
    # The live audit found a dozen paid buttons that LOOKED free because
    # nobody had written their line here. Every action below reaches
    # Gemini (or another paid provider) when pressed - the one thing a
    # user cannot see from the button's own wording.
    "assistant_chat": {
        "cost": COST_MODEL,
        "effect": "Schickt die Nachricht an den Reisebegleiter (Gemini).",
        "note": "Verbraucht Kontingent je Nachricht.",
    },
    "assistant_prepare": {
        "cost": COST_MODEL,
        "effect": "Lässt Gemini Vorschläge für die Reise ausarbeiten.",
        "note": "Verbraucht Kontingent.",
    },
    "assistant_briefing": {
        "cost": COST_MODEL,
        "effect": "Lässt Gemini das Tagesbriefing schreiben.",
        "note": "Verbraucht Kontingent je Briefing.",
    },
    "assistant_test": {
        "cost": COST_MODEL,
        "effect": "Prüft die Verbindung zum Reisebegleiter mit einer echten Testanfrage.",
        "note": "Verbraucht ein kleines Stück Kontingent - es ist ein echter Aufruf.",
    },
    "assistant_prepare_locations": {
        "cost": COST_MODEL,
        "effect": "Lässt Gemini fehlende GPS-Punkte dieses Tages vorschlagen.",
        "note": "Verbraucht Kontingent. Die Vorschläge landen im Korb, nicht direkt in der Reise.",
    },
    "assistant_prepare_trip_locations": {
        "cost": COST_MODEL,
        "effect": "Lässt Gemini fehlende GPS-Punkte der ganzen Reise vorschlagen.",
        "note": "Verbraucht Kontingent. Die Vorschläge landen im Korb, nicht direkt in der Reise.",
    },
    "park4night_lookup": {
        "cost": COST_MODEL,
        "effect": "Lässt Gemini die Park4Night-Seite dieses Stopps auslesen.",
        "note": "Verbraucht Kontingent je Seite.",
    },
    "place_link_lookup": {
        "cost": COST_MODEL,
        "effect": "Lässt Gemini die verlinkte Seite auslesen.",
        "note": "Verbraucht Kontingent - außer bei Google-Maps-Links, die ohne KI aufgelöst werden.",
    },
    "prepare_place_enrichment": {
        "cost": COST_MODEL,
        "effect": "Holt Ortsdaten aus den Kartendiensten und bereitet die Vorschau vor.",
        "note": (
            "Verbraucht Karten-Kontingent; die optionale KI-Bereinigung "
            "zusätzlich Gemini-Kontingent. Übernommen wird erst nach deiner Auswahl."
        ),
    },
    "media_video_analyze": {
        "cost": COST_MODEL,
        "effect": "Schickt die verkleinerte Prüfkopie des Videos zur Analyse an Gemini.",
        "note": "Verbraucht Kontingent je Video. Das Original bleibt zu Hause.",
    },
    "export_trip_video": {
        "cost": COST_MODEL,
        "effect": "Baut das Reisevideo und lässt Gemini die Erzähltexte schreiben.",
        "note": "Verbraucht Kontingent für die Texte; das Rendern selbst ist gratis, dauert aber Minuten.",
    },
    "export_trip_pdf": {
        "cost": COST_MODEL,
        "effect": "Baut das Reise-PDF - Personenzusammenfassungen und Crew-Fotoauswahl kommen von Gemini.",
        "note": "Verbraucht Kontingent, wenn der Reisebegleiter konfiguriert ist; ohne ihn entsteht das PDF trotzdem, nur ohne diese Texte.",
    },
    "run_system_check": {
        "cost": COST_MODEL,
        "effect": "Prüft alle Dienste - inklusive einer echten Testanfrage an Gemini.",
        "note": "Verbraucht ein kleines Stück Kontingent, wenn der Reisebegleiter konfiguriert ist.",
    },
}


def describe(action: str) -> dict[str, Any] | None:
    """What one action costs, or None when it is an ordinary edit.

    Ordinary edits - adding a stop, renaming a day - are deliberately not
    in the table. A button that does exactly what it says needs no
    explanation, and explaining all of them would bury the five that
    genuinely need it.
    """
    found = ACTION_COSTS.get(str(action or ""))
    return dict(found) if found else None


def panel_payload() -> dict[str, dict[str, Any]]:
    """The whole table, for the panel to render from.

    Sent once with the panel data rather than duplicated in JavaScript,
    so the button a user reads and the rule a test checks are the same
    sentence.
    """
    return {action: dict(entry) for action, entry in ACTION_COSTS.items()}


__all__ = [
    "ACTION_COSTS",
    "COST_CLASSES",
    "COST_FETCH",
    "COST_MODEL",
    "COST_READ",
    "COST_RECOMPUTE",
    "describe",
    "panel_payload",
]
