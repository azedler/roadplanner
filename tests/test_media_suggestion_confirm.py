"""Accepting the suggestions, because a suggestion is not in the film.

Live observation: 478 photographs assigned, 110 "Zu prüfen". Among the
110 was one taken **0 m** from the Trelleborg-Rostock crossing - as
close as a photograph can be to a stop.

That is not a broken rule. An automatic assignment requires the day to
match, and the nearest stop may belong to the neighbouring day, so no
distance can outvote a date that is one day out. Deliberately: an
overnight ferry and a camper still parked at yesterday's campsite look
identical from the outside, and letting proximity win would file this
morning's photographs into yesterday's chapter.

What was actually broken is what happens next. A photograph left as
`suggested` never reaches the film - the selection reads `manual` and
`automatic` and nothing else - and the only way to change that was one
dialog per photograph. A hundred of those are not a to-do list. They
are a hundred pictures quietly missing.

So the person decides, once, for all the near ones.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True

_PACKAGE = "roadplanner_confirm_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(ROOT / "custom_components" / "roadplanner_mcp")]
sys.modules[_PACKAGE] = _root

store_module = importlib.import_module(f"{_PACKAGE}.experience_store")


def _media(media_id: str, **overrides):
    base = {
        "id": media_id,
        "trip_id": "trip-1",
        "provider_item_id": f"item-{media_id}",
        "name": f"{media_id}.jpg",
        "assignment_status": "suggested",
        "linked_day_id": "day-2",
        "linked_stop_id": "faehre",
        "distance_m": 0.0,
    }
    base.update(overrides)
    return base


def _store_with(items):
    directory = tempfile.mkdtemp()
    store = store_module.ExperienceStore(Path(directory))
    store.initialize()
    state = store.load("trip-1")
    state["media"] = [store_module.normalize_media(item) for item in items]
    store.write(state)
    return store


def verify_a_near_suggestion_can_be_accepted_in_one_go() -> None:
    """The ferry photograph, and the ninety-nine others like it."""
    store = _store_with(
        [
            _media("ferry", distance_m=0.0),
            _media("beach", distance_m=1_200.0),
        ]
    )
    result = store.confirm_suggestions("trip-1", max_distance_m=1_500.0)
    assert result["confirmed"] == 2, result
    after = {item["id"]: item for item in store.load("trip-1")["media"]}
    for media_id in ("ferry", "beach"):
        # `manual` and not `automatic`: a person said so, and that is
        # both the truth and the reason a later rule change will leave
        # it alone.
        assert after[media_id]["assignment_status"] == "manual", after[media_id]
        assert after[media_id]["linked_stop_id"] == "faehre"


def verify_a_distant_guess_stays_a_guess() -> None:
    """Bulk acceptance is not "accept everything".

    A suggestion five kilometres from its stop is doubt about *which
    stop*, not about the date, and confirming that in bulk would file
    pictures under places nobody was.
    """
    store = _store_with(
        [
            _media("far", distance_m=4_800.0),
            _media("no_distance", distance_m=None),
            _media("dayless", linked_day_id=None, linked_stop_id=None, distance_m=10.0),
        ]
    )
    result = store.confirm_suggestions("trip-1", max_distance_m=1_500.0)
    assert result["confirmed"] == 0, result
    after = {item["id"]: item for item in store.load("trip-1")["media"]}
    assert after["far"]["assignment_status"] == "suggested"
    assert after["no_distance"]["assignment_status"] == "suggested"
    # No linked day means no answer to accept - normalisation has already
    # turned it into an unassigned photograph.
    assert after["dayless"]["assignment_status"] != "manual"


def verify_nothing_else_is_touched() -> None:
    """Only suggestions change, and only their status.

    An automatic assignment is still the rule's answer and stays
    re-decidable; a manual one was already somebody's decision.
    """
    store = _store_with(
        [
            _media("auto", assignment_status="automatic", distance_m=20.0),
            _media("hand", assignment_status="manual", distance_m=20.0),
            _media("open", distance_m=20.0, is_day_cover=True, caption="Am Kai"),
        ]
    )
    result = store.confirm_suggestions("trip-1", max_distance_m=1_500.0)
    assert result["confirmed"] == 1, result
    after = {item["id"]: item for item in store.load("trip-1")["media"]}
    assert after["auto"]["assignment_status"] == "automatic"
    assert after["hand"]["assignment_status"] == "manual"
    assert after["open"]["assignment_status"] == "manual"
    # Confirming is not editing: the caption and the cover flag are
    # somebody else's decisions and survive untouched.
    assert after["open"]["caption"] == "Am Kai"
    assert after["open"]["is_day_cover"] is True


def verify_one_day_can_be_confirmed_alone() -> None:
    """A day is a reviewable unit; a whole trip sometimes is not."""
    store = _store_with(
        [
            _media("d2", linked_day_id="day-2", distance_m=30.0),
            _media("d3", linked_day_id="day-3", distance_m=30.0),
        ]
    )
    result = store.confirm_suggestions("trip-1", max_distance_m=1_500.0, day_id="day-3")
    assert result["confirmed"] == 1, result
    after = {item["id"]: item for item in store.load("trip-1")["media"]}
    assert after["d2"]["assignment_status"] == "suggested"
    assert after["d3"]["assignment_status"] == "manual"


def verify_the_film_only_ever_sees_confirmed_photographs() -> None:
    """Why any of this matters, stated as an assertion.

    This is the rule that makes 110 "Zu prüfen" mean 110 pictures
    missing rather than 110 pictures pending.
    """
    intelligence = (ROOT / "custom_components" / "roadplanner_mcp" / "media_intelligence.py").read_text(
        encoding="utf-8"
    )
    assert 'assignment not in {"manual", "automatic"}' in intelligence


for check in (
    verify_a_near_suggestion_can_be_accepted_in_one_go,
    verify_a_distant_guess_stays_a_guess,
    verify_nothing_else_is_touched,
    verify_one_day_can_be_confirmed_alone,
    verify_the_film_only_ever_sees_confirmed_photographs,
):
    check()

print("Media suggestion confirmation tests passed.")
