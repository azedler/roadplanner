"""The composed day text says the day number once.

Live finding V5 (#375), read off the rendered film: the text page and
every caption under it opened with

    "Tag 2: Tag 2 - Ostsee. 1290 Kilometer und 15 Stunden 27 Minuten ..."

Every opening template states the day number itself, and a day title
almost always starts with that same number - so it came out twice in one
sentence, and a third time on the chapter card above it.

Affects trips WITHOUT a written text: an edited trip carries a
`video_caption` per chapter and never reaches this composer.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

sys.dont_write_bytecode = True

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_story_text_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


manifest = load("travel_story_manifest")

# The facts of the chapter the live film showed, as the manifest carries them.
DAY_TWO = {"day_number": 2, "distance_km": 1290, "duration_minutes": 927}
DAY_TWO_STOPS = ["Gedser Fährhafen", "Indalsälven Rastplats"]


def verify_a_title_that_opens_with_its_day_number_loses_that_prefix() -> None:
    strip = manifest.title_without_day_prefix
    assert strip("Tag 2 — Ostsee") == "Ostsee"
    assert strip("Tag 1 - Møn / Dänemark") == "Møn / Dänemark"
    assert strip("Tag 12: Nordkapp") == "Nordkapp"
    assert strip("  Tag 3 · Kiruna") == "Kiruna"


def verify_a_title_that_is_only_its_day_number_leaves_nothing() -> None:
    assert manifest.title_without_day_prefix("Tag 2") == ""


def verify_a_title_without_a_day_number_is_untouched() -> None:
    strip = manifest.title_without_day_prefix
    assert strip("Ostsee") == "Ostsee"
    # No digit, so no prefix - a word that merely starts the same way is
    # not a numbering.
    assert strip("Tage der Ruhe") == "Tage der Ruhe"
    assert strip("Tagebuch") == "Tagebuch"
    assert strip("") == ""


def verify_the_live_sentence_now_says_the_number_once() -> None:
    text = manifest.compose_story(
        DAY_TWO, index=1, title="Tag 2 — Ostsee", stop_names=DAY_TWO_STOPS
    )
    assert text.startswith("Tag 2: Ostsee."), text
    assert "Tag 2: Tag 2" not in text
    assert text.count("Tag 2") == 1, text
    # And nothing else about the sentence changed.
    assert "1290 Kilometer und 15 Stunden 27 Minuten liegen dazwischen." in text
    assert "Unterwegs: Gedser Fährhafen und Indalsälven Rastplats." in text


def verify_every_opening_variant_says_the_number_once() -> None:
    for index in range(len(manifest._OPENINGS)):
        number = index + 1
        text = manifest.compose_story(
            {"day_number": number},
            index=index,
            title=f"Tag {number} — Ostsee",
            stop_names=[],
        )
        assert text.count(f"Tag {number}") == 1, (index, text)
        assert "Ostsee" in text, (index, text)


def verify_a_title_that_was_only_a_number_falls_back_to_the_short_opening() -> None:
    text = manifest.compose_story({"day_number": 2}, index=1, title="Tag 2", stop_names=[])
    assert text.count("Tag 2") == 1, text
    # The opening that needs no title at all, rather than "Tag 2: ." with
    # an empty title where a place should be.
    assert ":" not in text, text


CHECKS = [
    verify_a_title_that_opens_with_its_day_number_loses_that_prefix,
    verify_a_title_that_is_only_its_day_number_leaves_nothing,
    verify_a_title_without_a_day_number_is_untouched,
    verify_the_live_sentence_now_says_the_number_once,
    verify_every_opening_variant_says_the_number_once,
    verify_a_title_that_was_only_a_number_falls_back_to_the_short_opening,
]


def verify_every_check_in_this_module_is_registered() -> None:
    declared = {
        name
        for name, value in globals().items()
        if name.startswith("verify_") and callable(value)
        and name != "verify_every_check_in_this_module_is_registered"
    }
    assert declared == {check.__name__ for check in CHECKS}, sorted(
        declared - {check.__name__ for check in CHECKS}
    )


if __name__ == "__main__":
    verify_every_check_in_this_module_is_registered()
    for check in CHECKS:
        check()
        print(f"ok - {check.__name__}")
    print(f"\n{len(CHECKS)} checks passed")
