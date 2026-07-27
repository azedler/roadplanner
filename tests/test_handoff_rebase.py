"""Behavioral tests for HandoffStore.rebase_pending.

Regression coverage for a real reported problem: with two pending changes to
confirm, applying the first bumps the trip revision and the second's
base_revision becomes stale - and there was no way to still apply it besides
discarding it and redoing the whole request from scratch. rebase_pending is
the persistence half of the fix (the caller is responsible for re-validating
via preview_changeset first; this only re-stamps and never touches the
operations themselves).
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import shutil
import sys
import tempfile
import types

ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE = "roadplanner_handoff_rebase_test"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package

for name in ("stop_ordering", "canonical_day", "roadplanner", "changeset", "handoff"):
    spec = spec_from_file_location(f"{PACKAGE}.{name}", ROOT / f"{name}.py")
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

handoff = sys.modules[f"{PACKAGE}.handoff"]

CHANGESET = {
    "kind": "roadplanner_changeset",
    "version": 1,
    "changeset_id": "changeset-rebase-001",
    "trip_id": "trip-1",
    "base_revision": 10,
    "created_at": "2026-07-27T12:00:00Z",
    "title": "Übernachtung aktualisieren",
    "summary": "Stopp aktualisieren.",
    "apply_mode": "review",
    "operations": [
        {
            "operation_id": "op-1",
            "action": "update",
            "entity_type": "stop",
            "entity_id": "stop-1",
            "day_id": "day-1",
            "changes": {"name": "Campingplatz Rovaniemi"},
        }
    ],
    "open_questions": [],
    "assumptions": [],
    "research_notes": [],
}


def verify_rebase_updates_revision_and_resets_status() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="roadplanner_handoff_test_"))
    try:
        store = handoff.HandoffStore(tmp_dir / "handoffs")
        ingested = store.ingest(changeset=CHANGESET, source="test")
        assert ingested["duplicate"] is False
        handoff_id = ingested["id"]

        rebased = store.rebase_pending(handoff_id=handoff_id, base_revision=13)

        assert rebased["status"] == "pending"
        assert rebased["base_revision"] == 13

        # The stored envelope must agree with itself: envelope.base_revision
        # and envelope.changeset.base_revision always match (_validate_envelope
        # enforces this on every read), and the operations are untouched.
        full = store.get(handoff_id)
        assert full["base_revision"] == 13
        assert full["changeset"]["base_revision"] == 13
        operation = full["changeset"]["operations"][0]
        assert operation["op"] == "update_stop"
        assert operation["stop_id"] == "stop-1"
        assert operation["patch"] == {"name": "Campingplatz Rovaniemi"}
        assert full["changeset_id"] == CHANGESET["changeset_id"]

        # A fresh store instance re-reads the same persisted, rebased data.
        reloaded = handoff.HandoffStore(tmp_dir / "handoffs")
        again = reloaded.get_pending(handoff_id)
        assert again["base_revision"] == 13
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def verify_rebase_of_unknown_handoff_raises() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="roadplanner_handoff_test_"))
    try:
        store = handoff.HandoffStore(tmp_dir / "handoffs")
        store.initialize()
        try:
            store.rebase_pending(handoff_id="handoff-missing", base_revision=5)
        except handoff.ValidationError:
            pass
        else:
            raise AssertionError("expected ValidationError for a missing handoff")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def verify_rebase_of_already_applied_handoff_raises() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="roadplanner_handoff_test_"))
    try:
        store = handoff.HandoffStore(tmp_dir / "handoffs")
        ingested = store.ingest(changeset=CHANGESET, source="test")
        handoff_id = ingested["id"]
        store.mark_applied(handoff_id=handoff_id, result={"revision": 11})
        try:
            store.rebase_pending(handoff_id=handoff_id, base_revision=13)
        except handoff.ValidationError:
            pass
        else:
            raise AssertionError(
                "expected ValidationError for an already-applied handoff"
            )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


verify_rebase_updates_revision_and_resets_status()
verify_rebase_of_unknown_handoff_raises()
verify_rebase_of_already_applied_handoff_raises()

print("Handoff rebase tests passed.")
