"""The film the player puts on the wall.

The whole point of this service is one distinction: a render that was
STARTED is not a film. A twelve-minute export that dies at minute forty
must leave last week's film exactly where it was, because the tablet in
the kitchen is showing it to somebody right now.

The second point is quieter and costs disk: the exchange folder cannot
be served, so the film is copied into the media library - and a copy per
page load would duplicate a 1.5 GB file until the library pruned the one
being played. So the adopted name is remembered, and only re-adopted when
the job changes or the copy has actually gone.

Everything here runs against fakes. Nothing starts a render, nothing
reaches a provider, and no test in this file can spend anything.
"""

from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import tempfile

sys.dont_write_bytecode = True

# Registered AS A PACKAGE rather than loaded by file path. A module full
# of relative imports loaded flat fails with a message that names neither
# the import nor the cause, and this repository has already lost an
# afternoon to that one.
ROOT = Path(__file__).resolve().parents[1]
_PACKAGE = "roadplanner_player_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(ROOT / "custom_components" / "roadplanner_mcp")]
sys.modules[_PACKAGE] = _root

player_film = importlib.import_module(f"{_PACKAGE}.player_film")
FULL_FILM_MIN_SECONDS = player_film.FULL_FILM_MIN_SECONDS
PlayerFilmService = player_film.PlayerFilmService
PlayerFilmStore = player_film.PlayerFilmStore


class FakeHass:
    """Just enough Home Assistant to run an executor call inline."""

    async def async_add_executor_job(self, target, *args):
        return target(*args)


class FakeRendererApp:
    def __init__(self, statuses=None, results=None, recent=None) -> None:
        self.statuses = statuses or {}
        self.results = results or {}
        self.recent = recent or []

    async def async_job_status(self, job_id):
        return self.statuses.get(job_id) or {"job_id": job_id, "state": "running"}

    async def async_result(self, job_id):
        return self.results.get(job_id)

    async def async_recent_jobs(self, limit=24):
        return self.recent[:limit]


class FakeTripVideo:
    """Counts adoptions, because adopting twice is the bug."""

    def __init__(self, library_dir: Path) -> None:
        self.library_dir = library_dir
        self.adoptions = 0

    async def async_adopt_video(self, source: Path) -> str:
        self.adoptions += 1
        self.library_dir.mkdir(parents=True, exist_ok=True)
        name = f"copy-{self.adoptions}.mp4"
        (self.library_dir / name).write_bytes(b"film")
        return f"/api/roadplanner/trip_video_library/{name}"


def _film_result(job_id: str, *, seconds: float = 912.0, audible: bool = True):
    return {
        "job_id": job_id,
        "completed_at": "2026-08-19T10:00:00Z",
        "video_path": f"/share/exchange/results/{job_id}/roadplanner-trip-film.mp4",
        "video": {
            "duration_seconds": seconds,
            "width": 2560,
            "height": 1440,
            "render_profile": "high_quality",
            "has_audible_audio": audible,
        },
    }


def _service(tmp: Path, renderer):
    store = PlayerFilmStore(tmp / "player")
    store.initialize()
    video = FakeTripVideo(tmp / "library")
    return PlayerFilmService(FakeHass(), renderer, video, store), video


def verify_a_finished_render_becomes_the_film() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        renderer = FakeRendererApp(
            statuses={"job-a": {"state": "completed"}},
            results={"job-a": _film_result("job-a")},
        )
        service, video = _service(tmp, renderer)
        asyncio.run(service.async_record_submission("trip", "job-a", excerpt=False))
        found = asyncio.run(service.async_latest("trip"))
        assert found, "der fertige Film wird nicht gefunden"
        assert found["job_id"] == "job-a", found
        assert found["url"].startswith("/api/roadplanner/trip_video_library/"), found
        assert found["has_music"] is True, found
        # Asked again: the same film, and NOT copied a second time.
        again = asyncio.run(service.async_latest("trip"))
        assert again["url"] == found["url"], (again, found)
        assert video.adoptions == 1, f"{video.adoptions} Kopien statt einer"


