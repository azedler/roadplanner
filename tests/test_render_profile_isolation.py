"""A profile decides pixels. Nothing else may ever depend on it.

This is the promise the whole feature rests on: a review copy is only
evidence about the film if the small render and the large one are the
same film - the same scenes in the same order, the same photographs, the
same clips, the same seconds. The moment one module asks "which profile
is this?" and answers differently, judging a cut at 480p stops telling
anybody anything about the 1440p version, and nothing about either file
would reveal it.

So this test does not check that the plans happen to match today. It
checks that the modules which DECIDE what is in a film cannot see the
profile at all, and that the modules which do see it use it for nothing
but size.
"""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"
RENDERER = ROOT / "apps" / "roadplanner_renderer" / "src"

# Everything that decides what a film CONTAINS. If any of these learns
# about render profiles, the same trip stops producing the same film.
CONTENT_MODULES = (
    "trip_film_plan.py",
    "trip_film_package.py",
    "film_photo_allocation.py",
    "visual_prominence.py",
    "video_orchestration.py",
    "trip_film_music_plan.py",
)


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"roadplanner_{name}", INTEGRATION / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


protocol = load("renderer_app_protocol")
NOW = datetime(2026, 8, 11, 9, 0, 0, tzinfo=timezone.utc)


def verify_no_content_module_can_see_a_profile() -> None:
    """The decisive check, and the cheapest one to keep."""
    for name in CONTENT_MODULES:
        path = INTEGRATION / name
        assert path.is_file(), f"{name} gibt es nicht mehr - Test anpassen"
        text = path.read_text(encoding="utf-8")
        for forbidden in ("render_profiles", "render_profile", "RENDER_PROFILES"):
            assert forbidden not in text, (
                f"{name} liest das Renderprofil ({forbidden}). Damit hängt der "
                "Inhalt des Films an seiner Größe, und eine Review-Kopie sagt "
                "nichts mehr über den echten Film aus."
            )


def verify_the_export_passes_the_profile_without_consulting_it() -> None:
    """The one integration module that knows the profile may only forward it.

    It is allowed to name the profile twice: once to reject an unknown id,
    once to hand it to the submit. What it must not do is branch on WHICH
    profile - the manifest, the budget, the clips and the photographs are
    the same either way.
    """
    text = (INTEGRATION / "trip_film_export.py").read_text(encoding="utf-8")
    assert "profile_id=profile_id" in text, "das Profil wird gar nicht übergeben"
    # No comparison against a specific profile id anywhere in the export.
    for name in ("review_480", "review_720", "full_hd", "high_quality", "uhd_4k"):
        assert name not in text, (
            f"trip_film_export.py nennt {name} - der Export darf keine "
            "Fallunterscheidung nach Profil kennen"
        )


def verify_the_composition_reads_the_profile_in_exactly_one_place() -> None:
    """A layout that reads it would draw differently at different sizes."""
    film = (RENDERER / "remotion" / "RoadplannerTripFilm.tsx").read_text(encoding="utf-8")
    # Declared as a prop, and never read in the component body: the size
    # is resolved by calculateMetadata before the component ever runs.
    assert "renderProfile?: string | null;" in film
    uses = [
        line
        for line in film.splitlines()
        if "renderProfile" in line and "renderProfile?:" not in line
    ]
    assert not uses, f"die Filmkomponente liest das Profil: {uses}"

    root = (RENDERER / "remotion" / "Root.tsx").read_text(encoding="utf-8")
    assert "renderProfile(props.renderProfile)" in root, (
        "die Komposition leitet ihre Größe nicht mehr aus dem Profil ab"
    )


def verify_a_profile_cannot_change_the_length_of_a_plan() -> None:
    """The frame count comes from the scene plan, never from the profile."""
    root = (RENDERER / "remotion" / "Root.tsx").read_text(encoding="utf-8")
    # The film's own calculateMetadata, not the trip day's above it.
    block = root.split("RoadplannerTripFilmProps }")[-1]
    duration = re.search(r"durationInFrames: (.+?),\n", block, re.S)
    assert duration, "Root.tsx berechnet keine Filmlänge mehr"
    assert "filmDurationInFrames(props.scenes" in duration.group(1), duration.group(1)
    assert "profile" not in duration.group(1), duration.group(1)


