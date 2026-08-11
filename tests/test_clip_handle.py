"""A clip needs a handle, and the analysed window must survive it.

From the review of the first film with video: the clips work, but some
of them "wirken zu kurz und eher wie bewegte Standbilder". The model
answers "when is the good part" precisely, which is right for finding a
moment and wrong for showing one - two and a bit seconds reads as a
moving still.

So the film adds a handle either side. The whole risk of that idea is in
one place: the analysed window is what the cache is keyed on and what
records what the model actually said. Widening it IN PLACE would quietly
change the meaning of a stored answer, make a paid call look unnecessary,
and leave nobody able to tell what Gemini recommended from what
Roadplanner stretched.

    analysis   5.2 - 7.4   asked, answered, stored, cached
    render     4.6 - 8.0   what the viewer sees, derived

These checks are about that separation, about the bounds a handle may
never cross, and about the run costing nothing: it is arithmetic, in a
module that cannot call anything.
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

_PACKAGE = "roadplanner_clip_handle_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(INTEGRATION)]
sys.modules[_PACKAGE] = _root

orch = importlib.import_module(f"{_PACKAGE}.video_orchestration")
analysis = importlib.import_module(f"{_PACKAGE}.video_analysis")
proxy = importlib.import_module(f"{_PACKAGE}.video_proxy")

EXPORT = (INTEGRATION / "trip_film_export.py").read_text(encoding="utf-8")
ORCH = (INTEGRATION / "video_orchestration.py").read_text(encoding="utf-8")


def _segment(
    *,
    start=5.2,
    end=7.4,
    role=analysis.ROLE_AMBIENT,
    window_start=0.0,
    window_end=30.0,
    duration=30.0,
):
    return {
        "media_id": "vid-1",
        "start_seconds": start,
        "end_seconds": end,
        "duration_seconds": round(end - start, 3),
        "role": role,
        "window_start": window_start,
        "window_end": window_end,
        "source_duration_seconds": duration,
    }


def _length(window):
    return round(window[1] - window[0], 3)


def verify_a_short_clip_grows_when_there_is_room() -> None:
    """2.2 s of ambient is a moving still. It becomes a scene."""
    before = _segment()
    after = orch.render_window(before)
    assert _length(after) > 2.2, after
    low, _high = orch.CLIP_TARGET_SECONDS[analysis.ROLE_AMBIENT]
    assert _length(after) >= low, after


def verify_a_hero_may_take_a_larger_handle() -> None:
    """The moment of the day is allowed more room than an ambient one."""
    ambient = orch.render_window(_segment(role=analysis.ROLE_AMBIENT))
    hero = orch.render_window(_segment(role=analysis.ROLE_HERO))
    assert _length(hero) > _length(ambient), (hero, ambient)


def verify_a_transition_may_stay_short() -> None:
    """Brevity is what a transition is for."""
    before = _segment(start=3.0, end=4.4, role=analysis.ROLE_TRANSITION)
    assert orch.render_window(before) == (3.0, 4.4)


def verify_a_clip_that_is_long_enough_is_left_alone() -> None:
    """Nothing is inflated to a target."""
    before = _segment(start=2.0, end=8.0)
    assert orch.render_window(before) == (2.0, 8.0)


def verify_the_handle_stays_inside_the_recording() -> None:
    """A clip at the very start cannot begin before the file does."""
    at_start = orch.render_window(_segment(start=0.1, end=2.0))
    assert at_start[0] >= 0.0, at_start
    at_end = orch.render_window(
        _segment(start=8.0, end=9.9, window_end=10.0, duration=10.0)
    )
    assert at_end[1] <= 10.0, at_end


def verify_the_handle_stays_inside_the_window_that_was_judged() -> None:
    """Beyond it is material the prefilter never vouched for.

    The window is what the technical prefilter selected and the model
    actually looked at. A handle reaching past it would show seconds
    nothing has judged - which is the opposite of what a curated film is.
    """
    tight = _segment(start=5.0, end=6.4, window_start=4.8, window_end=6.6)
    found = orch.render_window(tight)
    assert found[0] >= 4.8 and found[1] <= 6.6, found


def verify_a_clip_with_no_room_at_all_is_unchanged() -> None:
    before = _segment(start=0.0, end=2.0, window_end=2.0, duration=2.0)
    assert orch.render_window(before) == (0.0, 2.0)


def verify_the_ceiling_for_one_clip_still_holds() -> None:
    """The film's own limit outranks any target."""
    found = orch.render_window(_segment(start=0.0, end=0.6), max_seconds=1.0)
    assert _length(found) <= 1.0, found


