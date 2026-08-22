"""Which film the player shows, and where the browser can fetch it.

The player has one job: put the trip's film on a kitchen tablet and keep
it running. That turns out to rest on two questions the rest of the
integration never had to answer.

**Which film?** Not "the last render that was started" - a render that
failed at minute forty must not take the finished film off the wall. So
what is remembered is the last one that FINISHED, and a newer attempt
only replaces it once it has succeeded. A submitted job is recorded
separately and promoted on completion, which also means the promotion
survives the browser being closed: it happens the next time anybody
asks, not while somebody is watching a progress bar.

**Fetchable how?** The renderer writes into the exchange folder, which
nothing in Home Assistant can serve. `TripVideoExport.async_adopt_video`
already solves that for downloads by copying the file into the media
library under an unguessable name - but it copies on every call and hands
back a NEW name each time. A player that re-adopted on every page load
would duplicate a 1.5 GB film until the library pruned the one it was
playing. So the adopted name is remembered per job, and re-adopted only
when the job changes or the copy has been pruned away.

Nothing here starts a render, and nothing here can reach a provider.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
import os

from .json_io import RoadplannerError
from .qa_excerpt import QA_MAX_SECONDS

_LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    """Write or leave the old file intact - never a half-written record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ).encode("utf-8")
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except OSError as err:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise RoadplannerError(f"Player-Filmdatei konnte nicht geschrieben werden: {path}") from err

#: Kinds of finished job that produce something worth playing. A review
#: copy is deliberately absent: it is a small proof for somebody else to
#: look at, not the film.
PLAYABLE_KINDS = ("film_music", "trip_film")

#: Above this many seconds, a finished trip_film is a film rather than a
#: quality excerpt. Excerpts are cut to 60-90 s by `qa_excerpt`, and they
#: are the same kind of job with the same artefact name, so nothing in a
#: finished result distinguishes them once the job file is gone. Recorded
#: submissions carry the answer explicitly; this threshold only has to
#: sort out films that were rendered before anything was recorded.
FULL_FILM_MIN_SECONDS = QA_MAX_SECONDS + 30.0

#: How far back to look when no submission was ever recorded.
SCAN_LIMIT = 24



def library_filename(url: str) -> str:
    """The stored filename a library URL points at.

    Split off the query first: the same route now answers a download with
    `?download=1`, and a filename that quietly carried that suffix would
    match nothing on disk - so the file would read as "gone" and the
    protection that keeps a trip's film from being pruned would protect
    the wrong name.
    """
    text = str(url or "").split("?", 1)[0].split("#", 1)[0]
    return text.rsplit("/", 1)[-1] if text else ""