def verify_two_jobs_differ_only_in_their_profile() -> None:
    """The same trip at two sizes must produce two jobs that agree."""
    job_id = protocol.new_job_id()
    small = protocol.build_job(
        job_id=job_id,
        action=protocol.ACTION_RENDER_TRIP_FILM,
        message="Reisefilm",
        now=NOW,
        ttl_seconds=protocol.FILM_JOB_TTL_SECONDS,
        render_profile="review_480",
    )
    large = protocol.build_job(
        job_id=job_id,
        action=protocol.ACTION_RENDER_TRIP_FILM,
        message="Reisefilm",
        now=NOW,
        ttl_seconds=protocol.FILM_JOB_TTL_SECONDS,
        render_profile="high_quality",
    )
    differences = {
        key
        for key in set(small) | set(large)
        if small.get(key) != large.get(key)
    }
    assert differences == {"input"}, differences
    assert small["input"]["message"] == large["input"]["message"]
    assert small["input"]["render_profile"] == "review_480"
    assert large["input"]["render_profile"] == "high_quality"


def verify_a_job_without_a_profile_says_nothing_rather_than_guessing() -> None:
    """An old client keeps working, and gets what it always got."""
    job = protocol.build_job(job_id=protocol.new_job_id(), now=NOW)
    assert "render_profile" not in job["input"], job["input"]


def verify_a_profile_id_can_never_be_a_path() -> None:
    """It reaches a job file that another container reads."""
    for bad in ("../etc", "a/b", "review 720", "REVIEW", "x" * 40, "-vf"):
        try:
            protocol.build_job(
                job_id=protocol.new_job_id(),
                action=protocol.ACTION_RENDER_TRIP_FILM,
                now=NOW,
                render_profile=bad,
            )
        except protocol.RendererProtocolError:
            continue
        raise AssertionError(f"{bad!r} wurde als Profil akzeptiert")


def verify_a_review_copy_names_a_job_and_never_a_file() -> None:
    """The security property of the review copy, stated as a test.

    There is no filename and no path in this job. Both sides build
    ``results/<job id>/<fixed name>`` from a value matched against the
    job-id pattern, which is why there is nothing to traverse - rather
    than a sanitiser that has to be right every time.
    """
    source = protocol.new_job_id()
    job = protocol.build_job(
        job_id=protocol.new_job_id(),
        action=protocol.ACTION_CREATE_REVIEW_COPY,
        message="Review-Kopie",
        now=NOW,
        render_profile="review_480",
        source_job_id=source,
    )
    assert job["input"]["source_job_id"] == source
    flat = repr(job)
    for forbidden in ("/", "\\", "..", "http", ".mp4", "path"):
        assert forbidden not in flat, f"{forbidden!r} darf nicht im Auftrag stehen: {flat}"


def verify_a_review_copy_refuses_a_source_that_is_not_a_job_id() -> None:
    """Every other value is refused before a path is built from it."""
    for bad in ("", "..", "../../etc/passwd", "roadplanner-trip-film.mp4", "x" * 36):
        try:
            protocol.build_job(
                job_id=protocol.new_job_id(),
                action=protocol.ACTION_CREATE_REVIEW_COPY,
                message="Review-Kopie",
                now=NOW,
                source_job_id=bad,
            )
        except protocol.RendererProtocolError:
            continue
        raise AssertionError(f"{bad!r} wurde als Quelle akzeptiert")


def verify_the_review_copy_reaches_no_provider_and_no_credential() -> None:
    """It works on one local file. Nothing else may appear in that module."""
    text = (RENDERER / "review_copy.mjs").read_text(encoding="utf-8")
    for forbidden in (
        "fetch",
        "http",
        "onedrive",
        "gemini",
        "token",
        "Authorization",
        "process.env",
    ):
        assert forbidden.lower() not in text.lower(), (
            f"review_copy.mjs erwähnt {forbidden!r}"
        )


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Render profile isolation tests passed.")


if __name__ == "__main__":
    main()
