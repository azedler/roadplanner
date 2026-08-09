"""Which stage lost the pictures - counted, not guessed.

An important place is named in its day's story and barely visible in the
film. That one symptom has at least four causes needing opposite fixes:
the photographs were never accepted; they never reached the pool; the
vision pass never connected them to the motif; or the scene plan dropped
them because the day could not pay for them.

Guessing between those is how a special case for one place gets written.
So the module counts at every stage and names the first one where the
numbers stop making sense - and these checks drive each cause separately
to prove the verdicts are actually distinguishable.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True

_PACKAGE = "roadplanner_diagnosis_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(ROOT / "custom_components" / "roadplanner_mcp")]
sys.modules[_PACKAGE] = _root

diagnosis = importlib.import_module(f"{_PACKAGE}.day_film_diagnosis")

CHAPTER = {"chapter_id": "day-4", "title": "Wolfsschanze", "importance": "major_highlight"}
MEDIA = [{"id": f"m{index}", "latitude": 54.0} for index in range(40)]


def _curation(*, pool, selected, shows, must=("wolfsschanze",)):
    """A stored derivation where `shows` lists the ids showing the motif."""
    return {
        "day_id": "day-4",
        "photo_count": len(MEDIA),
        "series_count": 9,
        "pool_media_ids": list(pool),
        "media_ids": list(selected),
        "rejected": [{"id": "x"}] * 4,
        "analysis_count": len(pool),
        "brief": {"must_cover": list(must), "labels": {"wolfsschanze": "Wolfsschanze"}},
        "coverage": {
            "met": [name for name in must if any(item in shows for item in selected)],
            "unmet": [name for name in must if not any(item in shows for item in selected)],
        },
        "analyses": {
            media_id: {"zeigt": "Wolfsschanze Bunker" if media_id in shows else "Wald"}
            for media_id in pool
        },
    }


def verify_no_suitable_photograph_is_its_own_answer() -> None:
    """The most important verdict to be able to give.

    No amount of weighting conjures a picture nobody took, and a system
    that cannot say so will be re-tuned forever against material that
    does not exist.
    """
    pool = [f"m{index}" for index in range(20)]
    result = diagnosis.diagnose_day(
        chapter=CHAPTER,
        curation=_curation(pool=pool, selected=pool[:12], shows=set()),
        media=MEDIA,
        film_media_ids=pool[:12],
    )
    assert result["verdict"] == diagnosis.VERDICT_CURATION
    assert "kein einziges Bild" in result["detail"], result["detail"]
    assert result["motifs"][0]["in_pool"] == 0


def verify_curated_away_is_told_apart_from_planned_away() -> None:
    """The two failures that look identical in a finished film."""
    pool = [f"m{index}" for index in range(20)]
    shows = {"m0", "m1", "m2", "m3", "m4"}

    # (a) The pictures are in the pool and the selection ignored them.
    curated_away = diagnosis.diagnose_day(
        chapter=CHAPTER,
        curation=_curation(pool=pool, selected=[f"m{i}" for i in range(10, 22)], shows=shows),
        media=MEDIA,
        film_media_ids=[f"m{i}" for i in range(10, 22)],
    )
    assert curated_away["verdict"] == diagnosis.VERDICT_CURATION
    assert "keins davon genommen" in curated_away["detail"], curated_away["detail"]

    # (b) The pictures were curated and the film left them out.
    selected = ["m0", "m1", "m2", "m3", "m10", "m11", "m12", "m13"]
    planned_away = diagnosis.diagnose_day(
        chapter=CHAPTER,
        curation=_curation(pool=pool, selected=selected, shows=shows),
        media=MEDIA,
        # Only one of the four motif pictures survived the frame budget.
        film_media_ids=["m0", "m10", "m11", "m12", "m13"],
    )
    assert planned_away["verdict"] == diagnosis.VERDICT_SCENE_PLAN, planned_away
    assert "verdrängt" in planned_away["detail"]


def verify_a_day_that_is_fine_says_so() -> None:
    pool = [f"m{index}" for index in range(20)]
    shows = {"m0", "m1", "m2", "m3", "m4", "m5"}
    result = diagnosis.diagnose_day(
        chapter=CHAPTER,
        curation=_curation(pool=pool, selected=list(shows) + ["m10", "m11"], shows=shows),
        media=MEDIA,
        film_media_ids=list(shows) + ["m10", "m11"],
    )
    assert result["verdict"] == diagnosis.VERDICT_OK, result
    assert result["motifs"][0]["in_selection"] >= 2


def verify_every_stage_is_counted() -> None:
    """The numbers are the deliverable; the verdict is a reading of them."""
    pool = [f"m{index}" for index in range(20)]
    result = diagnosis.diagnose_day(
        chapter=CHAPTER,
        curation=_curation(pool=pool, selected=pool[:12], shows={"m0"}),
        media=MEDIA,
        film_media_ids=pool[:8],
    )
    stages = result["stages"]
    for key in (
        "media_total",
        "technically_accepted",
        "rejected",
        "series_count",
        "pool_size",
        "analysed",
        "selected",
        "in_film",
    ):
        assert key in stages, key
    assert stages["media_total"] == 40
    assert stages["pool_size"] == 20
    assert stages["selected"] == 12
    assert stages["in_film"] == 8
    # And the chain only ever narrows. A stage that grew would be a bug
    # in the diagnosis itself.
    assert stages["in_film"] <= stages["selected"] <= stages["pool_size"]


def verify_an_uncurated_day_is_a_finding_not_an_error() -> None:
    result = diagnosis.diagnose_day(
        chapter=CHAPTER, curation=None, media=MEDIA, film_media_ids=[]
    )
    assert result["curated"] is False
    assert result["stages"]["media_total"] == 40
    assert result["verdict"] in {
        diagnosis.VERDICT_OK,
        diagnosis.VERDICT_CURATION,
        diagnosis.VERDICT_NO_MATERIAL,
    }
    empty = diagnosis.diagnose_day(
        chapter=CHAPTER, curation=None, media=[], film_media_ids=[]
    )
    assert empty["verdict"] == diagnosis.VERDICT_NO_MATERIAL


def verify_it_knows_nothing_about_any_particular_place() -> None:
    source = (
        ROOT / "custom_components" / "roadplanner_mcp" / "day_film_diagnosis.py"
    ).read_text(encoding="utf-8")
    body = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    # The place that prompted this may appear in prose; it must not
    # appear in a comparison.
    assert '"wolfsschanze"' not in body.casefold()
    assert "== \"Wolfsschanze\"" not in body


for check in (
    verify_no_suitable_photograph_is_its_own_answer,
    verify_curated_away_is_told_apart_from_planned_away,
    verify_a_day_that_is_fine_says_so,
    verify_every_stage_is_counted,
    verify_an_uncurated_day_is_a_finding_not_an_error,
    verify_it_knows_nothing_about_any_particular_place,
):
    check()

print("Day film diagnosis tests passed.")
