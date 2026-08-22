"""Which trip a renderer job belongs to - the fact nothing else records.

A renderer job carries no trip identity of its own: the job file, its
status and the recent-jobs listing know only job_id, kind and state. The
live audit showed what that omission costs. Everything that connects a
finished job back to a trip - the story tab's adoption, the player's
film record, the music mux - had to GUESS, and every guess was "the
newest one". So a test trip's film became the real trip's film, another
trip's render was shown as this one's, and "Musik auflegen" was able to
put one trip's soundtrack onto another trip's video and record the
result as the film of the wrong journey.

The renderer stays trip-blind on purpose (it renders what it is given).
The knowledge lives HERE, on the integration side, written at the one
moment it is certain: submission. Every check afterwards asks this
ledger instead of guessing.

The ledger is bounded: jobs expire out of the exchange folder within a
day, so a few hundred entries cover everything that can still be asked
about, with room to spare.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .json_io import _write_json_atomic, utc_now_iso

_LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 1

#: Far more than a day's worth of jobs. Old entries are only dropped so
#: the file cannot grow forever.
MAX_ENTRIES = 300


class RendererJobLedger:
    """Blocking job->trip record. Call via the executor from async code."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            # A damaged ledger must not take the renderer features down.
            # Its consumers treat "unknown job" as "ownership unprovable"
            # and refuse to act - which is the safe direction.
            _LOGGER.warning("Auftrags-Verzeichnis ist beschädigt und wird neu aufgebaut")
            return {}
        jobs = raw.get("jobs") if isinstance(raw, dict) else None
        return jobs if isinstance(jobs, dict) else {}

    def record(
        self,
        job_id: str,
        trip_id: str,
        kind: str = "",
        excerpt: bool = False,
        source_job_id: str = "",
    ) -> None:
        """Remember that this job was submitted for this trip.

        `excerpt` and `source_job_id` are here for the same reason the
        trip is: they are certain at submission and unknowable
        afterwards. A quality excerpt is the same `kind` as a whole film
        and produces the same artefact, so the finished job cannot be
        told apart from a real film - and the panel, after a reload,
        offered "Musik auflegen" on a 65-second excerpt, which would have
        made that excerpt the trip's official film. `source_job_id` says
        which silent film a mux was laid onto, so the panel can show the
        scored film instead of offering to score it a second time.
        """
        job_id = str(job_id or "")
        trip_id = str(trip_id or "")
        if not job_id or not trip_id:
            return
        jobs = self._load()
        jobs[job_id] = {
            "trip_id": trip_id,
            "kind": str(kind or ""),
            "excerpt": bool(excerpt),
            "source_job_id": str(source_job_id or ""),
            "recorded_at": utc_now_iso(),
        }
        if len(jobs) > MAX_ENTRIES:
            ordered = sorted(
                jobs.items(),
                key=lambda item: str(item[1].get("recorded_at") or ""),
            )
            jobs = dict(ordered[-MAX_ENTRIES:])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(
            self.path,
            {"schema_version": SCHEMA_VERSION, "jobs": jobs},
        )

    def trip_for(self, job_id: str) -> str | None:
        """The trip a job was submitted for, or None when unrecorded.

        None is an honest answer, not a default: a job this ledger never
        saw (submitted before the ledger existed, or a test render that
        belongs to no trip) has UNPROVABLE ownership, and every consumer
        treats that as "do not connect it to any trip".
        """
        entry = self._load().get(str(job_id or ""))
        if not isinstance(entry, dict):
            return None
        trip_id = str(entry.get("trip_id") or "")
        return trip_id or None

    def excerpt_for(self, job_id: str) -> bool:
        """Whether this job was submitted as a quality excerpt."""
        entry = self._load().get(str(job_id or ""))
        return bool(entry.get("excerpt")) if isinstance(entry, dict) else False

    def annotate(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return the job list with what only submission knew attached."""
        known = self._load()
        annotated: list[dict[str, Any]] = []
        for job in jobs or []:
            if not isinstance(job, dict):
                continue
            entry = known.get(str(job.get("job_id") or ""))
            entry = entry if isinstance(entry, dict) else {}
            annotated.append(
                {
                    **job,
                    "trip_id": str(entry.get("trip_id") or ""),
                    # False for a job the ledger never saw, which is the
                    # same answer the panel already gives such a job: it
                    # is not adopted at all, so nothing reads this.
                    "excerpt": bool(entry.get("excerpt")),
                    "source_job_id": str(entry.get("source_job_id") or ""),
                }
            )
        return annotated
