"""The music goes on the film last, and it goes on all of it.

Two questions, and this project has already answered both wrongly.

**The order.** Music that travels in the render package has to be
ordered for a film nobody has seen, priced against an ESTIMATED length,
and changing it means rendering again - ninety minutes at 1440p. The
module that fixes that was written, documented and tested, and then
never wired to anything: no import, no job, not even a line in the
Dockerfile. Its own test passed the whole time, because it tested the
module rather than the chain. So the checks here are about the CHAIN.

**The whole film.** The planner was raised to eight sections while the
package builder still sliced the list at four. Both deployables carried
the same 4, so they agreed with each other perfectly - and a
twelve-minute film went silent for its last two and a half minutes,
after the fifth generation had been paid for. The number that decides is
the PLANNER's, and that is what gets read here.
"""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
from pathlib import Path
import re
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "roadplanner_mcp"
APP = ROOT / "apps" / "roadplanner_renderer"
sys.dont_write_bytecode = True

_PACKAGE = "roadplanner_mux_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(PACKAGE_ROOT)]
sys.modules[_PACKAGE] = _root


def _module(name: str, **attributes) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules.setdefault(name, module)
    return module


_module("homeassistant").__path__ = []
_module("homeassistant.core", HomeAssistant=object, callback=lambda fn: fn)
_module("homeassistant.exceptions", HomeAssistantError=type("E", (Exception,), {}))

music = importlib.import_module(f"{_PACKAGE}.trip_film_music")
plan = importlib.import_module(f"{_PACKAGE}.trip_film_music_plan")
profiles = importlib.import_module(f"{_PACKAGE}.render_profiles")

RENDER_SOURCE = (APP / "src" / "render.mjs").read_text(encoding="utf-8")
INDEX_SOURCE = (APP / "src" / "index.mjs").read_text(encoding="utf-8")
PROTOCOL_MJS = (APP / "src" / "protocol.mjs").read_text(encoding="utf-8")
DOCKERFILE = (APP / "Dockerfile").read_text(encoding="utf-8")
EXPORT_SOURCE = (PACKAGE_ROOT / "trip_film_export.py").read_text(encoding="utf-8")
AUDIOMUX = (APP / "src" / "audiomux.mjs").read_text(encoding="utf-8")


def verify_the_mux_module_is_actually_reachable() -> None:
    """The failure that hid for a whole release: written, never wired.

    Each link is named separately, because the chain broke at the two
    least visible ones - nothing imported it, and the runtime image did
    not even contain the file. A module tested in isolation says nothing
    about either.
    """
    assert 'from "./audiomux.mjs"' in RENDER_SOURCE, (
        "der Renderer importiert das Mux-Modul nicht - es ist wieder tot"
    )
    assert "muxArgs(" in RENDER_SOURCE, RENDER_SOURCE[:0]
    assert "muxFilmMusic" in INDEX_SOURCE, "kein Job ruft das Muxen auf"
    assert "src/audiomux.mjs" in DOCKERFILE, (
        "audiomux.mjs wird nicht ins Laufzeit-Image kopiert - der Job "
        "scheitert erst zur Laufzeit mit 'Cannot find module'"
    )
    assert '"add_music"' in PROTOCOL_MJS, "der Renderer kennt die Aktion nicht"


