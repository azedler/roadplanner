"""A video in the media library, beside the photographs.

The film already knows what a photograph is. A video is the same kind of
thing and has to stay the same kind of thing - one library, one
assignment to a day, one curation. A parallel "videos" world would mean
every rule about days, stops and coverage written twice, and the second
copy drifting.

What is genuinely different is that a video has an inside: a photograph
is chosen or not, a video is four minutes of which six seconds are worth
watching. These checks pin that difference and refuse the rest.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

sys.dont_write_bytecode = True
SOURCE = Path("custom_components/roadplanner_mcp/video_asset.py")
_spec = spec_from_file_location("video_asset", SOURCE)
assert _spec and _spec.loader
video = module_from_spec(_spec)
sys.modules[_spec.name] = video
_spec.loader.exec_module(video)

ITEM = {
    "id": "01ABCDEF!123",
    "name": "20260717_Elchpark.mp4",
    "mime_type": "video/mp4",
    "size": 84_000_000,
    "duration": 42.5,
    "width": 1920,
    "height": 1080,
    "capture_time": "2026-07-17T14:12:03Z",
}


def verify_a_video_is_recognised_by_either_signal() -> None:
    """Neither the mime type nor the name is reliable on its own."""
    assert video.is_video("clip.MOV", "application/octet-stream")
    assert video.is_video("no-extension", "video/quicktime")
    assert not video.is_video("urlaub.mp4.txt", "text/plain")
    assert not video.is_video("foto.jpg", "image/jpeg")
    assert not video.is_video("", "")


def verify_the_account_is_always_written() -> None:
    """One account today, and a field that can hold a second.

    Retrofitting an account onto stored records is a migration; writing
    it now is a field. A shape that can only express one account is
    exactly what has to be undone later.
    """
    asset = video.build_asset(ITEM)
    assert asset["source"] == "onedrive"
    assert asset["source_account_id"] == video.DEFAULT_ACCOUNT_ID
    other = video.build_asset(ITEM, source="onedrive", account_id="zweitkonto")
    assert other["source_account_id"] == "zweitkonto"
    # Same file, different account: still addressable apart.
    assert other["source_item_id"] == asset["source_item_id"]


def verify_the_two_analysis_states_are_separate() -> None:
    """One is local and free, the other is a paid call to a model.

    A single state could not say "looked at locally, never sent" - which
    is the state every asset is in before somebody agrees to a price.
    """
    asset = video.build_asset(ITEM)
    assert asset["technical_analysis_state"] == video.STATE_PENDING
    assert asset["semantic_analysis_state"] == video.STATE_PENDING
    assert asset["selected_segments"] == []
    assert asset["media_type"] == "video"


def verify_a_rotated_phone_clip_is_not_landscape() -> None:
    """A phone writes a landscape frame plus "rotate 90".

    Ignoring that produces a film with a sideways clip in it, which is a
    defect that only shows up in the finished video.
    """
    assert video.orientation_of(1920, 1080) == "landscape"
    assert video.orientation_of(1080, 1920) == "portrait"
    assert video.orientation_of(1920, 1080, 90) == "portrait"
    assert video.orientation_of(1920, 1080, 270) == "portrait"
    assert video.orientation_of(1920, 1080, 180) == "landscape"
    # Nothing usable is still an answer rather than a crash.
    assert video.orientation_of(None, None) == "landscape"
    upright = video.build_asset({**ITEM, "rotation": 90})
    assert upright["orientation"] == "portrait"
    assert upright["rotation"] == 90


def verify_the_same_recording_is_the_same_asset() -> None:
    """Re-listed after a folder move, it must not pay twice."""
    first = video.build_asset(ITEM)
    moved = video.build_asset({**ITEM, "id": "01ZZZ!999"})
    assert first["content_hash"] == moved["content_hash"]
    # A different recording is a different asset.
    other = video.build_asset({**ITEM, "name": "20260718_Fjord.mp4"})
    assert other["content_hash"] != first["content_hash"]
    # The provider's own hash wins where there is one: it survives a
    # rename, which the derived key does not.
    named = video.build_asset({**ITEM, "content_hash": "abc123", "name": "x.mp4"})
    renamed = video.build_asset({**ITEM, "content_hash": "abc123", "name": "y.mp4"})
    assert named["content_hash"] == renamed["content_hash"] == "abc123"


def verify_the_analysis_cache_is_keyed_on_what_changes_the_answer() -> None:
    asset = video.build_asset(ITEM)
    base = video.cache_key(asset, model="m", schema_version=1)
    assert base == video.cache_key(asset, model="m", schema_version=1)
    assert base != video.cache_key(asset, model="other", schema_version=1)
    assert base != video.cache_key(asset, model="m", schema_version=2)
    assert base != video.cache_key(asset, model="m", schema_version=1, sampling="fine")
    # A rename must NOT move it, or every render would pay again.
    renamed = video.build_asset({**ITEM, "content_hash": "abc", "name": "neu.mp4"})
    original = video.build_asset({**ITEM, "content_hash": "abc"})
    assert video.cache_key(renamed, model="m", schema_version=1) == video.cache_key(
        original, model="m", schema_version=1
    )


def verify_a_long_recording_is_marked_for_cutting_not_refused() -> None:
    """Somebody forgot to stop recording. That is not a broken file."""
    short = video.build_asset({**ITEM, "duration": 12})
    assert short["needs_segmentation"] is False
    long_clip = video.build_asset({**ITEM, "duration": 240})
    assert long_clip["needs_segmentation"] is True
    assert long_clip["duration"] == 240.0


def verify_what_cannot_be_stored_is_refused_with_a_reason() -> None:
    """A half-formed asset in the library is worse than a skipped file.

    It would be counted, shown, and eventually sent somewhere.
    """
    cases = {
        "keine ID": {**ITEM, "id": ""},
        "kein Video": {**ITEM, "name": "foto.jpg", "mime_type": "image/jpeg"},
        "zu lang": {**ITEM, "duration": 7200},
        "zu groß": {**ITEM, "size": 9 * 1024 * 1024 * 1024},
        "Pfad als ID": {**ITEM, "id": "../../etc/passwd"},
    }
    for label, item in cases.items():
        try:
            video.build_asset(item)
        except video.VideoAssetError:
            continue
        raise AssertionError(f"{label} haette abgelehnt werden muessen")
    for junk in (None, "text", 42, []):
        try:
            video.build_asset(junk)
        except video.VideoAssetError:
            continue
        raise AssertionError(f"{junk!r} haette abgelehnt werden muessen")


def verify_it_reaches_nothing() -> None:
    """It normalises a listing. No network, no file, no provider."""
    text = SOURCE.read_text(encoding="utf-8")
    body = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )
    for forbidden in ("import aiohttp", "requests", "open(", "subprocess", "homeassistant"):
        assert forbidden not in body, f"das Modul darf {forbidden} nicht benutzen"


for check in (
    verify_a_video_is_recognised_by_either_signal,
    verify_the_account_is_always_written,
    verify_the_two_analysis_states_are_separate,
    verify_a_rotated_phone_clip_is_not_landscape,
    verify_the_same_recording_is_the_same_asset,
    verify_the_analysis_cache_is_keyed_on_what_changes_the_answer,
    verify_a_long_recording_is_marked_for_cutting_not_refused,
    verify_what_cannot_be_stored_is_refused_with_a_reason,
    verify_it_reaches_nothing,
):
    check()

print("Video asset tests passed.")
