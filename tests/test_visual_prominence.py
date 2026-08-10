"""Being in the film is not the same as being shown.

From a real film: the bunkers of the day the trip was partly about were
present - correctly, in three quarter-size collage tiles - while a picnic
photograph held the screen alone. Coverage passed. The day still did not
show what it was about.

So these checks are about the second question. They use the field names
the curation actually stores (`motifs`, `shows`, `story_value`,
`visual_quality`) and the ones the video analysis stores (`subject`,
`role`, `story_value`), and they name no place: the day that exposed this
is a test case, never a rule.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"
sys.dont_write_bytecode = True

_PACKAGE = "roadplanner_prominence_module_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(INTEGRATION)]
sys.modules[_PACKAGE] = _root

prom = importlib.import_module(f"{_PACKAGE}.visual_prominence")
analysis = importlib.import_module(f"{_PACKAGE}.video_analysis")

SOURCE = (INTEGRATION / "visual_prominence.py").read_text(encoding="utf-8")
MOTIF = "bunker"


def _photo(*, story=3, quality=3, shows=()):
    return {
        "story_value": story,
        "visual_quality": quality,
        "motifs": [],
        "shows": list(shows),
    }


def _clip(*, subject="", story=4, quality=4, role=analysis.ROLE_AMBIENT, media_id="v1"):
    return {
        "media_id": media_id,
        "subject": subject,
        "role": role,
        "story_value": story,
        "visual_quality": quality,
        "start_seconds": 1.0,
        "end_seconds": 5.0,
    }


def verify_a_motif_only_in_a_collage_is_covered_but_not_prominent() -> None:
    """The failure this module exists for, stated as a verdict."""
    media_ids = ["picnic", "b1", "b2", "b3"]
    analyses = {
        "picnic": _photo(story=3, quality=4),
        "b1": _photo(story=4, quality=4, shows=[MOTIF]),
        "b2": _photo(story=3, quality=3, shows=[MOTIF]),
        "b3": _photo(story=3, quality=3, shows=[MOTIF]),
    }
    found = prom.prominence_for_day(
        must_cover=[MOTIF], media_ids=media_ids, analyses=analyses
    )
    assert found["coverage"][MOTIF] == "covered"
    assert found["prominence"][MOTIF] == prom.STATE_SUPPORTING, found
    assert found["unmet_prominence"] == [MOTIF]


def verify_the_best_evidence_is_moved_into_the_prominent_place() -> None:
    """The scene plan makes the first photograph the hero, so it moves."""
    media_ids = ["picnic", "b1", "b2"]
    analyses = {
        "picnic": _photo(story=3, quality=4),
        "b1": _photo(story=5, quality=4, shows=[MOTIF]),
        "b2": _photo(story=2, quality=2, shows=[MOTIF]),
    }
    ordered = prom.promote_for_prominence(
        media_ids, must_cover=[MOTIF], analyses=analyses
    )
    assert ordered[0] == "b1", ordered
    # A reordering and never a selection.
    assert sorted(ordered) == sorted(media_ids)


def verify_an_existing_hero_already_satisfies_prominence() -> None:
    """Nothing moves when the day already opens on its own subject."""
    media_ids = ["b1", "picnic"]
    analyses = {
        "b1": _photo(story=5, quality=4, shows=[MOTIF]),
        "picnic": _photo(story=3, quality=4),
    }
    found = prom.prominence_for_day(
        must_cover=[MOTIF], media_ids=media_ids, analyses=analyses
    )
    assert found["prominence"][MOTIF] == prom.STATE_PROMINENT
    assert prom.promote_for_prominence(
        media_ids, must_cover=[MOTIF], analyses=analyses
    ) == media_ids


def verify_nothing_is_invented_when_the_motif_has_no_medium() -> None:
    """A day with no picture of its subject gets no picture of its subject."""
    media_ids = ["a", "b"]
    analyses = {"a": _photo(), "b": _photo()}
    found = prom.prominence_for_day(
        must_cover=[MOTIF], media_ids=media_ids, analyses=analyses
    )
    assert found["coverage"][MOTIF] == "unmet"
    assert found["prominence"][MOTIF] == prom.STATE_NONE
    assert prom.promote_for_prominence(
        media_ids, must_cover=[MOTIF], analyses=analyses
    ) == media_ids


def verify_a_day_without_central_motifs_is_left_alone() -> None:
    """No requirement, no reordering."""
    media_ids = ["a", "b", "c"]
    analyses = {name: _photo() for name in media_ids}
    assert prom.promote_for_prominence(
        media_ids, must_cover=[], analyses=analyses
    ) == media_ids


# --- medium-neutral -----------------------------------------------------


def verify_a_clip_of_the_subject_satisfies_prominence() -> None:
    """Clips open the day, so a clip of the subject already shows it."""
    media_ids = ["picnic", "b1"]
    analyses = {"picnic": _photo(), "b1": _photo(shows=[MOTIF])}
    clips = [_clip(subject=f"Ein {MOTIF} im Wald", role=analysis.ROLE_HERO)]
    found = prom.prominence_for_day(
        must_cover=[MOTIF], media_ids=media_ids, analyses=analyses, clips=clips
    )
    assert found["prominence"][MOTIF] == prom.STATE_PROMINENT, found
    # And no photograph is shuffled to fix something that is not broken.
    assert prom.promote_for_prominence(
        media_ids, must_cover=[MOTIF], analyses=analyses, clips=clips
    ) == media_ids


def verify_a_video_can_be_the_best_evidence() -> None:
    """Not preferred for being a video - preferred on its own score."""
    media_ids = ["weak"]
    analyses = {"weak": _photo(story=1, quality=1, shows=[MOTIF])}
    clips = [_clip(subject=f"{MOTIF} von innen", story=5, quality=5, media_id="v9")]
    found = prom.prominence_for_day(
        must_cover=[MOTIF],
        media_ids=["other", *media_ids],
        analyses={"other": _photo(), **analyses},
        clips=[_clip(subject="etwas anderes"), *clips],
    )
    best = found["best"][MOTIF]
    assert best["media_type"] == "video", best
    assert best["media_id"] == "v9", best


def verify_a_strong_photograph_beats_a_weak_clip() -> None:
    """The comparison runs both ways, or it is not a comparison."""
    found = prom.prominence_for_day(
        must_cover=[MOTIF],
        media_ids=["other", "strong"],
        analyses={"other": _photo(), "strong": _photo(story=5, quality=5, shows=[MOTIF])},
        clips=[_clip(subject="anderes"), _clip(subject=f"{MOTIF}", story=0, quality=1)],
    )
    assert found["best"][MOTIF]["media_type"] == "image", found["best"][MOTIF]


# --- the rules of the house ---------------------------------------------


def verify_only_one_medium_is_promoted() -> None:
    """A day has one opening; promoting a second only demotes the first."""
    media_ids = ["filler", "a1", "b1"]
    analyses = {
        "filler": _photo(),
        "a1": _photo(story=5, quality=5, shows=["alpha"]),
        "b1": _photo(story=4, quality=4, shows=["beta"]),
    }
    ordered = prom.promote_for_prominence(
        media_ids, must_cover=["alpha", "beta"], analyses=analyses
    )
    assert ordered[0] == "a1", ordered
    assert sorted(ordered) == sorted(media_ids)


def verify_it_is_deterministic() -> None:
    """The renderer draws frames in parallel; nothing here may wobble."""
    media_ids = ["a", "b1", "c", "b2"]
    analyses = {
        "a": _photo(),
        "c": _photo(),
        "b1": _photo(story=4, quality=4, shows=[MOTIF]),
        "b2": _photo(story=4, quality=4, shows=[MOTIF]),
    }
    first = prom.promote_for_prominence(
        list(media_ids), must_cover=[MOTIF], analyses=analyses
    )
    for _ in range(5):
        assert (
            prom.promote_for_prominence(
                list(media_ids), must_cover=[MOTIF], analyses=analyses
            )
            == first
        )


def verify_no_place_or_brand_is_named() -> None:
    """The day that exposed this is a test case, never a rule."""
    lowered = SOURCE.casefold()
    for name in ("wolfsschanze", "wolf", "masuren", "gierloz", "ketrzyn"):
        assert name not in lowered, name


for check in (
    verify_a_motif_only_in_a_collage_is_covered_but_not_prominent,
    verify_the_best_evidence_is_moved_into_the_prominent_place,
    verify_an_existing_hero_already_satisfies_prominence,
    verify_nothing_is_invented_when_the_motif_has_no_medium,
    verify_a_day_without_central_motifs_is_left_alone,
    verify_a_clip_of_the_subject_satisfies_prominence,
    verify_a_video_can_be_the_best_evidence,
    verify_a_strong_photograph_beats_a_weak_clip,
    verify_only_one_medium_is_promoted,
    verify_it_is_deterministic,
    verify_no_place_or_brand_is_named,
):
    check()

print("Visual prominence tests passed.")
