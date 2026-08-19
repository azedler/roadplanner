"""The Home Assistant side: what it reports when the app is not there.

Almost every user will never install this app, so the interesting
behaviour is the absence cases. Three of them look similar in the folder
and mean entirely different things, and conflating them would either alarm
people who have nothing wrong or hide a real result.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types

# Loading integration modules by path would otherwise leave a
# __pycache__ inside the shipped integration directory, which the
# repository validator rightly refuses.
sys.dont_write_bytecode = True

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")


def _install_homeassistant_stub() -> None:
    """The client imports HomeAssistant for typing only; stub it."""
    if "homeassistant" in sys.modules:
        return
    homeassistant = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:  # noqa: D401 - stand-in
        """Stand-in for the real class."""

    core.HomeAssistant = HomeAssistant
    components = types.ModuleType("homeassistant.components")
    hassio = types.ModuleType("homeassistant.components.hassio")
    hassio.is_hassio = lambda hass: bool(getattr(hass, "supervisor", False))
    homeassistant.core = core
    homeassistant.components = components
    components.hassio = hassio
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.core": core,
            "homeassistant.components": components,
            "homeassistant.components.hassio": hassio,
        }
    )


_install_homeassistant_stub()


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"roadplanner_{name}", PACKAGE_ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sys.modules["roadplanner_renderer_app_protocol"] = load("renderer_app_protocol")
protocol = sys.modules["roadplanner_renderer_app_protocol"]

# The client imports its protocol as a relative module; give it that name.
spec = importlib.util.spec_from_file_location(
    "renderer_pkg.renderer_app_client", PACKAGE_ROOT / "renderer_app_client.py"
)
package = types.ModuleType("renderer_pkg")
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules["renderer_pkg"] = package
sys.modules["renderer_pkg.renderer_app_protocol"] = protocol
client_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = client_module
spec.loader.exec_module(client_module)


class FakeHass:
    def __init__(self, *, supervisor: bool) -> None:
        self.supervisor = supervisor

    async def async_add_executor_job(self, func, *args):
        return func(*args)


def make_client(tmp: str, *, supervisor: bool = True):
    return client_module.RendererAppClient(
        FakeHass(supervisor=supervisor), exchange_dir=Path(tmp) / "poc-v1"
    )


def verify_no_supervisor_is_the_answer_not_a_fault() -> None:
    """Apps exist only under Home Assistant OS or Supervised.

    On Container or Core no app can ever be installed. Reporting that
    plainly is the whole point - discovering it later as "the app never
    showed up" would waste the user's evening.
    """
    with tempfile.TemporaryDirectory() as tmp:
        result = asyncio.run(make_client(tmp, supervisor=False).async_environment())
    assert result["status"] == client_module.ENV_NO_SUPERVISOR, result
    assert result["ready"] is False
    assert "Supervisor" in result["summary_de"]
    assert "installier" not in result["recommended_next_step_de"].casefold()[:30]


def verify_a_writable_exchange_folder_reports_ready() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp, supervisor=True)
        result = asyncio.run(client.async_environment())
        details = result["details"]
        assert details["exchange_writable"] is True, details
        # The subdirectories both sides rely on must exist afterwards.
        for name in ("jobs", "processing", "status", "results"):
            assert (client.exchange_dir / name).is_dir(), name
        # On a machine without /share the probe stops there, which is
        # correct - it is the next thing that blocks.
        assert result["status"] in {
            client_module.ENV_READY,
            client_module.ENV_SHARE_MISSING,
        }, result["status"]


def verify_a_missing_app_is_not_an_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        status = asyncio.run(make_client(tmp).async_app_status())
    assert status["installed"] is False, status
    assert status["online"] is False
    assert status["reason"], "die Abwesenheit wird erklärt"


def verify_a_corrupt_heartbeat_is_reported_not_raised() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        client.exchange_dir.mkdir(parents=True, exist_ok=True)
        (client.exchange_dir / "renderer-status.json").write_text("{kaputt", encoding="utf-8")
        status = asyncio.run(client.async_app_status())
    assert status["installed"] is True, status
    assert status["online"] is False
    assert "JSON" in status["reason"], status


def verify_a_job_is_written_atomically_and_only_once() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        asyncio.run(client.async_environment())
        job = asyncio.run(client.async_submit_test_job())
        jobs = sorted((client.exchange_dir / "jobs").iterdir())
        assert [p.name for p in jobs] == [f"{job['job_id']}.json"], jobs
        # No temporary file may survive: a leftover .part would be read as
        # nothing by the worker but would clutter a shared folder forever.
        assert not any(p.suffix == ".part" for p in jobs), jobs
        written = json.loads(jobs[0].read_text(encoding="utf-8"))
        protocol.validate_job(written, now=protocol.utc_now())


def verify_an_unknown_job_is_reported_as_unknown() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        asyncio.run(client.async_environment())
        status = asyncio.run(client.async_job_status(protocol.new_job_id()))
    assert status["state"] is None, status
    assert status["terminal"] is False
    assert status["reason"], status


def verify_a_forged_artifact_is_refused() -> None:
    """The hash check has to look at the bytes, not at the claim."""
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        asyncio.run(client.async_environment())
        job_id = protocol.new_job_id()
        folder = client.exchange_dir / "results" / job_id
        folder.mkdir(parents=True, exist_ok=True)
        honest = b"echte Bytes"
        (folder / protocol.ARTIFACT_TEXT).write_bytes(b"andere Bytes")
        (folder / "result.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "protocol_version": 1,
                    "job_id": job_id,
                    "state": "completed",
                    "artifacts": [
                        {
                            "kind": "text",
                            "filename": protocol.ARTIFACT_TEXT,
                            "size_bytes": len(honest),
                            "sha256": hashlib.sha256(honest).hexdigest(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        try:
            asyncio.run(client.async_result(job_id))
        except protocol.RendererProtocolError:
            return
    raise AssertionError("Ein manipuliertes Artefakt wurde übernommen")


def verify_a_symlink_in_the_exchange_folder_is_not_followed() -> None:
    """Another container can write there; a link out of it must not work."""
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        asyncio.run(client.async_environment())
        secret = Path(tmp) / "secret.txt"
        secret.write_text("geheim", encoding="utf-8")
        link = client.exchange_dir / "renderer-status.json"
        try:
            link.symlink_to(secret)
        except OSError:
            return  # no symlink support here; nothing to prove
        try:
            asyncio.run(client.async_app_status())
        except protocol.RendererProtocolError as err:
            assert "Symlink" in str(err), err
            return
    raise AssertionError("Einem Symlink wurde gefolgt")


def _write_status(client, job_id: str, state: str, updated_at: str) -> None:
    folder = client.exchange_dir / "status"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{job_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protocol_version": 1,
                "job_id": job_id,
                "state": state,
                "updated_at": updated_at,
            }
        ),
        encoding="utf-8",
    )


def verify_a_running_job_can_be_found_again_without_the_browser() -> None:
    """The render outlives the page that started it.

    A trip film takes a quarter of an hour. If the only record of it lives
    in a browser tab, locking the phone loses it - the job runs on, and
    nothing in Home Assistant can say so. This is the way back.
    """
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        asyncio.run(client.async_environment())
        job = asyncio.run(
            client.async_submit_test_job(action=protocol.ACTION_RENDER_TRIP_FILM)
        )
        job_id = job["job_id"]
        _write_status(client, job_id, "running", "2026-08-07T15:00:00Z")

        found = asyncio.run(client.async_recent_jobs())
    assert [item["job_id"] for item in found] == [job_id], found
    assert found[0]["terminal"] is False, found
    # The kind is read from the job file, never guessed: announcing a trip
    # film as a test render would be worse than saying nothing.
    assert found[0]["kind"] == "trip_film", found


def verify_the_newest_job_is_reported_first() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        asyncio.run(client.async_environment())
        older, newer = protocol.new_job_id(), protocol.new_job_id()
        _write_status(client, older, "completed", "2026-08-07T10:00:00Z")
        _write_status(client, newer, "failed", "2026-08-07T12:00:00Z")
        found = asyncio.run(client.async_recent_jobs())
    assert [item["job_id"] for item in found] == [newer, older], found
    # Nothing produced these, so no kind may be invented for them.
    assert [item["kind"] for item in found] == ["", ""], found


def verify_one_unreadable_status_does_not_hide_the_others() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        asyncio.run(client.async_environment())
        good = protocol.new_job_id()
        _write_status(client, good, "running", "2026-08-07T09:00:00Z")
        (client.exchange_dir / "status" / f"{protocol.new_job_id()}.json").write_text(
            "{kaputt", encoding="utf-8"
        )
        # And something that is not a job id at all - the folder is written
        # by another container and may contain anything.
        (client.exchange_dir / "status" / "nonsense.json").write_text("{}", encoding="utf-8")
        found = asyncio.run(client.async_recent_jobs())
    assert [item["job_id"] for item in found] == [good], found


def verify_a_finished_mix_says_which_fassung_it_was() -> None:
    """Three mixes nobody can tell apart are three mixes nobody can use.

    The browser knew which job was fassung A and forgot it the moment
    the page reloaded, and the copy that gets uploaded is made from that
    job. So the label is written into the result by the side that made
    it and read back from there - never inferred from the order, which
    is exactly the guess that would put fassung C's music under A's
    name in a listening test.
    """
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        asyncio.run(client.async_environment())
        job_id = protocol.new_job_id()
        _write_status(client, job_id, "completed", "2026-08-13T05:00:00Z")
        folder = client.exchange_dir / "results" / job_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / protocol.ARTIFACT_FILM_WITH_MUSIC).write_bytes(b"nicht wirklich ein Film")
        (folder / "result.json").write_text(
            json.dumps({"video": {"music_variant": "B"}}), encoding="utf-8"
        )
        found = asyncio.run(client.async_recent_jobs())
    assert found[0]["kind"] == "film_music", found
    assert found[0]["music_variant"] == "B", found


def verify_a_mix_without_a_label_is_simply_unlabelled() -> None:
    """A missing label costs a click. A raised one costs the job list.

    And what comes back off disk is matched rather than trusted: the
    value ends up in a button's data attribute, and a variant is one
    letter or it is nothing.
    """
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        asyncio.run(client.async_environment())
        for stamp, payload in (
            ("05:01", "{kaputt"),
            ("05:02", json.dumps({"video": {}})),
            ("05:03", json.dumps({"video": {"music_variant": "../../etc/passwd"}})),
        ):
            job_id = protocol.new_job_id()
            _write_status(client, job_id, "completed", f"2026-08-13T{stamp}:00Z")
            folder = client.exchange_dir / "results" / job_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / protocol.ARTIFACT_FILM_WITH_MUSIC).write_bytes(b"x")
            (folder / "result.json").write_text(payload, encoding="utf-8")
        found = asyncio.run(client.async_recent_jobs())
    assert len(found) == 3, found
    for entry in found:
        assert entry["kind"] == "film_music", entry
        assert "music_variant" not in entry, entry


def verify_the_film_survives_the_jobs_piled_on_top_of_it() -> None:
    """The excerpt is the OLDEST entry of a music session, and the one
    the panel cannot work without.

    Rendering an excerpt and then mixing three fassungen and a few small
    copies already buries it: with a window of six it dropped off the
    end, and the card reported that no film existed while the film sat
    finished on disk. The number that protects the panel is the scan
    bound, not this window.
    """
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        asyncio.run(client.async_environment())
        film = protocol.new_job_id()
        _write_status(client, film, "completed", "2026-08-13T05:00:00Z")
        folder = client.exchange_dir / "results" / film
        folder.mkdir(parents=True, exist_ok=True)
        (folder / protocol.ARTIFACT_TRIP_FILM_VIDEO).write_bytes(b"ein Ausschnitt")
        for minute in range(1, 13):
            later = protocol.new_job_id()
            _write_status(client, later, "completed", f"2026-08-13T05:{minute:02d}:00Z")
        found = asyncio.run(client.async_recent_jobs())
    kinds = {item["job_id"]: item["kind"] for item in found}
    assert film in kinds, sorted(kinds.values())
    assert kinds[film] == "trip_film", kinds[film]


def verify_a_film_says_whether_it_already_has_a_soundtrack() -> None:
    """A comparison fassung only goes onto a SILENT film.

    An excerpt rendered with a track selected has music baked in by the
    composition, and the mux refuses to put a second one on it - rightly.
    The panel offered the button anyway, and the refusal then read as a
    fault in the comparison rather than as the wrong source. So the
    listing says it, and `None` when nobody could read it: reporting "no
    audio" for a film that was never measured is the guess that produces
    exactly that button.

    Read from `has_audible_audio`. The earlier version of this test used
    `has_audio` - the presence of a stream - and so agreed with a client
    that called every film scored: a Remotion render always writes an
    AAC track, and an empty one measures about -91 dBFS. A film carrying
    only that field, written before anything was measured, stays unknown.
    """
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        asyncio.run(client.async_environment())
        # The last case is the one that mattered in the field: a film
        # with an audio STREAM and nothing audible on it. It must not be
        # reported as scored.
        cases = {
            "05:10": ({"has_audible_audio": True}, True),
            "05:11": ({"has_audible_audio": False}, False),
            "05:12": ({}, None),
            "05:13": ({"has_audio": True}, None),
            "05:14": ({"has_audio": True, "has_audible_audio": False}, False),
        }
        wanted = {}
        for stamp, (video, audio) in cases.items():
            job_id = protocol.new_job_id()
            wanted[job_id] = audio
            _write_status(client, job_id, "completed", f"2026-08-13T{stamp}:00Z")
            folder = client.exchange_dir / "results" / job_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / protocol.ARTIFACT_TRIP_FILM_VIDEO).write_bytes(b"ein Film")
            payload = {} if not video else {"video": video}
            (folder / "result.json").write_text(json.dumps(payload), encoding="utf-8")
        found = asyncio.run(client.async_recent_jobs())
    for entry in found:
        assert entry["kind"] == "trip_film", entry
        expected = wanted[entry["job_id"]]
        if expected is None:
            assert "has_audio" not in entry, entry
        else:
            assert entry["has_audio"] is expected, entry


def verify_the_newest_jobs_are_read_and_not_the_alphabetically_first() -> None:
    """The scan bound has to cut by TIME, not by filename.

    Status files are named after job ids, so taking the first sixty
    names in alphabetical order takes an arbitrary sixty. Past that many
    files in the folder a finished film appeared or vanished depending
    on where its id happened to sort, the card reported that no film
    existed, and reloading changed nothing - the answer was not stale,
    it was the wrong sixty. Sorting the RESULT by time cannot repair a
    selection made without looking at time.
    """
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        asyncio.run(client.async_environment())
        folder = client.exchange_dir / "status"
        # Eighty older jobs whose ids all sort BEFORE the new film's, so
        # an alphabetical cut keeps exactly the wrong ones.
        for index in range(80):
            job_id = f"0000{index:04d}-0000-4000-8000-{index:012d}"
            _write_status(client, job_id, "completed", "2026-08-01T00:00:00Z")
            os.utime(folder / f"{job_id}.json", (1_000_000, 1_000_000))
        film = "ffffffff-0000-4000-8000-000000000001"
        _write_status(client, film, "completed", "2026-08-13T12:40:00Z")
        os.utime(folder / f"{film}.json", (2_000_000, 2_000_000))
        results = client.exchange_dir / "results" / film
        results.mkdir(parents=True, exist_ok=True)
        (results / protocol.ARTIFACT_TRIP_FILM_VIDEO).write_bytes(b"ein Film")

        found = asyncio.run(client.async_recent_jobs())
    assert found, "die Auftragsliste ist leer"
    assert found[0]["job_id"] == film, [item["job_id"] for item in found[:3]]
    assert found[0]["kind"] == "trip_film", found[0]


verify_the_newest_jobs_are_read_and_not_the_alphabetically_first()
verify_a_film_says_whether_it_already_has_a_soundtrack()
verify_the_film_survives_the_jobs_piled_on_top_of_it()
verify_a_finished_mix_says_which_fassung_it_was()
verify_a_mix_without_a_label_is_simply_unlabelled()
verify_no_supervisor_is_the_answer_not_a_fault()
verify_a_writable_exchange_folder_reports_ready()
verify_a_missing_app_is_not_an_error()
verify_a_corrupt_heartbeat_is_reported_not_raised()
verify_a_job_is_written_atomically_and_only_once()
verify_an_unknown_job_is_reported_as_unknown()
verify_a_forged_artifact_is_refused()
verify_a_symlink_in_the_exchange_folder_is_not_followed()
verify_a_running_job_can_be_found_again_without_the_browser()
verify_the_newest_job_is_reported_first()
verify_one_unreadable_status_does_not_hide_the_others()
print("Renderer app client tests passed.")
