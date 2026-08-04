"""Behavioral tests for the cross-trip crew (people/vehicle) registry."""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import shutil
import sys
import tempfile
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_crew_store_test"


class StorageError(RuntimeError):
    pass


class ValidationError(RuntimeError):
    pass


package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

roadplanner = types.ModuleType(f"{PACKAGE_NAME}.roadplanner")
roadplanner.StorageError = StorageError
roadplanner.ValidationError = ValidationError
sys.modules[roadplanner.__name__] = roadplanner


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


crew_store = load("crew_store")


def verify_person_crud_and_retirement() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="roadplanner_crew_test_"))
    try:
        store = crew_store.CrewStore(tmp_dir / "crew")
        store.initialize()

        assert store.load_people() == []

        aron = store.create_person({"name": "Aron", "kind": "person", "note": "Fahrer"})
        assert aron["active"] is True
        assert aron["id"].startswith("person-")

        rufus = store.create_person({"name": "Rufus", "kind": "dog"})
        people = store.load_people()
        # Most recently created first.
        assert [item["name"] for item in people] == ["Rufus", "Aron"]

        updated = store.update_person(aron["id"], {"note": "Fahrer & Planer"})
        assert updated["note"] == "Fahrer & Planer"
        assert updated["name"] == "Aron"

        retired = store.set_person_active(aron["id"], False)
        assert retired["active"] is False
        # Retiring keeps the record (findable, just inactive) - never deleted.
        assert any(item["id"] == aron["id"] for item in store.load_people())

        reactivated = store.set_person_active(aron["id"], True)
        assert reactivated["active"] is True

        try:
            store.update_person("person-does-not-exist", {"name": "x"})
        except ValidationError:
            pass
        else:
            raise AssertionError("expected ValidationError for unknown person_id")

        try:
            store.create_person({"name": "", "kind": "person"})
        except ValidationError:
            pass
        else:
            raise AssertionError("expected ValidationError for a blank name")

        try:
            store.create_person({"name": "Katze", "kind": "cat"})
        except ValidationError:
            pass
        else:
            raise AssertionError("expected ValidationError for an unsupported kind")

        # A fresh store instance re-reads the same persisted data (durability).
        reloaded = crew_store.CrewStore(tmp_dir / "crew")
        names = sorted(item["name"] for item in reloaded.load_people())
        assert names == ["Aron", "Rufus"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def verify_vehicle_retirement_does_not_delete() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="roadplanner_crew_test_"))
    try:
        store = crew_store.CrewStore(tmp_dir / "crew")
        store.initialize()

        nugget = store.create_vehicle(
            {"name": "Nugget", "description": "Cremefarbener Kastenwagen mit orangem Streifen"}
        )
        assert nugget["active"] is True

        retired = store.set_vehicle_active(nugget["id"], False)
        assert retired["active"] is False
        assert retired["description"] == nugget["description"]
        # Still resolvable by id - a past trip's snapshot reference must not break.
        vehicles = store.load_vehicles()
        assert len(vehicles) == 1
        assert vehicles[0]["id"] == nugget["id"]
        assert vehicles[0]["active"] is False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def verify_reference_photo_assignment_round_trips() -> None:
    # "Wer ist wer": one assigned trip photo per person - the portrait in
    # the PDF and the Vision reference face. Survives updates, clearable.
    with tempfile.TemporaryDirectory() as tmp:
        store = crew_store.CrewStore(Path(tmp))
        store.initialize()
        person = store.create_person({"name": "Peer"})
        assert person["reference_media_id"] == ""
        updated = store.update_person(
            person["id"], {"reference_media_id": "media-123"}
        )
        assert updated["reference_media_id"] == "media-123"
        assert store.load_people()[0]["reference_media_id"] == "media-123"
        # An unrelated update keeps the assignment; explicit empty clears it.
        kept = store.update_person(person["id"], {"note": "Sportlich"})
        assert kept["reference_media_id"] == "media-123"
        cleared = store.update_person(person["id"], {"reference_media_id": ""})
        assert cleared["reference_media_id"] == ""


def verify_reference_crop_is_validated_and_clamped() -> None:
    # A group photo needs a face region so ONE person is identifiable.
    with tempfile.TemporaryDirectory() as tmp:
        store = crew_store.CrewStore(Path(tmp))
        store.initialize()
        person = store.create_person({"name": "Levi"})
        assert person["reference_crop"] is None
        saved = store.update_person(
            person["id"],
            {"reference_crop": {"x": 0.25, "y": 0.1, "w": 0.3, "h": 0.4}},
        )
        assert saved["reference_crop"] == {"x": 0.25, "y": 0.1, "w": 0.3, "h": 0.4}
        # Out-of-bounds boxes are clamped into the image.
        clamped = store.update_person(
            person["id"], {"reference_crop": {"x": 0.8, "y": 0.9, "w": 0.5, "h": 0.5}}
        )
        assert clamped["reference_crop"] == {"x": 0.8, "y": 0.9, "w": 0.2, "h": 0.1}
        # Degenerate or malformed input simply means "whole photo".
        for junk in ({"x": 0, "y": 0, "w": 0.01, "h": 0.5}, {"x": "a"}, "nope", None):
            assert store.update_person(
                person["id"], {"reference_crop": junk}
            )["reference_crop"] is None


verify_person_crud_and_retirement()
verify_vehicle_retirement_does_not_delete()
verify_reference_photo_assignment_round_trips()
verify_reference_crop_is_validated_and_clamped()

print("Crew store tests passed.")