def verify_every_planned_section_survives_to_the_renderer() -> None:
    """The number that decides is the planner's, not either copy of it.

    Comparing the two deployables to each other would have found them in
    perfect agreement on 4 while the planner produced 5 - so this reads
    the planner and then both sides.
    """
    planner_max = plan.MAX_SECTIONS
    assert music.MAX_MUSIC_SECTIONS >= planner_max, (
        f"der Planer kann {planner_max} Abschnitte planen, das Paket nimmt "
        f"nur {music.MAX_MUSIC_SECTIONS} - der Rest des Films wäre still"
    )
    for line in PROTOCOL_MJS.splitlines():
        if line.startswith("export const MAX_MUSIC_SECTIONS"):
            renderer_max = int(line.split("=")[1].strip().rstrip(";"))
            break
    else:  # pragma: no cover - the constant must exist
        raise AssertionError("MAX_MUSIC_SECTIONS fehlt im Renderer")
    assert renderer_max >= planner_max, (
        f"der Renderer nimmt {renderer_max} Abschnitte, geplant werden bis zu "
        f"{planner_max}"
    )
    assert renderer_max == music.MAX_MUSIC_SECTIONS, (
        "die beiden Deployables sind sich uneinig, wie viele Abschnitte ein "
        "Film haben darf"
    )


def verify_a_long_film_keeps_its_music_to_the_last_second() -> None:
    """The concrete failure, measured rather than described.

    Twelve minutes planned five sections; four arrived; the film went
    quiet at 9:54 and stayed quiet for 2:29 - and the fifth generation
    had been paid for.
    """
    for film_seconds in (443.0, 743.0, 900.0):
        sections = plan.plan_sections(
            film_seconds=film_seconds, track_seconds=180.0, chapters=None
        )
        assert len(sections) <= music.MAX_MUSIC_SECTIONS, (
            f"{film_seconds} s planen {len(sections)} Abschnitte"
        )
        last = sections[-1]
        end = last["start_seconds"] + last["seconds"]
        assert end >= film_seconds - 0.01, (
            f"die Musik endet bei {end} s, der Film bei {film_seconds} s"
        )


def verify_too_many_sections_are_refused_rather_than_sliced() -> None:
    """A dropped tail is silence nobody is told about.

    Slicing produced a film that was quiet at the end and said nothing
    about why - the single hardest kind of fault to find, because
    everything reports success.
    """
    timeline = [
        {"name": f"section-{index}.mp3", "start_seconds": index * 60.0, "seconds": 60.0}
        for index in range(music.MAX_MUSIC_SECTIONS + 1)
    ]
    try:
        music.build_music_timeline_package(timeline, root="/nonexistent-music-root")
    except music.MusicPackageError as err:
        assert str(music.MAX_MUSIC_SECTIONS) in str(err), err
        return
    raise AssertionError("zu viele Abschnitte wurden stillschweigend abgeschnitten")


