"""Regression tests for the three confirmed Stellplatz audit findings.

1. Option galleries could never be persisted: the synthetic gallery key
   "option:<id>" failed validate_identifier (colon), so every save/refresh
   raised and stored galleries were dropped on load. Gallery keys now go
   through validate_gallery_stop_id, which accepts the prefixed form.
2. Activating Plan B demoted the STALE activation-time snapshot of the old
   stop, silently discarding manual edits (fixed GPS, renamed stop, new
   notes) made after activation. Demotion now takes the stop's live fields.
3. Activation was completely blocked when the existing overnight stop
   failed strict option validation (address-only location, overlong name) -
   "Plan B" was impossible until the OLD stop was edited. Demotion now
   clamps instead of rejecting, and preserves an address-only location in
   the notes instead of dropping it.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import tempfile
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_pitch_audit_test"

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


store_module = load("experience_store")
pitch = load("pitch_options")

NOW = "2026-07-30T15:00:00Z"


# --- 1. option gallery keys ---------------------------------------------

def verify_option_gallery_keys_are_accepted() -> None:
    assert store_module.validate_gallery_stop_id("option:opt-abc123", "stop_id") == "option:opt-abc123"
    assert store_module.validate_gallery_stop_id("stop-abc123", "stop_id") == "stop-abc123"
    for bad in ("option:", "option:has space", "option:a:b", None, "a b"):
        try:
            store_module.validate_gallery_stop_id(bad, "stop_id")
        except store_module.ValidationError:
            continue
        raise AssertionError(f"invalid key must still be rejected: {bad!r}")


def verify_option_gallery_roundtrip_persists() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = store_module.ExperienceStore(Path(tmp))
        gallery = {
            "stop_id": "option:opt-abc123",
            "day_id": "day-1",
            "status": "ready",
            "images": [{"id": "img-1", "image_url": "https://example.org/a.jpg"}],
        }
        result = store.upsert_destination_galleries("trip-1", [gallery])
        assert result["updated"] == 1, "saving an option gallery must not raise"
        loaded = store.load("trip-1")
        assert "option:opt-abc123" in loaded["destination_galleries"], (
            "an option gallery must survive a load roundtrip instead of being "
            "silently dropped"
        )
        store.delete_destination_gallery("trip-1", "option:opt-abc123")
        assert "option:opt-abc123" not in store.load("trip-1")["destination_galleries"]


def verify_prefix_constants_stay_in_sync() -> None:
    helpers_source = (PACKAGE_ROOT / "experience_helpers.py").read_text(encoding="utf-8")
    assert f'OPTION_STOP_PREFIX = "{store_module.OPTION_GALLERY_PREFIX}"' in helpers_source, (
        "experience_helpers.OPTION_STOP_PREFIX and "
        "experience_store.OPTION_GALLERY_PREFIX must stay identical"
    )


# --- 2. live stop fields win over the stale snapshot --------------------

def _day_document(stop):
    return {"day": {"id": "day-1", "details": {}}, "stops": [stop]}


def verify_demotion_uses_live_stop_fields_not_stale_snapshot() -> None:
    stop = {
        "id": "stop-1",
        "type": "overnight",
        # Live state: the user fixed GPS and renamed the stop AFTER activation.
        "name": "Stellplatz Hafen Nord (korrigiert)",
        "location": {"latitude": 63.1, "longitude": 18.4},
        "notes": "Tor-Code 4711",
        "details": {
            "active_overnight_option": {
                "id": "opt-old",
                "name": "Stellplatz Hafen",
                "location": {"latitude": 0.5, "longitude": 0.5},
                "notes": "",
                "pros": ["ruhig"],
                "status": "backup",
            },
            "overnight_option_id": "opt-old",
        },
    }
    doc = _day_document(stop)
    plan = pitch.get_overnight_plan(doc["day"])
    option_b = pitch.validate_option_input({"name": "Plan B Wiese"}, now=NOW)
    plan["options"] = [option_b]
    doc["day"]["details"]["overnight_plan"] = plan

    result = pitch.apply_activate_option(doc, option_b["id"], now=NOW, actor="test")
    demoted = result["demoted_option"]
    assert demoted["name"] == "Stellplatz Hafen Nord (korrigiert)", (
        "manual rename after activation must survive demotion"
    )
    assert demoted["location"] == {"latitude": 63.1, "longitude": 18.4}, (
        "manually corrected GPS must survive demotion, not the stale snapshot"
    )
    assert "Tor-Code 4711" in demoted["notes"]
    assert demoted["pros"] == ["ruhig"], "snapshot metadata (pros/cons) is kept"


# --- 3. activation must never be blocked by the old stop ----------------

def verify_activation_succeeds_with_address_only_location() -> None:
    stop = {
        "id": "stop-1",
        "type": "overnight",
        "name": "Camping " + "sehr lang " * 30,  # > 200 chars, legal for stops
        "location": {"address": "Camping Roma, Italien"},
        "notes": "",
        "details": {},
    }
    doc = _day_document(stop)
    plan = pitch.get_overnight_plan(doc["day"])
    option_b = pitch.validate_option_input({"name": "Plan B Wiese"}, now=NOW)
    plan["options"] = [option_b]
    doc["day"]["details"]["overnight_plan"] = plan

    result = pitch.apply_activate_option(doc, option_b["id"], now=NOW, actor="test")
    demoted = result["demoted_option"]
    assert len(demoted["name"]) <= 200, "overlong stop name is clamped, not rejected"
    assert demoted["location"] == {}, "address-only location yields no fake coordinates"
    assert "Camping Roma, Italien" in demoted["notes"], (
        "the address must survive in the notes instead of being dropped"
    )
    assert doc["stops"][0]["name"] == "Plan B Wiese", "activation itself succeeded"


if __name__ == "__main__":
    verify_option_gallery_keys_are_accepted()
    verify_option_gallery_roundtrip_persists()
    verify_prefix_constants_stay_in_sync()
    verify_demotion_uses_live_stop_fields_not_stale_snapshot()
    verify_activation_succeeds_with_address_only_location()
    print("Pitch audit fix tests passed.")
