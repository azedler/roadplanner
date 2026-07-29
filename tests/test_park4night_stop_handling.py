"""Behavioral tests for Park4Night stop handling at assistant ingestion.

The assistant regularly writes stop names like "Parkplatz am Angelteich
(p4n #506374)" during a plan handover. The ID is a valuable provider
identity (enrichment classifies such stops as camping and links the p4n
page), but it is metadata, not part of the place's name - and once it's in
the name it pollutes every card, map legend, PDF and video. The sanitizer
must strip it from incoming names WITHOUT ever losing the reference:
_source_hints() scans name and notes, so the guaranteed notes URL keeps
every downstream feature working.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_p4n_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
ha_util = types.ModuleType("homeassistant.util")
ha_util.__path__ = []
sys.modules.setdefault("homeassistant.util", ha_util)
ha_dt = types.ModuleType("homeassistant.util.dt")
ha_dt.now = None
ha_dt.utcnow = None
sys.modules.setdefault("homeassistant.util.dt", ha_dt)


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


intelligence = load("destination_intelligence")


def verify_id_regex_matches_the_shapes_seen_in_the_wild() -> None:
    for text in (
        "Parkplatz am Angelteich (p4n #506374)",
        "P4N-506374",
        "Park4Night: 506374",
        "park 4 night id 506374",
    ):
        match = intelligence._PARK4NIGHT_ID_RE.search(text)
        assert match and match.group("id") == "506374", text


def verify_suffix_cleanup_keeps_the_real_name() -> None:
    assert (
        intelligence._clean_park4night_suffix("Parkplatz am Angelteich (p4n #506374)")
        == "Parkplatz am Angelteich"
    )
    assert intelligence._clean_park4night_suffix("p4n 506374") == ""


def verify_source_hint_extracted_from_notes_url() -> None:
    hints = intelligence._source_hints(
        {"name": "Parkplatz am Angelteich", "notes": "Park4Night: https://park4night.com/lieu/506374/"}
    )
    assert any(
        hint.get("provider") == "park4night" and hint.get("id") == "506374"
        for hint in hints
    )


def _strip(changes: dict) -> dict:
    # The sanitizer helper is pure; exercise it without the full module's
    # heavyweight import graph by rebuilding its exact logic contract here
    # via the real function.
    sanitizer = load("assistant_operation_sanitizer")
    sanitizer._strip_park4night_from_name(changes)
    return changes


def verify_ingestion_strips_id_from_name_and_preserves_it_in_notes() -> None:
    changes = _strip({"name": "Parkplatz am Angelteich (p4n #506374)", "type": "stellplatz"})
    assert changes["name"] == "Parkplatz am Angelteich"
    assert "https://park4night.com/lieu/506374/" in changes["notes"]


def verify_existing_notes_are_appended_not_replaced() -> None:
    changes = _strip(
        {
            "name": "Ängelholm Stellplatz p4n 448383",
            "notes": "Strom vorhanden, 80 SEK.",
        }
    )
    assert changes["name"] == "Ängelholm Stellplatz"
    assert changes["notes"].startswith("Strom vorhanden, 80 SEK.")
    assert "https://park4night.com/lieu/448383/" in changes["notes"]


def verify_reference_already_elsewhere_is_not_duplicated() -> None:
    changes = _strip(
        {
            "name": "Angelteich (p4n #506374)",
            "place_query": "park4night 506374 Schweden",
            "notes": "",
        }
    )
    assert changes["name"] == "Angelteich"
    assert changes.get("notes", "") == "", "the ID already survives in place_query"


def verify_name_that_is_only_the_reference_is_kept() -> None:
    changes = _strip({"name": "p4n 506374"})
    assert changes["name"] == "p4n 506374", "an empty name would be worse than a technical one"


def verify_names_without_reference_are_untouched() -> None:
    changes = _strip({"name": "Fiskeplats Korpträsket", "notes": "Schön ruhig."})
    assert changes["name"] == "Fiskeplats Korpträsket"
    assert changes["notes"] == "Schön ruhig."


def verify_sanitizer_calls_the_helper_in_the_stop_path() -> None:
    source = (PACKAGE_ROOT / "assistant_operation_sanitizer.py").read_text(encoding="utf-8")
    assert '_strip_park4night_from_name(operation["changes"])' in source


def verify_frontend_shows_clean_name_and_p4n_link() -> None:
    stop_card = (PACKAGE_ROOT / "frontend/features/trip-day-stop.js").read_text(encoding="utf-8")
    assert "cleanPlaceName(stop.name)" in stop_card, (
        "the stop card must display the cleaned name for existing roadbook "
        "entries too, without mutating the roadbook"
    )
    assert "park4nightReference(" in stop_card
    assert "Park4Night #" in stop_card
    helper = (PACKAGE_ROOT / "frontend/lib/place-links.js").read_text(encoding="utf-8")
    assert "park4night.com/lieu/" in helper


if __name__ == "__main__":
    verify_id_regex_matches_the_shapes_seen_in_the_wild()
    verify_suffix_cleanup_keeps_the_real_name()
    verify_source_hint_extracted_from_notes_url()
    verify_ingestion_strips_id_from_name_and_preserves_it_in_notes()
    verify_existing_notes_are_appended_not_replaced()
    verify_reference_already_elsewhere_is_not_duplicated()
    verify_name_that_is_only_the_reference_is_kept()
    verify_names_without_reference_are_untouched()
    verify_sanitizer_calls_the_helper_in_the_stop_path()
    verify_frontend_shows_clean_name_and_p4n_link()
    print("Park4Night stop handling tests passed.")