def verify_the_music_planner_is_actually_reached() -> None:
    """Built, tested, and connected to nothing. Twice in one block.

    The cue sheet, the model brief and the proposal validator all
    existed and all had passing tests, and the section boundaries were
    still placed by arithmetic on every run - because no caller ever
    passed a scene plan, so the director returned None before doing
    anything. The panel said so, honestly, in a line nobody had reason
    to read: "Abschnitte geplant von - der Verteilung im Film".

    The same shape as the mux above. A module tested in isolation says
    nothing about whether anything calls it, so the chain gets checked.
    """
    export = (PACKAGE_ROOT / "trip_film_export.py").read_text(encoding="utf-8")
    # The plan is handed back rather than measured and dropped. It used
    # to be built, its length taken, and the plan itself discarded.
    assert "async def async_estimate_plan(" in export, export[:0]
    body = export.split("async def async_estimate_plan(", 1)[1]
    body = body.split("\n    async def ", 1)[0]
    assert "return plan_seconds(plan), plan" in body, body

    panel = (PACKAGE_ROOT / "panel.py").read_text(encoding="utf-8")
    for action in ("story_film_music_offer", "story_film_music_generate"):
        block = panel.split(f'if action == "{action}":', 1)[1]
        block = block.split('\n    if action == "', 1)[0]
        assert "async_estimate_plan(" in block, (
            f"{action} holt weiterhin nur die Länge - der Planer sieht keinen "
            "Szenenplan und die Arithmetik entscheidet"
        )
        assert "scene_plan=scene_plan" in block, block

    # And the two must agree: a price accepted for one set of sections
    # must buy that set, not another.
    offer = panel.split('if action == "story_film_music_offer":', 1)[1]
    offer = offer.split('\n    if action == "', 1)[0]
    generate = panel.split('if action == "story_film_music_generate":', 1)[1]
    generate = generate.split('\n    if action == "', 1)[0]
    for block in (offer, generate):
        assert "film_seconds=seconds" in block, block

    # The RENDER path no longer reads the timeline at all: the generated
    # score never travels in the package. This assertion used to demand
    # the opposite - and the call it demanded had its arguments scrambled
    # and crashed on every attempt, which is how the render-time branch
    # was found to have no users. The question "does the film get the
    # score that was planned and paid for?" is answered by the mux, which
    # fits the timeline to the film's MEASURED length.
    music_body = export.split("async def _async_music(", 1)[1]
    music_body = music_body.split("\n    async def ", 1)[0]
    assert "async_estimate_plan(" not in music_body, (
        "der Renderpfad plant wieder Musik ins Paket - Musik kommt zuletzt"
    )
    add_body = export.split("async def async_add_music(", 1)[1]
    add_body = add_body.split("\n    async def ", 1)[0]
    # Looked up the way the OFFER looked: estimated length plus the same
    # scene plan. The sections on disk are keyed by that plan; asking
    # with the measured length or without the plan reproduces a different
    # plan and reports paid music as never generated. The measured length
    # is used for FITTING - sections starting after the real film end are
    # dropped, not sent to a renderer that would refuse the whole mux.
    assert "self._music_timeline(trip_id, estimated, scene_plan)" in add_body, (
        "der Mux fragt die Timeline nicht mehr so ab, wie das Angebot "
        "gefragt hat - bezahlte Abschnitte würden als nie erzeugt gemeldet"
    )
    assert "async_estimate_plan(trip_id)" in add_body, add_body[:0]
    assert '< seconds' in add_body, (
        "Abschnitte hinter dem gemessenen Filmende werden nicht mehr "
        "aussortiert - der Renderer verweigert dann den ganzen Mux"
    )


def verify_the_film_export_still_has_no_path_to_a_paid_call() -> None:
    """The mux reads what exists. It never orders anything.

    The whole architecture rests on this: rendering, and now vertonen,
    are free. `async_add_music` reaches the same injected reading
    question `_async_music` uses - "what exists, and when does it play?"
    - and never the music service.
    """
    for spender in ("async_generate", "TripFilmMusicService", "Lyria", "lyria"):
        assert spender not in EXPORT_SOURCE, (
            f"der Filmexport erreicht {spender} - Vertonen darf nichts kosten"
        )
    body = EXPORT_SOURCE.split("async def async_add_music(", 1)[1]
    body = body.split("\n    async def ", 1)[0]
    assert "self._music_timeline(" in body, body
    assert "async_submit_add_music_job(" in body, body


def verify_the_score_is_fitted_to_the_measured_film() -> None:
    """The entire reason the music comes last.

    An estimate is what the plan was PRICED against. What the score is
    laid out over has to be the film that exists, or the last section
    ends before or after the last frame.
    """
    body = EXPORT_SOURCE.split("async def async_add_music(", 1)[1]
    body = body.split("\n    async def ", 1)[0]
    assert "_async_source_film_seconds(" in body, "die gemessene Länge wird nicht gelesen"
    assert "async_estimate_seconds" not in body, (
        "die Vertonung benutzt wieder die Schätzung statt der Messung"
    )
    # Where that measurement comes from: the renderer's own result, and -
    # once the exchange folder has aged out - the record of the film that
    # was adopted for the player. Both are measurements of the finished
    # file; neither is the estimate.
    measured = EXPORT_SOURCE.split("async def _async_source_film_seconds(", 1)[1]
    measured = measured.split("\n    async def ", 1)[0]
    assert "async_result(" in measured, measured
    assert "duration_seconds" in measured, measured
    assert "async_estimate_seconds" not in measured, measured
    # A missing measurement is named, not replaced by the estimate.
    assert "keine gemessene Filmlänge" in measured, measured


