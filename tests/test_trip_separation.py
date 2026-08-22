"""Trips own their renderer jobs - the backend half of the audit fixes.

The live audit found one root cause behind every separation fault: a
renderer job carries no trip identity, so everything that connected a
finished job back to a trip guessed "the newest one". A test trip's film
became the real journey's film; "Musik auflegen" could put one trip's
soundtrack onto another trip's video and record the result under the
wrong journey.

The fix is a submission ledger (renderer_job_ledger.py): written at the
one moment ownership is certain, asked everywhere a job is connected to
a trip. These tests pin the ledger itself, the mux refusal, and the
player's rescue scan - each against the production code.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "roadplanner_mcp"
PACKAGE_NAME = "rp_separation"

if PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(PACKAGE_ROOT)]
    sys.modules[PACKAGE_NAME] = package

for name, attributes in (
    ("homeassistant", {}),
    ("homeassistant.core", {
        "HomeAssistant": type("HomeAssistant", (), {}),
        "callback": lambda fn: fn,
    }),
    ("homeassistant.helpers", {}),
    ("homeassistant.helpers.event", {"async_call_later": None, "async_track_time_interval": None}),
    ("homeassistant.helpers.aiohttp_client", {"async_get_clientsession": lambda _hass: None}),
    ("homeassistant.util", {}),
    ("aiohttp", {"ClientError": type("ClientError", (Exception,), {}), "ClientTimeout": type("ClientTimeout", (), {"__init__": lambda self, **kw: None})}),
):
    if name not in sys.modules:
        module = types.ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        sys.modules[name] = module


def load(name: str):
    full = f"{PACKAGE_NAME}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, PACKAGE_ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


ledger_module = load("renderer_job_ledger")
player_film = load("player_film")
trip_film_export = load("trip_film_export")


class FakeHass:
    """Runs executor jobs inline - the ledger is synchronous anyway."""

    async def async_add_executor_job(self, fn, *args):
        return fn(*args)


def verify_the_ledger_answers_honestly() -> None:
    with tempfile.TemporaryDirectory() as base:
        ledger = ledger_module.RendererJobLedger(Path(base) / "jobs.json")
        ledger.record("job-a", "finnland", "trip_film")
        ledger.record("job-b", "test-reise", "trip_film")
        assert ledger.trip_for("job-a") == "finnland"
        assert ledger.trip_for("job-b") == "test-reise"
        # Unknown is None, never a guess.
        assert ledger.trip_for("job-x") is None
        assert ledger.trip_for("") is None
        annotated = ledger.annotate([
            {"job_id": "job-a", "kind": "trip_film"},
            {"job_id": "job-x", "kind": "trip_film"},
        ])
        assert annotated[0]["trip_id"] == "finnland"
        assert annotated[1]["trip_id"] == ""


def verify_the_ledger_is_bounded_and_survives_damage() -> None:
    with tempfile.TemporaryDirectory() as base:
        path = Path(base) / "jobs.json"
        ledger = ledger_module.RendererJobLedger(path)
        for index in range(ledger_module.MAX_ENTRIES + 40):
            ledger.record(f"job-{index:04d}", "reise", "trip_film")
        stored = json.loads(path.read_text(encoding="utf-8"))["jobs"]
        assert len(stored) == ledger_module.MAX_ENTRIES
        # The newest survive, the oldest were dropped.
        assert f"job-{ledger_module.MAX_ENTRIES + 39:04d}" in stored
        assert "job-0000" not in stored
        # A damaged file answers "unknown", never crashes and never guesses.
        path.write_text("kaputt{", encoding="utf-8")
        assert ledger.trip_for(f"job-{ledger_module.MAX_ENTRIES + 39:04d}") is None


def _exporter_with_ledger(ledger):
    exporter = trip_film_export.TripFilmExporter.__new__(
        trip_film_export.TripFilmExporter
    )
    exporter._hass = FakeHass()
    exporter._job_ledger = ledger
    return exporter


def verify_the_mux_refuses_a_foreign_or_unknown_film() -> None:
    """The critical finding: one trip's music onto another trip's video."""
    with tempfile.TemporaryDirectory() as base:
        ledger = ledger_module.RendererJobLedger(Path(base) / "jobs.json")
        ledger.record("film-finnland", "finnland", "trip_film")
        exporter = _exporter_with_ledger(ledger)

        # The own film passes.
        asyncio.run(
            exporter._async_assert_job_belongs_to("film-finnland", "finnland")
        )
        # Another trip's film is refused, with a sentence a person can act on.
        try:
            asyncio.run(
                exporter._async_assert_job_belongs_to("film-finnland", "test-reise")
            )
        except trip_film_export.ValidationError as err:
            assert "anderen Reise" in str(err), err
        else:
            raise AssertionError("fremder Film wurde vertont")
        # Unknown ownership is refused too - unprovable must not mean "probably mine".
        try:
            asyncio.run(
                exporter._async_assert_job_belongs_to("film-vergessen", "finnland")
            )
        except trip_film_export.ValidationError as err:
            assert "neu rendern" in str(err), err
        else:
            raise AssertionError("unbelegter Film wurde vertont")


def verify_both_mux_paths_ask_before_they_work() -> None:
    """The check must be the FIRST thing both methods do."""
    import ast

    source = (PACKAGE_ROOT / "trip_film_export.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for name in ("async_add_music", "async_add_variant_music"):
        node = next(
            item
            for item in ast.walk(tree)
            if isinstance(item, ast.AsyncFunctionDef) and item.name == name
        )
        body = [
            statement
            for statement in node.body
            if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
        ]
        first = ast.unparse(body[0])
        assert "_async_assert_job_belongs_to" in first, (
            f"{name} arbeitet, bevor es die Zugehörigkeit prüft: {first}"
        )


class FakeRendererApp:
    def __init__(self, jobs, durations):
        self._jobs = jobs
        self._durations = durations

    async def async_recent_jobs(self, limit=24):
        return list(self._jobs)

    async def async_result(self, job_id):
        return {
            "video_path": f"/share/{job_id}.mp4",
            "video": {"duration_seconds": self._durations.get(job_id, 600.0)},
        }


class FakeTripVideo:
    library_dir = Path("/nonexistent")

    async def async_adopt_video(self, source):
        return f"/api/roadplanner/trip_video_library/{source.name}"


def verify_the_rescue_scan_only_adopts_provable_ownership() -> None:
    """The likely live bug: the newest film, whoever made it."""
    with tempfile.TemporaryDirectory() as base:
        ledger = ledger_module.RendererJobLedger(Path(base) / "jobs.json")
        ledger.record("film-test", "test-reise", "trip_film")
        ledger.record("film-finnland", "finnland", "trip_film")
        jobs = [
            # Newest first, exactly how the exchange folder answers: the
            # test trip's film is newer.
            {"job_id": "film-test", "kind": "trip_film", "state": "completed"},
            {"job_id": "film-unbekannt", "kind": "trip_film", "state": "completed"},
            {"job_id": "film-finnland", "kind": "trip_film", "state": "completed"},
        ]
        service = player_film.PlayerFilmService(
            FakeHass(),
            FakeRendererApp(jobs, {}),
            FakeTripVideo(),
            player_film.PlayerFilmStore(Path(base) / "player"),
            job_ledger=ledger,
        )
        found = asyncio.run(service._async_scan_for_film("finnland"))
        assert found and found["job_id"] == "film-finnland", found
        # A trip with no provable film gets NOTHING - not the newest one.
        assert asyncio.run(service._async_scan_for_film("neue-reise")) is None
        # And without a ledger the scan adopts nothing at all.
        blind = player_film.PlayerFilmService(
            FakeHass(),
            FakeRendererApp(jobs, {}),
            FakeTripVideo(),
            player_film.PlayerFilmStore(Path(base) / "player2"),
        )
        assert asyncio.run(blind._async_scan_for_film("finnland")) is None


def verify_submissions_and_the_job_list_carry_the_trip() -> None:
    """The wiring, read from the real files: recorded at submit, said in the list."""
    export_source = (PACKAGE_ROOT / "trip_film_export.py").read_text(encoding="utf-8")
    assert export_source.count("_async_record_job(") >= 4, (
        "nicht jede Einreichung schreibt ins Auftrags-Verzeichnis"
    )
    panel_source = (PACKAGE_ROOT / "panel.py").read_text(encoding="utf-8")
    assert "runtime.job_ledger.annotate" in panel_source, (
        "die Auftragsliste sagt nicht mehr, wem ein Auftrag gehört"
    )
    setup_source = (PACKAGE_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert "job_ledger=job_ledger" in setup_source


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Trip separation tests passed.")


if __name__ == "__main__":
    main()
