"""A quality check is a window into the film, not a second film.

A full render at 1440p is projected at about an hour and a half. Nobody
judges a cut that costs that much, so the check has to be a piece - and
the whole worth of that piece depends on one property: it must be the
SAME film. Same scene ids, same media, same seconds, same map. Only the
frames drawn and the size they are drawn at may differ.

If anything else changed, the check would be about a film nobody is
going to ship, and a "GO" on it would mean nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import re
import sys
import types

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "roadplanner_mcp"
APP = ROOT / "apps" / "roadplanner_renderer" / "src"

_pkg = types.ModuleType("roadplanner_qa_pkg")
_pkg.__path__ = [str(PACKAGE_ROOT)]
sys.modules["roadplanner_qa_pkg"] = _pkg


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"roadplanner_qa_pkg.{name}", PACKAGE_ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


load("trip_film_plan")
qa = load("qa_excerpt")
protocol = load("renderer_app_protocol")
NOW = datetime(2026, 8, 12, 9, 0, 0, tzinfo=timezone.utc)


def _plan(days: int = 12) -> dict:
    scenes = [
        {"type": "intro", "chapter_id": "", "frames": 150},
        {"type": "crew", "chapter_id": "", "frames": 120},
        {"type": "map_start", "chapter_id": "", "frames": 120},
    ]
    for day in range(days):
        chapter_id = f"d{day}"
        scenes.append({"type": "chapter_card", "chapter_id": chapter_id, "frames": 90})
        scenes.append({"type": "map_leg", "chapter_id": chapter_id, "frames": 190})
        for _ in range(4):
            scenes.append({"type": "collage", "chapter_id": chapter_id, "frames": 140})
        if day % 3 == 1:
            scenes.append({"type": "hero", "chapter_id": chapter_id, "frames": 110})
        if day % 4 == 2:
            scenes.append({"type": "clip", "chapter_id": chapter_id, "frames": 170})
        if day % 5 == 3:
            scenes.append({"type": "text", "chapter_id": chapter_id, "frames": 110})
    scenes.append({"type": "outro_collage", "chapter_id": "", "frames": 180})
    scenes.append({"type": "outro", "chapter_id": "", "frames": 150})
    return {
        "plan_version": 1,
        "fps": 30,
        "total_frames": sum(int(s["frames"]) for s in scenes),
        "scenes": scenes,
    }


def verify_the_excerpt_lasts_between_sixty_and_ninety_seconds() -> None:
    found = qa.excerpt_range(_plan())
    assert qa.QA_MIN_SECONDS <= found["seconds"] <= qa.QA_MAX_SECONDS, found


def verify_it_is_not_simply_the_first_minute() -> None:
    """§22 warns against it, and a film's opening is unrepresentative.

    The score decides rather than a rule against minute zero: an opening
    that genuinely contained everything would be a fine excerpt. On a
    real twelve-minute shape it does not, so it does not win.
    """
    found = qa.excerpt_range(_plan())
    assert found["start_seconds"] > 30, found
    assert found["score"] and found["score"] > 0, found


def verify_a_rich_opening_still_does_not_win() -> None:
    """The real film's answer, which the synthetic plan above hid.

    On the first real trip this ran against, the opening minute happened
    to contain all seven ingredients - so it scored the maximum, as did
    many later windows, and "earliest wins" handed back minute zero on
    every run. The check above passed the whole time, because its
    invented plan had a poorer opening.

    So this builds the shape that actually occurred: everything worth
    scoring, right at the front.
    """
    def _rich(chapter_id: str) -> list[dict]:
        """Every ingredient, inside one window's worth of frames."""
        return [
            {"type": "chapter_card", "chapter_id": chapter_id, "frames": 90},
            {"type": "map_leg", "chapter_id": chapter_id, "frames": 190},
            {"type": "hero", "chapter_id": chapter_id, "frames": 110},
            {"type": "clip", "chapter_id": chapter_id, "frames": 170},
            {"type": "collage", "chapter_id": chapter_id, "frames": 140},
            {"type": "text", "chapter_id": chapter_id, "frames": 110},
        ]

    plain = [{"type": "collage", "chapter_id": "fill", "frames": 140}] * 12
    scenes = (
        [{"type": "intro", "chapter_id": "", "frames": 90}]
        + _rich("d0")
        + plain
        + _rich("d6")
        + plain
        + [{"type": "outro", "chapter_id": "", "frames": 150}]
    )
    plan = {
        "plan_version": 1,
        "fps": 30,
        "total_frames": sum(int(s["frames"]) for s in scenes),
        "scenes": scenes,
    }
    found = qa.excerpt_range(plan)
    # Two windows reach the same top score. The tie is the whole test.
    assert all(found["contains"].values()), found
    assert found["start_frame"] > 0, (
        "der Ausschnitt ist wieder der Filmanfang - genau die eine Minute, "
        "die den Film am schlechtesten vertritt"
    )
    # The rule is about the window's MIDDLE, not where it starts: an
    # excerpt centred on the film sits well before half-time, and the
    # ending is as unrepresentative as the opening. Within a tenth of
    # the film, so a coarse scene grid does not make this brittle.
    middle = found["film_seconds"] / 2
    centre = found["start_seconds"] + found["seconds"] / 2
    assert abs(centre - middle) <= found["film_seconds"] / 10, found


def verify_the_window_never_splits_a_scene() -> None:
    """A photograph fading in and never landing reads as a broken film."""
    plan = _plan()
    found = qa.excerpt_range(plan)
    boundaries = {0}
    cursor = 0
    for scene in plan["scenes"]:
        cursor += int(scene["frames"])
        boundaries.add(cursor)
    assert found["start_frame"] in boundaries, found
    assert found["end_frame"] + 1 in boundaries, found