def verify_a_failed_render_does_not_take_the_film_away() -> None:
    """The point of the whole service, stated as a test."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        renderer = FakeRendererApp(
            statuses={"job-a": {"state": "completed"}, "job-b": {"state": "failed"}},
            results={"job-a": _film_result("job-a")},
        )
        service, _video = _service(tmp, renderer)
        asyncio.run(service.async_record_submission("trip", "job-a", excerpt=False))
        good = asyncio.run(service.async_latest("trip"))
        assert good["job_id"] == "job-a"
        # A newer attempt that dies.
        asyncio.run(service.async_record_submission("trip", "job-b", excerpt=False))
        after = asyncio.run(service.async_latest("trip"))
        assert after["job_id"] == "job-a", (
            "ein gescheiterter Render hat den letzten guten Film verdrängt"
        )


def verify_a_running_render_leaves_the_old_film_playing() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        renderer = FakeRendererApp(
            statuses={"job-a": {"state": "completed"}, "job-b": {"state": "running"}},
            results={"job-a": _film_result("job-a")},
        )
        service, _video = _service(tmp, renderer)
        asyncio.run(service.async_record_submission("trip", "job-a", excerpt=False))
        asyncio.run(service.async_latest("trip"))
        asyncio.run(service.async_record_submission("trip", "job-b", excerpt=False))
        during = asyncio.run(service.async_latest("trip"))
        assert during["job_id"] == "job-a", during


def verify_an_excerpt_never_becomes_the_film() -> None:
    """Sixty seconds of the film is not the film.

    The same kind of job, the same artefact name, the same result shape.
    Only the record tells them apart once the job file is gone - which is
    why the submission is recorded rather than guessed at afterwards.
    """
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        renderer = FakeRendererApp(
            statuses={"job-a": {"state": "completed"}, "job-x": {"state": "completed"}},
            results={
                "job-a": _film_result("job-a"),
                "job-x": _film_result("job-x", seconds=64.0),
            },
        )
        service, _video = _service(tmp, renderer)
        asyncio.run(service.async_record_submission("trip", "job-a", excerpt=False))
        asyncio.run(service.async_latest("trip"))
        asyncio.run(service.async_record_submission("trip", "job-x", excerpt=True))
        after = asyncio.run(service.async_latest("trip"))
        assert after["job_id"] == "job-a", "ein Prüfausschnitt wurde zum Reisefilm"


def verify_a_pruned_copy_is_fetched_again() -> None:
    """The library keeps a bounded number of files; the record survives."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        renderer = FakeRendererApp(
            statuses={"job-a": {"state": "completed"}},
            results={"job-a": _film_result("job-a")},
        )
        service, video = _service(tmp, renderer)
        asyncio.run(service.async_record_submission("trip", "job-a", excerpt=False))
        first = asyncio.run(service.async_latest("trip"))
        (video.library_dir / first["url"].rsplit("/", 1)[-1]).unlink()
        second = asyncio.run(service.async_latest("trip"))
        assert second, "nach dem Aufräumen findet der Player gar nichts mehr"
        assert second["url"] != first["url"], second
        assert video.adoptions == 2, video.adoptions


def verify_no_record_falls_back_to_the_newest_finished_film() -> None:
    """Installations whose films predate this bookkeeping."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        renderer = FakeRendererApp(
            results={
                "old-excerpt": _film_result("old-excerpt", seconds=70.0),
                "old-film": _film_result("old-film", seconds=900.0),
            },
            recent=[
                {"job_id": "old-excerpt", "state": "completed", "kind": "trip_film"},
                {"job_id": "old-film", "state": "completed", "kind": "trip_film"},
            ],
        )
        service, _video = _service(tmp, renderer)
        found = asyncio.run(service.async_latest("trip"))
        assert found, "ohne Aufzeichnung findet der Player keinen Film"
        assert found["job_id"] == "old-film", (
            "der Rückfall hält einen 70-Sekunden-Ausschnitt für den Reisefilm"
        )
        assert 70.0 < FULL_FILM_MIN_SECONDS <= 900.0


def verify_an_unknown_trip_is_no_film_and_no_error() -> None:
    with tempfile.TemporaryDirectory() as raw:
        service, _video = _service(Path(raw), FakeRendererApp())
        assert asyncio.run(service.async_latest("")) is None
        assert asyncio.run(service.async_latest("nie-gesehen")) is None


def verify_a_damaged_record_does_not_break_the_player() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        renderer = FakeRendererApp(
            results={"old-film": _film_result("old-film")},
            recent=[{"job_id": "old-film", "state": "completed", "kind": "trip_film"}],
        )
        service, _video = _service(tmp, renderer)
        (tmp / "player" / "films.json").write_text("{kaputt", encoding="utf-8")
        found = asyncio.run(service.async_latest("trip"))
        assert found and found["job_id"] == "old-film", found


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Player film tests passed.")


if __name__ == "__main__":
    main()