def verify_the_pictures_are_copied_and_not_re_encoded() -> None:
    """`-c:v copy` is what makes a second soundtrack cheap.

    Re-encoding would cost twenty minutes and a fresh set of compression
    artefacts, and trying another soundtrack would go back to being a
    decision about whether it is worth the evening.
    """
    assert '"-c:v",\n    "copy",' in AUDIOMUX, AUDIOMUX[:0]
    # As an ARGUMENT, which is quoted. Searching the file for the bare
    # word flagged the comment explaining why it is not used - the same
    # mistake as reading a module's own docstring as a rule violation.
    assert '"-shortest"' not in AUDIOMUX, (
        "der Ton darf die Bilder nicht abschneiden - der Film ist der Master"
    )
    # And the renderer proves it afterwards rather than trusting it.
    body = RENDER_SOURCE.split("export async function muxFilmMusic(", 1)[1]
    body = body.split("\n/**", 1)[0]
    assert "facts.width !== source.width" in body, (
        "niemand prüft, ob der Videostream wirklich unangetastet blieb"
    )
    assert "!facts.has_audio" in body, body


def verify_a_film_that_already_has_music_is_refused() -> None:
    """Two soundtracks in one file, the second one inaudible.

    Refused on a MEASUREMENT, not on the presence of a stream. This
    check used to demand the opposite - it required the words "bereits
    eine Tonspur" beside a test of `source.has_audio`, and so it wrote
    the bug down a second time and held it in place. Every Remotion
    render carries an AAC track; an empty one measures about -91 dBFS.
    Counting streams therefore refused every silent excerpt and told the
    user to render a film without an audio stream, which this renderer
    cannot produce.
    """
    body = RENDER_SOURCE.split("export async function muxFilmMusic(", 1)[1]
    body = body.split("\n/**", 1)[0]
    assert "measureVolume(sourcePath)" in body, (
        "der Mux entscheidet wieder nach Streams statt nach Pegel"
    )
    assert "hörbare Tonspur" in body, body
    # Unknown must not refuse. An unmeasurable film is the one case where
    # both answers are guesses, and blocking is the guess that costs the
    # user work already paid for.
    assert "audible === true" in body, (
        "eine ungemessene Quelle darf den Mux nicht blockieren"
    )


def verify_a_silent_mix_is_not_reported_as_ready() -> None:
    """Something has to be audible before the file counts as finished.

    A mux that wrote silence is right in every checkable respect: the
    file has the expected size, length and an audio stream. Exactly that
    file was uploaded and listened to before anybody measured it.
    """
    body = RENDER_SOURCE.split("export async function muxFilmMusic(", 1)[1]
    body = body.split("\n/**", 1)[0]
    assert "measureVolume(partial)" in body, (
        "das Ergebnis wird nicht mehr daraufhin geprüft, ob etwas zu hören ist"
    )
    assert "stumme Tonspur" in body, body


def verify_the_review_copy_carries_the_soundtrack() -> None:
    """The copy somebody sends out is the one with music on it.

    A review of a finished film that is silent cannot answer the one
    question a finished film raises, and the silent cut and the vertonte
    one are the same pictures - nothing in the file would reveal which
    was copied.
    """
    body = INDEX_SOURCE.split("async function produceReviewCopy(", 1)[1]
    body = body.split("\n/**", 1)[0]
    assert "ARTIFACT_FILM_WITH_MUSIC" in body, (
        "die Review-Kopie nimmt weiterhin immer den stummen Film"
    )
    assert "ARTIFACT_TRIP_FILM_VIDEO" in body, "und den stummen, wenn es nur den gibt"