class PlayerFilmStore:
    """Per-trip record of what was submitted and what finished."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir

    def initialize(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self.root_dir / "films.json"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            # A damaged record is not worth an exception: it holds a
            # pointer to a file, and the scan can rebuild it. Losing the
            # player over it would be the larger failure.
            _LOGGER.warning("Player-Filmdatei ist beschädigt und wird neu aufgebaut")
            return {}
        trips = raw.get("trips") if isinstance(raw, dict) else None
        return trips if isinstance(trips, dict) else {}

    def protected_filenames(self) -> set[str]:
        """The library files that ARE a trip's film, and may never be pruned.

        The video library is one folder for every trip, ten files deep,
        emptied oldest-first. That is fine for excerpts and review copies
        and wrong for the one thing it also held: rendering a handful of
        test films on a second trip pushed a real journey's finished film
        out of it, and once the renderer's exchange folder had aged past
        its day, the film was gone for good. Testing must not delete
        results.

        Read straight off the record the player already keeps, so there
        is no second list to fall out of step with it.
        """
        protected: set[str] = set()
        for entry in self.load().values():
            if not isinstance(entry, dict):
                continue
            latest = entry.get("latest")
            if not isinstance(latest, dict):
                continue
            url = str(latest.get("url") or "")
            name = library_filename(url)
            if name:
                protected.add(name)
        return protected

    def recorded_film(self, trip_id: str) -> dict[str, Any]:
        """What this trip's finished film is, as far as the record knows.

        The record outlives the renderer's exchange folder, which is
        exactly why it is asked: an hour after a render the exchange has
        forgotten the job, and everything that still needs the finished
        film - putting music on it, copying it for review - had nothing
        left to point at.
        """
        entry = self.load().get(str(trip_id or ""))
        latest = entry.get("latest") if isinstance(entry, dict) else None
        return dict(latest) if isinstance(latest, dict) else {}

    def save(self, trips: dict[str, Any]) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(
            self.path,
            {
                "schema_version": SCHEMA_VERSION,
                "updated_at": utc_now_iso(),
                "trips": trips,
            },
        )


class PlayerFilmService:
    """The player's answer to "what do I play, and from where?"."""

    def __init__(
        self, hass, renderer_app, trip_video, store: PlayerFilmStore, *, job_ledger=None
    ) -> None:
        self._hass = hass
        self._renderer_app = renderer_app
        self._trip_video = trip_video
        self._store = store
        # Job -> trip, written at submission. The rescue scan below used
        # to take "the newest finished film, whoever made it" - which is
        # how a fresh test trip's player showed the real journey's film,
        # and vice versa. Provable ownership or nothing.
        self._job_ledger = job_ledger

    # --- writing -------------------------------------------------------

    async def async_record_submission(
        self, trip_id: str, job_id: str, *, excerpt: bool
    ) -> None:
        """Remember that this job is meant to become the trip's film.

        An excerpt is recorded as one on purpose rather than skipped: the
        record is what lets a finished job be identified without guessing
        from its length, and "this one is not a film" is exactly the fact
        that would otherwise have to be guessed.
        """
        trip_id = str(trip_id or "")
        job_id = str(job_id or "")
        if not trip_id or not job_id:
            return
        trips = await self._hass.async_add_executor_job(self._store.load)
        entry = dict(trips.get(trip_id) or {})
        entry["submitted"] = {
            "job_id": job_id,
            "excerpt": bool(excerpt),
            "at": utc_now_iso(),
        }
        trips[trip_id] = entry
        await self._hass.async_add_executor_job(self._store.save, trips)

    # --- reading -------------------------------------------------------

    async def async_latest(self, trip_id: str) -> dict[str, Any] | None:
        """The newest film that finished, ready to play, or nothing.

        Never raises for an absent film: "there is no film yet" is a
        state the player renders, not an error it reports.
        """
        trip_id = str(trip_id or "")
        if not trip_id:
            return None
        trips = await self._hass.async_add_executor_job(self._store.load)
        entry = dict(trips.get(trip_id) or {})
        changed = await self._async_promote_submission(entry)
        latest = entry.get("latest")
        if not isinstance(latest, dict):
            found = await self._async_scan_for_film(trip_id)
            if found:
                entry["latest"] = found
                latest = found
                changed = True
        if isinstance(latest, dict):
            if await self._async_ensure_playable(latest):
                changed = True
        if changed:
            trips[trip_id] = entry
            await self._hass.async_add_executor_job(self._store.save, trips)
        latest = entry.get("latest")
        if not isinstance(latest, dict) or not latest.get("url"):
            return None
        return dict(latest)

    async def _async_promote_submission(self, entry: dict[str, Any]) -> bool:
        """Turn a finished submission into the trip's film. True if changed."""
        submitted = entry.get("submitted")
        if not isinstance(submitted, dict):
            return False
        job_id = str(submitted.get("job_id") or "")
        if not job_id:
            entry.pop("submitted", None)
            return True
        try:
            status = await self._renderer_app.async_job_status(job_id)
        except RoadplannerError:
            return False
        except Exception:  # noqa: BLE001 - a probe must not break the player
            _LOGGER.debug("Auftragsstatus für %s nicht lesbar", job_id, exc_info=True)
            return False
        state = str((status or {}).get("state") or "")
        if state not in ("completed", "failed", "cancelled"):
            return False
        entry.pop("submitted", None)
        if state != "completed" or submitted.get("excerpt"):
            # A failed render leaves the wall exactly as it was. That is
            # the whole reason this is two fields and not one.
            return True
        described = await self._async_describe(job_id)
        if described:
            entry["latest"] = described
        return True

    async def _async_scan_for_film(self, trip_id: str) -> dict[str, Any] | None:
        """No record yet: find the newest finished film OF THIS TRIP.

        The scan exists for records that were lost, not for films that
        were never this trip's. It used to take the newest finished film
        regardless of who made it - the audited fault: a fresh test trip
        adopted the real journey's film as its own, stored that, and
        protected the wrong file from pruning. Now a job qualifies only
        when the submission ledger says it was submitted for exactly this
        trip. A job the ledger does not know is skipped: unprovable
        ownership must never become an assignment.
        """
        try:
            jobs = await self._renderer_app.async_recent_jobs(limit=SCAN_LIMIT)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Auftragsliste nicht lesbar", exc_info=True)
            return None
        for job in jobs or []:
            if str(job.get("state") or "") != "completed":
                continue
            if str(job.get("kind") or "") not in PLAYABLE_KINDS:
                continue
            job_id = str(job.get("job_id") or "")
            if not await self._async_job_belongs_to(job_id, trip_id):
                continue
            described = await self._async_describe(job_id)
            if not described:
                continue
            if float(described.get("duration_seconds") or 0.0) < FULL_FILM_MIN_SECONDS:
                continue
            return described
        return None

    async def _async_job_belongs_to(self, job_id: str, trip_id: str) -> bool:
        if self._job_ledger is None or not job_id:
            return False
        try:
            owner = await self._hass.async_add_executor_job(
                self._job_ledger.trip_for, job_id
            )
        except Exception:  # noqa: BLE001 - a probe must not break the player
            _LOGGER.debug("Auftrags-Verzeichnis nicht lesbar", exc_info=True)
            return False
        return owner == str(trip_id or "")

    async def _async_describe(self, job_id: str) -> dict[str, Any] | None:
        """What a finished job produced, plus a URL a browser can use."""
        if not job_id:
            return None
        try:
            result = await self._renderer_app.async_result(job_id)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Ergebnis für %s nicht lesbar", job_id, exc_info=True)
            return None
        video_path = str((result or {}).get("video_path") or "")
        if not video_path:
            return None
        facts = (result or {}).get("video") or {}
        url = await self._async_adopt(Path(video_path))
        if not url:
            return None
        return {
            "job_id": job_id,
            "url": url,
            "created_at": str((result or {}).get("completed_at") or "") or utc_now_iso(),
            "render_profile": str(facts.get("render_profile") or ""),
            "width": int(facts.get("width") or 0),
            "height": int(facts.get("height") or 0),
            "duration_seconds": float(facts.get("duration_seconds") or 0.0),
            # Measured, never inferred from whether music was requested.
            "has_music": bool(facts.get("has_audible_audio")),
            "source_path": video_path,
        }

    async def _async_ensure_playable(self, latest: dict[str, Any]) -> bool:
        """Re-adopt if the library copy was pruned away. True if changed."""
        url = str(latest.get("url") or "")
        filename = library_filename(url)
        if filename and await self._hass.async_add_executor_job(
            self._library_has, filename
        ):
            return False
        source = Path(str(latest.get("source_path") or ""))
        if not str(source):
            latest.pop("url", None)
            return True
        fresh = await self._async_adopt(source)
        if not fresh:
            # The exchange folder cleans up after a day. The film is gone,
            # and saying so is better than serving a link that 404s.
            latest.pop("url", None)
            return True
        latest["url"] = fresh
        return True

    def _library_has(self, filename: str) -> bool:
        try:
            return (self._trip_video.library_dir / filename).is_file()
        except OSError:
            return False

    async def _async_adopt(self, source: Path) -> str:
        try:
            return await self._trip_video.async_adopt_video(source)
        except RoadplannerError:
            return ""
        except OSError:
            _LOGGER.debug("Film konnte nicht in die Bibliothek kopiert werden", exc_info=True)
            return ""
