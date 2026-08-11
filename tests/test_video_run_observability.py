"""A long run has to be tellable apart from a stuck one.

Live report, and the reason this file exists: the card said "Die Analyse
läuft · 10 von 21", and three minutes later it still said exactly that.
Nothing was wrong - the first step of a window is downloading a phone
video of a few hundred megabytes - but nothing said so either. From
outside, "working" and "wedged" were the same picture:

- the only log line in the whole run was a FAILURE, so success was
  silence, and a run that was fine looked identical to one that had
  stopped;
- the download was the one slow step in the path with no time limit of
  its own, while ffmpeg and the provider call both had one - so a stream
  that went quiet would have hung until the box was restarted;
- and since a run in flight now refuses a second one, a hang would have
  locked the trip out of analysis for good.

The checks are about those properties, not about a duration.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"

SERVICE = (INTEGRATION / "video_curation_service.py").read_text(encoding="utf-8")
SOURCE = (INTEGRATION / "video_media_source.py").read_text(encoding="utf-8")
PROXY = (INTEGRATION / "video_proxy.py").read_text(encoding="utf-8")
CARD = (
    INTEGRATION / "frontend" / "features" / "story-editor.js"
).read_text(encoding="utf-8")
EXPORT = (INTEGRATION / "trip_film_export.py").read_text(encoding="utf-8")
STORE = (INTEGRATION / "experience_store.py").read_text(encoding="utf-8")


def verify_every_slow_step_states_its_own_limit() -> None:
    """Three steps per window, and each one can stop moving."""
    # ffmpeg
    assert "ANALYSIS_TIMEOUT" in PROXY and "timeout=ANALYSIS_TIMEOUT" in PROXY
    # the download - the one that had none
    assert "CONNECT_TIMEOUT_SECONDS" in SOURCE, "der Download hat keine Zeitgrenze"
    assert "STALL_TIMEOUT_SECONDS" in SOURCE, "ein stehender Stream fällt nicht auf"
    assert "sock_read=" in SOURCE, "eine Gesamtzeit ersetzt keine Stillstandserkennung"


def verify_a_stalled_download_costs_one_recording_and_not_the_run() -> None:
    """The caller catches its own error types, so the new one has to be one.

    Letting an asyncio.TimeoutError out would have skipped past the
    per-window `except` and ended the whole run - so one recording on a
    bad connection would have cost every recording after it.
    """
    assert "except TimeoutError" in SOURCE
    assert "except aiohttp.ClientError" in SOURCE
    # Both are re-raised as this module's own error, which the service
    # catches per window.
    timeout_block = SOURCE.split("except TimeoutError")[1].split("except aiohttp")[0]
    assert "raise ValidationError" in timeout_block, timeout_block
    assert "(RoadplannerError, VideoProxyError, VideoAssetError, OSError)" in SERVICE


def verify_the_run_says_where_it_is() -> None:
    """Not one line at the end - one per step, while it is happening."""
    assert SERVICE.count("_LOGGER.info") >= 4, "der Lauf protokolliert seinen Verlauf nicht"
    for phrase in ("Videoanalyse gestartet", "lade Aufnahme", "Videoanalyse beendet"):
        assert phrase in SERVICE, phrase


def verify_nothing_slow_blocks_the_event_loop() -> None:
    """Hundreds of megabytes, read and deleted on Home Assistant's loop.

    Each of these was a blocking call inside an async function: reading
    the proxy into memory, deleting the work directory, sizing the
    download. On a box that is also running everything else in the house,
    that is a freeze nobody connects to a video analysis.
    """
    tree = ast.parse(SERVICE)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            name = ""
            if isinstance(inner.func, ast.Attribute):
                name = inner.func.attr
            if name not in {"read_bytes", "rmtree", "stat"}:
                continue
            # Fine when handed to the executor: the call is then an
            # argument rather than something awaited on the loop.
            source_line = ast.get_source_segment(SERVICE, inner) or ""
            parent_line = SERVICE.splitlines()[inner.lineno - 1]
            if "async_add_executor_job" in parent_line or "lambda" in parent_line:
                continue
            offenders.append(f"{name} in Zeile {inner.lineno}: {source_line[:60]}")
    assert not offenders, offenders


def verify_the_outcome_of_a_paid_run_outlives_the_connection() -> None:
    """Eleven windows failed and the reasons went with the WebSocket.

    Live report: a run was started, ten minutes passed, the page was
    reloaded - and the card showed "10 im Cache · 11 neu" with nothing
    about the eleven that had just been attempted. The summary existed
    only in the response to the click, so a dropped connection or a
    reload took every failure reason with it, and the person who had
    paid for the run was left to ask a log file.
    """
    assert "def save_video_run" in STORE, "das Ergebnis wird nicht gespeichert"
    # Written in the `finally`, so an interrupted run still leaves a record.
    tail = SERVICE.split("finally:")[-1]
    assert "save_video_run" in tail, "nur ein sauber beendeter Lauf wird berichtet"
    # Named in BOTH places, or `load` returns a key set that silently
    # drops it - which this project has already shipped once.
    assert STORE.count('"video_run"') >= 3, "geschrieben, aber nicht zurückgelesen"
    # The card reads the store, not only the response it may never get.
    assert "offer?.last_run" in CARD
    # And it names the reason rather than only counting failures.
    assert "kein Grund angegeben" in CARD, "eine Zahl ohne Ursache"


def verify_the_video_budget_is_the_same_number_on_both_sides() -> None:
    """Read both files and compare - the only form of this test that works.

    The pictures had a budget from the first day. The video that arrived
    later had none: one clip was capped at 24 MB, which is nearly the
    whole picture budget, and nothing bounded the sum at all. A trip with
    many moments could write an unbounded package into /share on a box
    that has a few gigabytes to spare.
    """
    package = (INTEGRATION / "trip_film_package.py").read_text(encoding="utf-8")
    protocol = (
        ROOT / "apps" / "roadplanner_renderer" / "src" / "protocol.mjs"
    ).read_text(encoding="utf-8")

    def megabytes(body: str, name: str) -> int:
        match = re.search(rf"{name} = (\d+) \* 1024 \* 1024", body)
        assert match, f"{name} fehlt oder ist anders geschrieben"
        return int(match.group(1))

    assert megabytes(package, "MAX_FILM_VIDEO_BYTES") == megabytes(
        protocol, "MAX_FILM_VIDEO_BYTES"
    ), "die beiden Deployables rechnen mit verschiedenen Videobudgets"
    # And each side enforces it, rather than only declaring it.
    assert "total_clip_bytes > MAX_FILM_VIDEO_BYTES" in package
    assert "totalBytes > MAX_FILM_VIDEO_BYTES" in protocol


def verify_a_film_that_cannot_start_says_so_on_the_card() -> None:
    """Live report: "Aktuell passiert nichts beim Drücken von Film erzeugen."

    It was one line: `if (!result?.renderer_app_job?.job_id) return;`.
    `_runAction` returns null for EVERY failure and shows a toast that is
    gone in seconds, so a render that could not start looked exactly like
    a button that does nothing - and the reasonable reaction to a button
    that does nothing is to press it again.

    Third time in this project that a failure was reported by saying
    nothing, so it gets a check rather than a comment.
    """
    assert "_storyFilmStartError" in CARD, "der stumme Rückweg ist wieder da"
    # Shown on the card, not only in a toast that expires.
    assert "startError" in CARD
    # And cleared once a render is actually running, or it would linger
    # over a film that is working.
    assert "running ? \"\" : String(this._storyFilmStartError" in CARD


def verify_the_film_says_it_is_being_prepared_before_any_progress() -> None:
    """Minutes pass between the click and the first progress number.

    Live report: "Es hatte nur sehr lange gedauert bis er das Rendering
    angefangen hat." Nothing was broken - between the click and the first
    number lies the whole package build: a couple of hundred photographs
    fetched and shrunk, every recording a clip comes from downloaded and
    cut, the package written. In silence, that is indistinguishable from
    a dead button.

    Same shape as the analysis run and the failed start: the fix is not
    to make it faster, it is to say what is happening.
    """
    assert "_storyFilmPreparing" in CARD, "der Klick bleibt bis zum Job stumm"
    assert "Der Film wird vorbereitet" in CARD
    # Cleared once the job exists, or it would sit over a running render.
    assert "this._storyFilmPreparing = false;" in CARD
    # And a second press during the preparation would build the whole
    # package twice, so the button is out of reach while it runs.
    assert "!running && !preparing" in CARD


def verify_a_recording_is_dropped_as_soon_as_nothing_needs_it() -> None:
    """Peak disk, not total: /share holds one recording, not all of them."""
    assert "def _async_release" in SERVICE, "die Aufnahmen bleiben bis zum Schluss liegen"
    assert "pending" in SERVICE
    # The film export downloads the same recordings again for cutting.
    assert "still_needed" in EXPORT, "der Filmexport hält alle Originale bis zum Ende"


def verify_the_counter_does_not_credit_this_run_with_older_answers() -> None:
    """"10 von 21 fertig" read as "ten done in a second". Ten were older."""
    assert "insgesamt" in CARD, "der Zähler behauptet weiter, es sei dieser Lauf"
    assert "dieser Lauf seit" in CARD


def verify_the_film_export_survives_a_missing_media_source() -> None:
    """One reach into a private attribute, one dead film export.

    The clip step called `service._media_source.async_download_to(...)`
    inside a loop whose `except` lists RoadplannerError, VideoProxyError
    and OSError. On a configuration with no media source that attribute
    is None, so the call raises AttributeError - which is none of those
    three, and would have ended the whole export. Not "the film has no
    clips": no film.
    """
    assert "service._media_source" not in EXPORT, "greift weiter privat zu"
    assert 'getattr(service, "media_source", None)' in EXPORT
    assert "der Film läuft ohne Clips" in EXPORT, "die Lücke wird nicht benannt"
    # And the accessor exists on the real class, rather than only here.
    assert "def media_source" in SERVICE


def verify_the_clip_step_does_not_block_the_event_loop() -> None:
    """Same class of problem as the analysis run, one file over."""
    clips = EXPORT.split("async def _async_clips")[1].split("\n    async def ")[0]
    for line in clips.splitlines():
        stripped = line.strip()
        if stripped.startswith("raw = ") and "read_bytes" in stripped:
            assert "async_add_executor_job" in clips
        if "shutil.rmtree" in stripped:
            assert "async_add_executor_job" in clips, stripped


for check in (
    verify_every_slow_step_states_its_own_limit,
    verify_the_film_export_survives_a_missing_media_source,
    verify_the_clip_step_does_not_block_the_event_loop,
    verify_a_stalled_download_costs_one_recording_and_not_the_run,
    verify_the_run_says_where_it_is,
    verify_nothing_slow_blocks_the_event_loop,
    verify_the_counter_does_not_credit_this_run_with_older_answers,
    verify_the_outcome_of_a_paid_run_outlives_the_connection,
):
    check()

print("Video run observability tests passed.")