def verify_a_copied_stream_does_not_borrow_the_default_profile_name() -> None:
    """Failure pattern 8, in the place a file gets its name.

    A mux job never chose a profile. Reading the default there would
    save a 1440p film as `...-review-720p.mp4`, and the filename is the
    only thing anybody looks at before sending it on.
    """
    assert profiles.profile_for_size(2560, 1440) == "high_quality"
    assert profiles.profile_for_size(854, 480) == "review_480"
    # An unknown size answers with nothing rather than with a guess.
    assert profiles.profile_for_size(1000, 500) == ""
    assert profiles.profile_for_size(None, None) == ""
    panel = (PACKAGE_ROOT / "panel.py").read_text(encoding="utf-8")
    assert "profile_for_size(" in panel, (
        "der Download-Name fällt weiterhin auf das Standardprofil zurück"
    )


def verify_no_place_or_brand_name_reached_the_new_code() -> None:
    """The house rule, checked where new prose was written."""
    module = ast.parse((PACKAGE_ROOT / "trip_film_music.py").read_text(encoding="utf-8"))
    # Whole words. A substring match reported "udio" inside "Audio" - a
    # test that fails on the thing it is about is worse than none.
    forbidden = {"spotify", "youtube", "suno", "udio", "apple"}
    for node in ast.walk(module):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            words = set(re.findall(r"[a-zä-ü]+", node.value.lower()))
            found = words & forbidden
            assert not found, f"{found} steht in einer Regel"


def verify_the_scored_film_says_it_has_music() -> None:
    """M-1 (#378): the mux measures the level and used to throw it away.

    `player_film` reads exactly one field - `has_audible_audio` - and
    only the RENDER ever set it. So a film with a -7,4 dBFS soundtrack
    came back as `bool(None)` and the card announced "ohne Musik" over a
    file that had just been measured at practically its own target. The
    mux knew, it measured, and it did not say.
    """
    body = RENDER_SOURCE.split("export async function muxFilmMusic(", 1)[1]
    body = body.split("\n/**", 1)[0]
    facts = body.split("return {", 1)[1]
    assert "has_audible_audio: isAudible(heard) === true" in facts, facts[:600]
    assert "audio_peak_dbfs" in facts, "the number behind the answer travels too"
    # From the measurement this job already makes, not a second one.
    assert body.count("measureVolume(partial)") == 1, body.count("measureVolume(partial)")
    # And the reader still reads that one field, so the two agree.
    player = (PACKAGE_ROOT / "player_film.py").read_text(encoding="utf-8")
    assert 'facts.get("has_audible_audio")' in player, "the reader and the writer must name the same field"


def verify_the_scored_film_carries_the_profile_it_was_rendered_at() -> None:
    body = RENDER_SOURCE.split("export async function muxFilmMusic(", 1)[1]
    body = body.split("\n/**", 1)[0]
    assert "profileForSize(facts.width, facts.height)" in body, (
        "the mux result had an empty render profile and the download route "
        "reconstructed it on its own"
    )
    profiles = (APP / "src" / "render_profiles.mjs").read_text(encoding="utf-8")
    assert "export function profileForSize(" in profiles, (
        "the reconstruction belongs in the file that owns the table"
    )


def verify_a_section_may_repeat_itself_to_reach_the_films_end() -> None:
    """M-3 (#378): the plan is built against an estimate, the film is longer."""
    audiomux = AUDIOMUX
    assert "function sectionInputArgs(" in audiomux
    assert '"-stream_loop", "-1"' in audiomux, (
        "looping has to be an INPUT option, before the -i it belongs to"
    )
    # Bounded by the trim that was already there, so an endless input
    # cannot run away.
    assert "atrim=0:" in audiomux
    assert audiomux.count('args.push(...sectionInputArgs(section))') == 2, (
        "both the mux and the measurement build their inputs the same way"
    )


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Film music mux tests passed.")


if __name__ == "__main__":
    main()