def verify_the_analysis_window_is_never_rewritten() -> None:
    """The one thing that must not happen, checked on the real fields."""
    before = _segment()
    kept = dict(before)
    [after] = orch.with_render_windows([before])
    # The input dict itself is untouched...
    assert before == kept, before
    # ...and the analysis fields travel on unchanged beside the new ones.
    assert after["start_seconds"] == kept["start_seconds"]
    assert after["end_seconds"] == kept["end_seconds"]
    assert after["duration_seconds"] == kept["duration_seconds"]
    assert after["render_start_seconds"] <= after["start_seconds"]
    assert after["render_end_seconds"] >= after["end_seconds"]
    assert after["render_duration_seconds"] > after["duration_seconds"]


def verify_the_analysed_window_stays_the_middle() -> None:
    """A handle, not a shift: the moment stays inside what it grew from."""
    for role in (analysis.ROLE_AMBIENT, analysis.ROLE_HERO):
        for start, end in ((5.2, 7.4), (0.5, 2.0), (12.0, 13.1)):
            before = _segment(start=start, end=end, role=role, window_end=40.0, duration=40.0)
            found = orch.render_window(before)
            assert found[0] <= start and found[1] >= end, (role, before, found)


def verify_it_is_deterministic() -> None:
    """Frames render in parallel tabs; the same clip must cut the same."""
    before = _segment()
    first = orch.render_window(before)
    for _ in range(5):
        assert orch.render_window(before) == first


def verify_nothing_here_can_call_a_provider() -> None:
    """The extension is local by construction, not by intention.

    `video_orchestration` is the module that decides and cannot act - no
    network, no files, no clock. Stated as a check because "no new costs"
    is the kind of promise that is only worth making if something breaks
    when it stops being true.
    """
    for forbidden in (
        "async_analyze_video",
        "aiohttp",
        "requests",
        "open(",
        "Path(",
        "time.time",
        "datetime",
    ):
        assert forbidden not in ORCH, forbidden


def verify_the_film_cuts_and_times_the_render_window() -> None:
    """Both, or the clip is cut to one length and held for another."""
    assert 'segment.get("render_start_seconds")' in EXPORT
    assert 'segment.get("render_end_seconds")' in EXPORT
    assert 'segment.get("render_duration_seconds")' in EXPORT
    # And the handle is applied where the clips are chosen, once.
    assert "with_render_windows(" in EXPORT
    # The film's per-clip ceiling is the one the cutter enforces, rather
    # than a second number written down beside it.
    assert "max_seconds=MAX_RENDER_SECONDS" in EXPORT
    assert proxy.MAX_RENDER_SECONDS > 0


for check in (
    verify_a_short_clip_grows_when_there_is_room,
    verify_a_hero_may_take_a_larger_handle,
    verify_a_transition_may_stay_short,
    verify_a_clip_that_is_long_enough_is_left_alone,
    verify_the_handle_stays_inside_the_recording,
    verify_the_handle_stays_inside_the_window_that_was_judged,
    verify_a_clip_with_no_room_at_all_is_unchanged,
    verify_the_ceiling_for_one_clip_still_holds,
    verify_the_analysis_window_is_never_rewritten,
    verify_the_analysed_window_stays_the_middle,
    verify_it_is_deterministic,
    verify_nothing_here_can_call_a_provider,
    verify_the_film_cuts_and_times_the_render_window,
):
    check()

print("Clip handle tests passed.")
