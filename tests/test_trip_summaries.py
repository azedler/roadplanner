"""Behavioral tests for the trip summary prompts, fact sheets and cleanup.

Pure: no Home Assistant, no provider, no network. What matters here is what
the model is TOLD - the facts it may use and the invention it is forbidden -
and that whatever comes back survives being drawn into a PDF.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

MODULE_PATH = Path("custom_components/roadplanner_mcp/trip_summaries.py")
spec = spec_from_file_location("roadplanner_trip_summaries_test", MODULE_PATH)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def verify_every_prompt_forbids_invention_and_sets_the_tone() -> None:
    """The user asked for funny, not for fiction.

    Live request: "eine kleine lustige Zusammenfassung ... erstellt von der
    ki aus den Bildern". A model given photos will happily name a lake it
    cannot possibly know, so every prompt has to say so explicitly.
    """
    prompts = [
        module.DAY_SUMMARY_SYSTEM_PROMPT,
        module.DAY_SUMMARY_TEXT_ONLY_PROMPT,
        module.PERSON_SUMMARY_SYSTEM_PROMPT,
        module.PERSON_SUMMARY_TEXT_PROMPT,
        module.TRIP_SUMMARY_SYSTEM_PROMPT,
    ]
    for prompt in prompts:
        assert "Erfinde keine" in prompt, prompt[:80]
        assert "Augenzwinkern" in prompt, "the requested tone must be in every prompt"
        assert "nie auf Kosten der Personen" in prompt, (
            "this is printed and read by the people it describes"
        )
        assert "Emojis" in prompt

    # A day WITHOUT photos must not be told to describe experiences.
    assert "keine Fotos" in module.DAY_SUMMARY_TEXT_ONLY_PROMPT
    assert "behaupte nicht" in module.DAY_SUMMARY_TEXT_ONLY_PROMPT
    # A person who cannot be recognised gets no invented story.
    assert "leeren Text" in module.PERSON_SUMMARY_SYSTEM_PROMPT
    assert "rate nicht" in module.PERSON_SUMMARY_SYSTEM_PROMPT


def verify_day_facts_carry_only_what_the_trip_really_holds() -> None:
    day = {
        "title": "Nuuksio Nationalpark",
        "date": "2026-07-24",
        "distance_km": 187.4,
        "drive_minutes": 155,
        "notes": "Regen am Nachmittag",
    }
    stops = [
        {"name": "Haltia Naturzentrum", "type": "sightseeing"},
        {"name": "RMK Matsi Beach", "type": "wildcamp"},
        {"name": "", "type": "fuel"},
        "kein Dict",
    ]
    facts = module.day_facts(day, stops)
    assert "Nuuksio Nationalpark" in facts
    assert "2026-07-24" in facts
    assert "187 km" in facts
    assert "2 h 35 min" in facts
    assert "Haltia Naturzentrum (sightseeing)" in facts
    assert "RMK Matsi Beach" in facts
    assert "Regen am Nachmittag" in facts
    # A nameless stop contributes nothing, and a non-dict must not crash.
    assert facts.count("\n- ") == 2

    # Nothing invented for an empty day.
    sparse = module.day_facts({"title": ""}, [])
    assert "ohne Titel" in sparse
    assert "km" not in sparse and "Fahrzeit" not in sparse


def verify_the_trip_foreword_is_written_from_the_day_summaries() -> None:
    facts = module.trip_facts(
        {"title": "Finnland & Baltikum", "start_date": "2026-07-17", "end_date": "2026-08-08"},
        day_summaries=[("Tag 1", "Losgefahren."), ("Tag 2", ""), ("Tag 3", "Sauna.")],
        total_distance_km=5279.3,
        country_count=7,
    )
    assert "Finnland & Baltikum" in facts
    assert "2026-07-17 bis 2026-08-08" in facts
    assert "5279 km" in facts
    assert "Länder: 7" in facts
    assert "Losgefahren." in facts and "Sauna." in facts
    # A day without a summary contributes no empty bullet.
    assert "Tag 2:" not in facts
    # The day count still counts every day.
    assert "Reisetage: 3" in facts


def verify_a_model_answer_survives_being_drawn_into_a_pdf() -> None:
    """reportlab draws one line at a time - a newline becomes a black box."""
    assert module.normalize_summary("Erste Zeile\nZweite Zeile", max_chars=200) == (
        "Erste Zeile Zweite Zeile"
    )
    # Markdown leftovers and wrapping quotes come off.
    assert module.normalize_summary("## Überschrift weg", max_chars=200) == (
        "Überschrift weg"
    )
    assert module.normalize_summary('"Ein Zitat"', max_chars=200) == "Ein Zitat"
    assert module.normalize_summary("„Deutsche Anführung“", max_chars=200) == (
        "Deutsche Anführung"
    )
    assert module.normalize_summary(None, max_chars=200) == ""

    # Over-long answers stop at a sentence, not mid-thought.
    long_text = "Erster Satz. Zweiter Satz. Dritter Satz der viel zu lang ist."
    cut = module.normalize_summary(long_text, max_chars=30)
    assert cut.endswith("."), cut
    assert len(cut) <= 30
    # Even without any sentence end it never breaks a word in half.
    no_stop = module.normalize_summary("a" * 10 + " " + "b" * 40, max_chars=20)
    assert " " not in no_stop.strip() or not no_stop.endswith("b" * 3)


def verify_person_facts_stay_bounded() -> None:
    facts = module.person_facts(
        "Peer", "Mag Bagger", [f"Bildunterschrift {index}" for index in range(40)]
    )
    assert "Person: Peer" in facts
    assert "Mag Bagger" in facts
    assert facts.count("\n- ") == 20, "captions are capped"


verify_every_prompt_forbids_invention_and_sets_the_tone()
verify_day_facts_carry_only_what_the_trip_really_holds()
verify_the_trip_foreword_is_written_from_the_day_summaries()
verify_a_model_answer_survives_being_drawn_into_a_pdf()
verify_person_facts_stay_bounded()
print("Trip summary prompt tests passed.")
