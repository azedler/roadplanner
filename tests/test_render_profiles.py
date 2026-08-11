"""One profile table, two deployables - so read both and compare them.

This project's oldest and most expensive bug is a single number that
exists in the integration and in the renderer, and gets raised on one
side only. It has happened four times: the photo regex (4 -> 10 -> 20),
the images per chapter (10 -> 14), the total images (180 -> 260). Each
time the two halves disagreed silently and the film came out wrong in a
way nothing in either codebase looked broken.

A render profile is exactly that shape of risk again, and worse: a
profile that means 1440p on the integration side and 1080p on the
renderer side produces a perfectly successful render at a size nobody
asked for. Nothing fails, nothing logs, and the only symptom is a file
that looks slightly soft on a television.

So this test does not write the table down a third time. It reads
`custom_components/roadplanner_mcp/render_profiles.py` and
`apps/roadplanner_renderer/src/render_profiles.mjs` and compares them.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PYTHON_TABLE = ROOT / "custom_components" / "roadplanner_mcp" / "render_profiles.py"
JS_TABLE = ROOT / "apps" / "roadplanner_renderer" / "src" / "render_profiles.mjs"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


profiles = load(PYTHON_TABLE, "roadplanner_render_profiles")
JS = JS_TABLE.read_text(encoding="utf-8")


def _js_number(block: str, key: str) -> int:
    """A number, or the one constant the table is allowed to refer to.

    `fps: FILM_FPS` is deliberate - the frame rate must not be typed five
    times - so reading only literals here would report a missing field
    where the real answer is "thirty, said once".
    """
    match = re.search(rf"\b{key}:\s*(\d+|FILM_FPS)", block)
    assert match, f"{key} fehlt im Renderer-Profil"
    if match.group(1) == "FILM_FPS":
        return int(re.search(r"export const FILM_FPS = (\d+)", JS).group(1))
    return int(match.group(1))


def _js_string(block: str, key: str) -> str:
    match = re.search(rf'\b{key}:\s*"([^"]*)"', block)
    assert match, f"{key} fehlt im Renderer-Profil"
    return match.group(1)


def _js_bool(block: str, key: str) -> bool:
    match = re.search(rf"\b{key}:\s*(true|false)", block)
    assert match, f"{key} fehlt im Renderer-Profil"
    return match.group(1) == "true"


def _js_blocks() -> dict[str, str]:
    """Every profile in the renderer's table, as its raw source block."""
    table = re.search(
        r"export const RENDER_PROFILES = \{(.*?)\n\};", JS, re.S
    )
    assert table, "der Renderer hat kein RENDER_PROFILES mehr"
    body = table.group(1)
    blocks: dict[str, str] = {}
    for match in re.finditer(r"\n  ([a-z0-9_]+): \{(.*?)\n  \},", body, re.S):
        blocks[match.group(1)] = match.group(2)
    return blocks


def verify_both_sides_know_the_same_profiles() -> None:
    """Neither side may carry a profile the other has never heard of."""
    blocks = _js_blocks()
    assert set(blocks) == set(profiles.RENDER_PROFILES), (
        "Die Profiltabellen unterscheiden sich: "
        f"nur Python {sorted(set(profiles.RENDER_PROFILES) - set(blocks))}, "
        f"nur Renderer {sorted(set(blocks) - set(profiles.RENDER_PROFILES))}"
    )


def verify_every_profile_means_the_same_size() -> None:
    """The failure this file exists for: one side raised, the other not."""
    blocks = _js_blocks()
    for name, block in blocks.items():
        entry = profiles.RENDER_PROFILES[name]
        for key in ("width", "height", "fps"):
            assert _js_number(block, key) == entry[key], (
                f"{name}.{key}: Renderer {_js_number(block, key)} "
                f"!= Integration {entry[key]}"
            )
        assert _js_string(block, "id") == entry["id"] == name
        # The suffix ends up in a filename on the integration side and
        # names the same thing on the renderer's. A copy called
        # "-1080p" that is 1440p is the same lie in a smaller place.
        assert _js_string(block, "suffix") == entry["suffix"], name
        for key in ("experimental", "recommended"):
            assert _js_bool(block, key) == entry[key], f"{name}.{key}"


def verify_the_design_surface_is_one_size() -> None:
    """The layout is authored once. Both sides must name the same surface."""
    width = int(re.search(r"export const DESIGN_WIDTH = (\d+)", JS).group(1))
    height = int(re.search(r"export const DESIGN_HEIGHT = (\d+)", JS).group(1))
    fps = int(re.search(r"export const FILM_FPS = (\d+)", JS).group(1))
    assert width == profiles.DESIGN_WIDTH, f"{width} != {profiles.DESIGN_WIDTH}"
    assert height == profiles.DESIGN_HEIGHT, f"{height} != {profiles.DESIGN_HEIGHT}"
    assert fps == profiles.FILM_FPS, f"{fps} != {profiles.FILM_FPS}"


def verify_the_frame_rate_is_one_number_in_three_places() -> None:
    """Three files say "thirty", and none of them may say it alone.

    The film PLAN turns seconds into frames, the profile table declares
    the rate, and the composition refuses anything else. If the plan and
    the renderer ever disagreed, every scene would be a fixed fraction too
    long or too short and the film would still render perfectly - only
    the timing of everything in it would be wrong.

    The plan is deliberately not allowed to import the profile table (see
    test_render_profile_isolation), so the numbers are compared here
    instead of being shared.
    """
    plan = (
        ROOT / "custom_components" / "roadplanner_mcp" / "trip_film_plan.py"
    ).read_text(encoding="utf-8")
    match = re.search(r"^FILM_FPS = (\d+)$", plan, re.M)
    assert match, "trip_film_plan.py hat kein FILM_FPS mehr"
    assert int(match.group(1)) == profiles.FILM_FPS, (
        f"Der Filmplan rechnet mit {match.group(1)} fps, "
        f"die Profiltabelle mit {profiles.FILM_FPS}"
    )
    film = (
        ROOT
        / "apps"
        / "roadplanner_renderer"
        / "src"
        / "remotion"
        / "RoadplannerTripFilm.tsx"
    ).read_text(encoding="utf-8")
    # The composition must not carry its own literal. It had one.
    assert not re.search(r"export const FILM_FPS = \d+", film), (
        "die Filmkomponente definiert die Bildrate wieder selbst"
    )
    assert "export const FILM_FPS = PROFILE_FPS;" in film


