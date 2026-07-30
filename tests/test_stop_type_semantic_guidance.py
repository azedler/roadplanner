"""Contract test: the compile prompt explains what the experience stop types mean.

Live observation: the assistant filed the Skuleberget hike (a mountain in the
Höga-Kusten area) as "Stadtbesichtigung" (sightseeing). Root cause: the prompt
listed the allowed stop types as a bare enumeration with no semantics - the
only type family with an explicit meaning rule was the sleep-place group, so
the model guessed for everything else. The prompt now carries a short meaning
rule for the experience types (activity / sightseeing / attraction /
viewpoint), including the explicit anchor that a mountain or hiking trail is
activity, never sightseeing.
"""
from __future__ import annotations

from pathlib import Path

PROMPT_SOURCE = Path(
    "custom_components/roadplanner_mcp/assistant_prompt.py"
).read_text(encoding="utf-8")


def verify_experience_types_have_a_meaning_rule() -> None:
    assert "Bedeutung der Erlebnis-Typen" in PROMPT_SOURCE, (
        "the prompt must explain experience stop types, not just list them"
    )
    for anchor in ("Wanderung", "Stadtbummel", "Museum", "Aussichtshalt"):
        assert anchor in PROMPT_SOURCE, (
            f"meaning rule must anchor each type with concrete examples "
            f"(missing: {anchor})"
        )


def verify_hike_is_pinned_to_activity() -> None:
    assert (
        "Ein Berg oder Wanderweg ist damit activity, niemals\n  sightseeing."
        in PROMPT_SOURCE
    ), "the exact misclassification seen live must be called out explicitly"


def verify_guidance_only_names_allowed_types() -> None:
    compile_source = Path(
        "custom_components/roadplanner_mcp/assistant_compile.py"
    ).read_text(encoding="utf-8")
    for stop_type in ("activity", "sightseeing", "attraction", "viewpoint"):
        assert f'"{stop_type}"' in compile_source, (
            f"guidance names type {stop_type!r} which the sanitizer must allow"
        )


if __name__ == "__main__":
    verify_experience_types_have_a_meaning_rule()
    verify_hike_is_pinned_to_activity()
    verify_guidance_only_names_allowed_types()
    print("Stop type semantic guidance tests passed.")