def verify_the_same_film_always_yields_the_same_window() -> None:
    plan = _plan()
    assert qa.excerpt_range(plan) == qa.excerpt_range(plan)


def verify_a_day_and_a_time_can_be_asked_for() -> None:
    plan = _plan()
    by_day = qa.excerpt_range(plan, chapter_id="d7")
    assert by_day["chapter_ids"][0] == "d7", by_day
    assert by_day["reason"] == "gewählter Tag"
    by_time = qa.excerpt_range(plan, start_seconds=200.0)
    assert 190 <= by_time["start_seconds"] <= 215, by_time


def verify_a_time_past_the_end_clamps_instead_of_resetting() -> None:
    """Silently answering "minute zero" would look like it was honoured."""
    plan = _plan()
    found = qa.excerpt_range(plan, start_seconds=99999.0)
    assert found["start_seconds"] > 0, found
    assert found["start_seconds"] + found["seconds"] <= found["film_seconds"] + 0.1


def verify_an_unknown_day_is_refused_rather_than_guessed() -> None:
    try:
        qa.excerpt_range(_plan(), chapter_id="gibt-es-nicht")
    except qa.QaExcerptError:
        return
    raise AssertionError("ein unbekannter Tag haette abgelehnt werden muessen")


def verify_what_could_not_be_weighed_is_named() -> None:
    """The scene plan carries no orientation, so nothing balanced it.

    Reporting a "representative" excerpt while silently ignoring half of
    what the brief asked for is an absent answer dressed as a state.
    """
    found = qa.excerpt_range(_plan())
    assert "portrait_landscape_mix" in found["unscored"], found
    plan_source = (PACKAGE_ROOT / "trip_film_plan.py").read_text(encoding="utf-8")
    assert '"orientation"' not in plan_source, (
        "der Szenenplan führt jetzt eine Ausrichtung - dann kann sie auch "
        "gewichtet werden, statt als ungewichtet gemeldet zu werden"
    )


def verify_a_short_film_is_its_own_excerpt() -> None:
    """Refusing here would make the check impossible for a test film."""
    short = {
        "plan_version": 1,
        "fps": 30,
        "total_frames": 300,
        "scenes": [{"type": "intro", "chapter_id": "", "frames": 300}],
    }
    found = qa.excerpt_range(short)
    assert found["start_frame"] == 0
    assert found["end_frame"] == 299, found


def verify_the_frame_range_reaches_the_renderer_as_two_numbers() -> None:
    """No second kind of job: the same film, fewer frames."""
    job = protocol.build_job(
        job_id=protocol.new_job_id(),
        action=protocol.ACTION_RENDER_TRIP_FILM,
        message="Prüfausschnitt",
        now=NOW,
        ttl_seconds=protocol.FILM_JOB_TTL_SECONDS,
        render_profile="high_quality",
        frame_range=(900, 3600),
    )
    assert job["action"] == protocol.ACTION_RENDER_TRIP_FILM, job
    assert job["input"]["frame_start"] == 900
    assert job["input"]["frame_end"] == 3600
    assert job["input"]["render_profile"] == "high_quality"
    # Still no path anywhere in a job.
    flat = repr(job)
    for forbidden in ("/", "\\", "http", ".mp4"):
        assert forbidden not in flat, flat


def verify_a_broken_range_is_refused() -> None:
    for bad in ((-1, 100), (500, 400)):
        try:
            protocol.build_job(
                job_id=protocol.new_job_id(),
                action=protocol.ACTION_RENDER_TRIP_FILM,
                message="x",
                now=NOW,
                frame_range=bad,
            )
        except protocol.RendererProtocolError:
            continue
        raise AssertionError(f"{bad} wurde akzeptiert")


def verify_the_renderer_draws_only_that_window() -> None:
    """And measures the excerpt against its own length, not the film's."""
    render = (APP / "render.mjs").read_text(encoding="utf-8")
    assert "frameRange" in render
    assert "...(frameRange ? { frameRange } : {})" in render, (
        "der Renderer zeichnet weiterhin den ganzen Film"
    )
    # The expectation follows the window - an excerpt checked against the
    # film's length would be refused every single time.
    assert "frameRange[1] - frameRange[0] + 1" in render
    worker = (APP / "protocol.mjs").read_text(encoding="utf-8")
    assert "frame_start" in worker and "frame_end" in worker
    # Half a request is refused rather than completed by guessing.
    assert "Bildbereich unvollständig" in worker


def verify_the_excerpt_uses_the_same_package_as_a_full_render() -> None:
    """The whole point: evidence about the film somebody will ship.

    Building a smaller package for the check would make it a different
    film - fewer photographs, a different plan, different timing.
    """
    export = (PACKAGE_ROOT / "trip_film_export.py").read_text(encoding="utf-8")
    body = export.split("async def async_submit(", 1)[1].split("\n    async def ", 1)[0]
    # The excerpt is computed FROM the finished package's scene plan,
    # after it has been built exactly as always.
    build_at = body.index("build_film_package(")
    excerpt_at = body.index("excerpt_range(")
    assert build_at < excerpt_at, (
        "der Ausschnitt wird bestimmt, bevor das Paket gebaut ist - dann "
        "ist es nicht mehr derselbe Film"
    )
    assert 'excerpt_range(\n                    package["scene_plan"]' in body, body[
        excerpt_at - 200 : excerpt_at + 200
    ]


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("QA excerpt tests passed.")


if __name__ == "__main__":
    main()
