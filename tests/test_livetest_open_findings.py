"""The backend half of the 2026-08-21 live findings.

O-5  Two counters for one set. The "Ohne Tag" tile counted photographs
     without a linked day (112) while the filter chip beside it counted
     the assignment status (163). Only the status partitions the library,
     and only the chip's number could be reached by clicking - so the
     tile was both wrong and unverifiable.

O-8  A failed refresh deleted what already worked. When a picture
     provider was briefly unreachable, the gallery was rewritten with an
     empty image list, so a stop lost its planning pictures and the day
     lost its cover - while the dialog promised "Die Stoppdaten bleiben
     vollständig erhalten" in the same breath.

O-9  A class name collision of my own making: the diagnostics accordions
     were given a class that already carried unrelated rules, so they
     inherited `padding: 16px 0` and their headings ran into the card
     edge instead of separating from the subtitle.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_components" / "roadplanner_mcp"
FRONTEND = PACKAGE / "frontend"


def verify_the_unassigned_stat_answers_the_filters_question() -> None:
    """One definition, so the tile and the chip cannot disagree."""
    source = (PACKAGE / "panel_payload_builder.py").read_text(encoding="utf-8")
    match = re.search(r'"unassigned_count":\s*sum\((.*?)\),\n', source, re.S)
    assert match, "unassigned_count fehlt"
    body = match.group(1)
    assert "assignment_status" in body, (
        "die Kachel zählt wieder etwas anderes als der Filter daneben: " + body.strip()
    )
    assert "linked_day_id" not in body, body.strip()

    # And the tile reads the number the filter computed, not a second one.
    media = (FRONTEND / "features" / "media.js").read_text(encoding="utf-8")
    assert '"Ohne Tag", counts.unassigned)' in media, (
        "die Kachel liest wieder eine eigene Zahl"
    )


def verify_a_failed_refresh_keeps_the_pictures_it_already_had() -> None:
    """The error branch must carry the previous images forward.

    Read as AST rather than by importing: the module pulls in the whole
    Home Assistant helper stack. What matters is that the branch which
    used to write `"images": []` now reads the existing gallery.
    """
    source = (PACKAGE / "destination_gallery_manager.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    handlers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        and "RoadplannerError" in {
            name.id for name in ast.walk(node) if isinstance(name, ast.Name)
        }
        and any(
            isinstance(inner, ast.Constant) and inner.value == "provider_errors"
            for inner in ast.walk(node)
        )
    ]
    assert handlers, "kein Fehlerzweig, der eine Galerie zurückgibt"
    for handler in handlers:
        rendered = ast.unparse(handler)
        assert '"images": []' not in rendered.replace("'", '"'), (
            "ein gescheiterter Abruf löscht wieder die vorhandenen Bilder"
        )
        assert "existing" in rendered, (
            "der Fehlerzweig kennt den vorherigen Stand nicht: " + rendered[:200]
        )


def verify_the_diagnostics_accordions_own_their_class() -> None:
    """A shared class name means one card silently restyles another."""
    diagnostics = (FRONTEND / "features" / "diagnostics.js").read_text(encoding="utf-8")
    styles = (FRONTEND / "lib" / "styles.js").read_text(encoding="utf-8")
    assistant = (FRONTEND / "features" / "assistant.js").read_text(encoding="utf-8")

    assert diagnostics.count("diagnostics-accordion") == 4, (
        "nicht alle vier Diagnose-Ausklapper tragen ihre eigene Klasse"
    )
    assert "diagnostics-section" not in diagnostics, (
        "die Ausklapper benutzen wieder die fremde Klasse"
    )
    # The other owner keeps its own rules, untouched.
    assert "diagnostics-section" in assistant
    assert ".diagnostics-accordion > summary" in styles
    # And the two never describe the same element again.
    assert ".diagnostics-section > summary" not in styles


def verify_the_candidate_details_cannot_be_painted_over() -> None:
    """The block a place candidate is judged by needs its own surface."""
    styles = (FRONTEND / "lib" / "styles.js").read_text(encoding="utf-8")
    rule = next(
        line for line in styles.split("\n") if ".place-candidate-details {" in line
    )
    for expected in ("position: relative", "z-index: 1", "background:"):
        assert expected in rule, f"{expected} fehlt: {rule.strip()}"


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Live findings tests passed.")


if __name__ == "__main__":
    main()
