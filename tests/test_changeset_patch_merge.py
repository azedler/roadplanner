"""Regression test: updating a stop/day/trip must not silently wipe out
unrelated nested `details` data that wasn't part of the update's patch.

Real failure this reproduces: a stop accumulates details.geocoding (from the
Nominatim resolution) and, separately, details.booking (facts pulled from a
resolved Booking.com link). A later, unrelated update - e.g. just setting
arrival_time - patched with `raw_stop.update(patch)`, a shallow dict update
that replaces the whole `details` key wholesale and destroys whichever half
of `details` the new patch didn't happen to mention.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_changeset_patch_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


for module_name in (
    "bounded_json",
    "json_io",
    "identifiers",
    "json_tree_validation",
    "stop_ordering",
    "canonical_day",
    "routing_helpers",
    "trip_documents",
    "trip_projections",
    "trip_state",
):
    load(module_name)

changeset = load("changeset")


def verify_details_merge_preserves_unrelated_keys() -> None:
    existing = {
        "id": "stop-1",
        "name": "Camping Look-Out",
        "arrival_time": "16:00",
        "details": {
            "geocoding": {"status": "resolved", "query": "Camping Look-Out"},
            "booking": {"source": "booking.com", "stay": "27.07-29.07"},
        },
    }
    # A later update only ever meant to change arrival_time, but the model's
    # patch also includes an unrelated details sub-key (per the documented
    # "only actually-changed fields" contract, this should be additive).
    patch = {
        "arrival_time": "18:00",
        "details": {"transport": {"mode_to_next": "ferry"}},
    }
    merged = changeset._apply_patch(existing, patch)

    assert merged["arrival_time"] == "18:00"
    # The two pre-existing details sub-keys must survive untouched.
    assert merged["details"]["geocoding"] == existing["details"]["geocoding"]
    assert merged["details"]["booking"] == existing["details"]["booking"]
    # The new sub-key from the patch is present too.
    assert merged["details"]["transport"] == {"mode_to_next": "ferry"}
    # Untouched top-level fields survive as a plain overwrite would expect.
    assert merged["name"] == "Camping Look-Out"


def verify_details_sub_key_overwrite_still_applies() -> None:
    existing = {"details": {"geocoding": {"status": "resolved"}}}
    patch = {"details": {"geocoding": {"status": "pending"}}}
    merged = changeset._apply_patch(existing, patch)
    assert merged["details"]["geocoding"] == {"status": "pending"}


def verify_missing_details_on_either_side_is_a_plain_overwrite() -> None:
    # No existing details: patch's details becomes the whole value (nothing
    # to merge against).
    assert changeset._apply_patch({"name": "x"}, {"details": {"a": 1}}) == {
        "name": "x",
        "details": {"a": 1},
    }
    # No details in the patch at all: existing details is untouched.
    existing = {"details": {"a": 1}}
    assert changeset._apply_patch(existing, {"name": "y"}) == {
        "details": {"a": 1},
        "name": "y",
    }


def verify_non_details_fields_always_fully_overwrite() -> None:
    existing = {"notes": "old", "type": "camping"}
    merged = changeset._apply_patch(existing, {"notes": "new"})
    assert merged["notes"] == "new"
    assert merged["type"] == "camping"


verify_details_merge_preserves_unrelated_keys()
verify_details_sub_key_overwrite_still_applies()
verify_missing_details_on_either_side_is_a_plain_overwrite()
verify_non_details_fields_always_fully_overwrite()

SOURCE = (PACKAGE_ROOT / "changeset.py").read_text(encoding="utf-8")
assert 'candidate.trip_document["trip"] = _apply_patch(' in SOURCE
assert 'document["day"] = _apply_patch(' in SOURCE
assert 'raw_stop = _apply_patch(raw_stop, deepcopy(operation["patch"]))' in SOURCE
# The bug this guards against: a plain shallow dict.update() call reappearing
# on any of the three update_* patch sites would silently reintroduce it.
assert '["trip"].update(' not in SOURCE
assert '["day"].update(' not in SOURCE
assert "raw_stop.update(" not in SOURCE

print("ChangeSet patch-merge tests passed.")