def verify_the_default_is_the_same_on_both_sides() -> None:
    """A job that says nothing must mean the same thing in both places."""
    match = re.search(r'export const DEFAULT_RENDER_PROFILE = "([a-z0-9_]+)"', JS)
    assert match, "der Renderer hat kein DEFAULT_RENDER_PROFILE mehr"
    assert match.group(1) == profiles.DEFAULT_RENDER_PROFILE
    assert profiles.DEFAULT_RENDER_PROFILE in profiles.RENDER_PROFILES


def verify_one_frame_rate_everywhere() -> None:
    """A profile decides pixels, never timing.

    If a profile could change the frame rate it could change how long a
    plan runs, and the same scene plan would produce two different films.
    That is the one thing this whole feature must not be able to do.
    """
    for name, entry in profiles.RENDER_PROFILES.items():
        assert entry["fps"] == profiles.FILM_FPS, f"{name} hat eine eigene Bildrate"


def verify_a_review_copy_is_only_ever_smaller() -> None:
    """Making a "review copy" in 4K would be a re-encode with no purpose."""
    for name in profiles.REVIEW_COPY_PROFILES:
        entry = profiles.RENDER_PROFILES[name]
        assert entry["width"] <= profiles.DESIGN_WIDTH, name
        assert not entry["experimental"], name
    assert profiles.DEFAULT_REVIEW_PROFILE in profiles.REVIEW_COPY_PROFILES


def verify_the_review_table_matches_the_renderers() -> None:
    """The app refuses what it does not offer; both lists must agree."""
    review = ROOT / "apps" / "roadplanner_renderer" / "src" / "review_copy.mjs"
    text = review.read_text(encoding="utf-8")
    match = re.search(r"export const REVIEW_COPY_PROFILES = \[(.*?)\];", text, re.S)
    assert match, "der Renderer hat keine REVIEW_COPY_PROFILES mehr"
    names = tuple(re.findall(r'"([a-z0-9_]+)"', match.group(1)))
    assert names == tuple(profiles.REVIEW_COPY_PROFILES), (
        f"Renderer {names} != Integration {tuple(profiles.REVIEW_COPY_PROFILES)}"
    )
    default = re.search(r'export const DEFAULT_REVIEW_PROFILE = "([a-z0-9_]+)"', text)
    assert default and default.group(1) == profiles.DEFAULT_REVIEW_PROFILE


def verify_an_unknown_id_answers_with_the_default() -> None:
    """User input reaches this. It must never raise a KeyError."""
    for value in ("", None, "nope", "../etc/passwd", "REVIEW_720"):
        chosen = profiles.render_profile(value)
        assert chosen["id"] == profiles.DEFAULT_RENDER_PROFILE, value


def verify_a_filename_says_which_version_it_is() -> None:
    """A folder of MP4s that are all called the same thing answers nothing."""
    assert profiles.film_filename("Reise 2026", "high_quality") == "Reise-2026-1440p.mp4"
    assert (
        profiles.film_filename("reise", "review_720", source_suffix="1440p")
        == "reise-1440p-review-720p.mp4"
    )
    # Nothing a user typed may become a path.
    made = profiles.film_filename("../../etc/passwd", "full_hd")
    assert "/" not in made and ".." not in made, made
    assert profiles.film_filename("", "full_hd") == "reise-1080p.mp4"


def verify_the_choices_name_their_own_default() -> None:
    """A picker that shows one profile while another renders is invisible."""
    chosen = [entry for entry in profiles.profile_choices() if entry["default"]]
    assert len(chosen) == 1, chosen
    assert chosen[0]["id"] == profiles.DEFAULT_RENDER_PROFILE
    review = [entry for entry in profiles.review_choices() if entry["default"]]
    assert len(review) == 1, review
    assert review[0]["id"] == profiles.DEFAULT_REVIEW_PROFILE
    assert {entry["id"] for entry in profiles.review_choices()} == set(
        profiles.REVIEW_COPY_PROFILES
    )


def verify_the_composition_scales_the_design_surface() -> None:
    """Read the film component: no layout may know the real size.

    The whole architecture rests on one line. If a component started
    reading `width` from `useVideoConfig` for its own layout, a 480p film
    would wrap its headings somewhere else than a 1440p one - and the
    review copy would stop being evidence about the film.
    """
    film = (
        ROOT
        / "apps"
        / "roadplanner_renderer"
        / "src"
        / "remotion"
        / "RoadplannerTripFilm.tsx"
    ).read_text(encoding="utf-8")
    assert "const designScale = Math.min(width / DESIGN_WIDTH, height / DESIGN_HEIGHT);" in film
    # Exactly one place reads the real size, and it is that line.
    reads = re.findall(r"const \{([^}]*)\} = useVideoConfig\(\);", film)
    with_size = [block for block in reads if "width" in block or "height" in block]
    assert len(with_size) == 1, (
        "mehr als eine Komponente liest die echte Rendergröße: " f"{with_size}"
    )


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Render profile tests passed.")


if __name__ == "__main__":
    main()
