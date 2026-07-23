"""Regression tests for persisted destination galleries and decision media refs."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import tempfile
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_gallery_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

roadplanner_module = types.ModuleType(f"{PACKAGE_NAME}.roadplanner")


class StorageError(RuntimeError):
    pass


class ValidationError(RuntimeError):
    pass


def validate_identifier(value, field):
    text = str(value or "").strip()
    if not text or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in text):
        raise ValidationError(f"Invalid {field}")
    return text


roadplanner_module.StorageError = StorageError
roadplanner_module.ValidationError = ValidationError
roadplanner_module.validate_identifier = validate_identifier
sys.modules[roadplanner_module.__name__] = roadplanner_module

spec = spec_from_file_location(
    f"{PACKAGE_NAME}.experience_store",
    PACKAGE_ROOT / "experience_store.py",
)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

raw_images = [
    {
        "id": f"image-{index}",
        "provider": "wikimedia_commons" if index % 2 else "openverse",
        "image_url": f"https://example.org/image-{index}.jpg",
        "source_url": f"https://example.org/source-{index}",
        "license": "CC BY 4.0",
    }
    for index in range(1, 6)
]
raw_images.append({**raw_images[0], "id": "duplicate"})

gallery = module.normalize_destination_gallery(
    {
        "stop_id": "stop-1",
        "day_id": "day-1",
        "query": "Berg der Kreuze Domantai Litauen",
        "images": raw_images,
        "primary_image_id": "missing-primary",
    }
)
assert gallery["status"] == "ready"
assert len(gallery["images"]) == 3
assert gallery["primary_image_id"] == gallery["images"][0]["id"]
assert len({item["source_url"] for item in gallery["images"]}) == 3

media_ref_decision = module.normalize_decision(
    {
        "id": "decision-1",
        "trip_id": "trip-1",
        "title": "Where to stay",
        "question": "Keep the current plan?",
        "options": [
            {
                "id": "option-current",
                "title": "Current stop",
                "images": [
                    {
                        "id": "media-photo-1",
                        "media_id": "photo-1",
                        "provider": "onedrive",
                        "alt": "Own travel photo",
                    }
                ],
            },
            {
                "id": "option-alternative",
                "title": "Alternative stop",
                "images": [
                    {
                        "id": "external-1",
                        "provider": "wikimedia_commons",
                        "image_url": "https://example.org/alternative.jpg",
                        "source_url": "https://example.org/alternative",
                    }
                ],
            },
        ],
    }
)
assert media_ref_decision["options"][0]["images"][0]["media_id"] == "photo-1"
assert media_ref_decision["options"][0]["image"]["media_id"] == "photo-1"

resolved_decisions = module.resolve_decision_media_references(
    [media_ref_decision],
    [
        {
            "id": "photo-1",
            "name": "Lake sunset.jpg",
            "caption": "Sunset at the stop",
            "thumbnail_url": "/api/roadplanner/media/thumbnail/trip-1/photo-1?token=fresh",
            "original_url": "/api/roadplanner/media/original/trip-1/photo-1?token=fresh",
        }
    ],
)
resolved_option = resolved_decisions[0]["options"][0]
assert "token=fresh" in resolved_option["images"][0]["image_url"]
assert resolved_option["image"]["provider"] == "onedrive"
assert resolved_option["image"]["alt"] == "Own travel photo"

with tempfile.TemporaryDirectory() as tmp:
    store = module.ExperienceStore(Path(tmp))
    store.initialize()
    result = store.upsert_destination_galleries("trip-1", [gallery])
    assert result == {"updated": 1, "total": 1}
    loaded = store.load("trip-1")
    assert loaded["schema_version"] == 3
    assert loaded["destination_galleries"]["stop-1"]["primary_image_id"] == gallery["primary_image_id"]
    updated = store.update_destination_gallery(
        "trip-1",
        "stop-1",
        {"primary_image_id": gallery["images"][1]["id"]},
    )
    assert updated["primary_image_id"] == gallery["images"][1]["id"]
    curation = store.upsert_media_curation(
        "trip-1",
        {
            "stop_id": "stop-1",
            "status": "ready",
            "mode": "hybrid_vision",
            "fingerprint": "abc",
            "candidate_ids": ["photo-1", "photo-2"],
            "cover_id": "photo-2",
            "highlight_ids": ["photo-2", "photo-1"],
            "reasons": {"photo-2": "best cover"},
        },
    )
    assert curation["mode"] == "hybrid_vision"
    assert store.load("trip-1")["media_curations"]["stop-1"]["cover_id"] == "photo-2"
    first = store.reserve_vision_call("trip-1", "2026-07-23", 1)
    second = store.reserve_vision_call("trip-1", "2026-07-23", 1)
    assert first["reserved"] is True
    assert second["reserved"] is False
    store.delete_destination_gallery("trip-1", "stop-1")
    assert store.load("trip-1")["destination_galleries"] == {}

print("Destination gallery persistence tests passed.")
