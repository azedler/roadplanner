"""Behaviour of the file contract between Roadplanner and the renderer app.

The protocol is the whole product of this PoC: there is no connection to
inspect, no handshake, no error the other side can return. Whatever these
rules allow is what the two processes can say to each other, so they are
tested directly rather than through either implementation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
from pathlib import Path
import sys

# Loading integration modules by path would otherwise leave a
# __pycache__ inside the shipped integration directory, which the
# repository validator rightly refuses.
sys.dont_write_bytecode = True

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"roadplanner_{name}", PACKAGE_ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


protocol = load("renderer_app_protocol")
NOW = datetime(2026, 8, 6, 14, 0, 0, tzinfo=timezone.utc)


def _job(**overrides):
    job = protocol.build_job(job_id=protocol.new_job_id(), now=NOW)
    job.update(overrides)
    return job


def verify_a_job_carries_data_and_never_a_path() -> None:
    """The job must not be able to name a file, a command or a URL."""
    job = _job()
    flat = repr(job)
    for forbidden in ("/", "\\", "output_path", "command", "http"):
        assert forbidden not in flat, f"{forbidden!r} darf nicht im Auftrag stehen: {flat}"
    assert set(job["input"]) == {"message"}, job["input"]


def verify_the_filename_comes_only_from_a_generated_id() -> None:
    job_id = protocol.new_job_id()
    assert protocol.job_filename(job_id) == f"{job_id}.json"
    for hostile in ("../escape", "a/b", "", "x" * 200, "'; rm -rf /"):
        try:
            protocol.job_filename(hostile)
        except protocol.RendererProtocolError:
            continue
        raise AssertionError(f"Job-ID wurde akzeptiert: {hostile!r}")


def verify_an_unknown_protocol_version_is_refused_not_interpreted() -> None:
    for field in ("schema_version", "protocol_version"):
        payload = _job(**{field: 99})
        try:
            protocol.validate_job(payload, now=NOW)
        except protocol.RendererProtocolError as err:
            assert "nicht unterstützt" in str(err), err
            continue
        raise AssertionError(f"{field}=99 wurde akzeptiert")


def verify_an_expired_job_is_not_processed() -> None:
    job = protocol.build_job(job_id=protocol.new_job_id(), now=NOW, ttl_seconds=60)
    # Valid a minute before it expires...
    protocol.validate_job(job, now=NOW + timedelta(seconds=30))
    # ...and refused after.
    try:
        protocol.validate_job(job, now=NOW + timedelta(seconds=61))
    except protocol.RendererProtocolError as err:
        assert "abgelaufen" in str(err), err
        return
    raise AssertionError("Ein abgelaufener Auftrag wurde akzeptiert")


def verify_a_stale_heartbeat_never_reads_as_ready() -> None:
    """A stopped app leaves its last heartbeat lying in the folder.

    Showing that as "bereit" is the specific failure this guards against -
    the file is still perfectly valid, only old.
    """
    beat = {
        "schema_version": 1,
        "protocol_version": 1,
        "renderer": protocol.RENDERER_NAME,
        "app_version": "0.1.0-poc.1",
        "state": "ready",
        "heartbeat_at": protocol.format_time(NOW),
        "capabilities": ["ping"],
    }
    fresh = protocol.validate_heartbeat(beat, now=NOW + timedelta(seconds=5))
    assert fresh["online"] is True and fresh["fresh"] is True

    stale = protocol.validate_heartbeat(
        beat, now=NOW + timedelta(seconds=protocol.HEARTBEAT_MAX_AGE_SECONDS + 1)
    )
    assert stale["online"] is False, stale
    assert stale["state"] == "ready", "der gemeldete Zustand bleibt sichtbar"

    # A clock running ahead is no more trustworthy than one lagging behind.
    skewed = protocol.validate_heartbeat(beat, now=NOW - timedelta(minutes=5))
    assert skewed["online"] is False, skewed


def verify_a_stopping_app_is_not_online() -> None:
    for state, expected in (("ready", True), ("degraded", True), ("starting", False), ("stopping", False)):
        beat = protocol.validate_heartbeat(
            {
                "schema_version": 1,
                "protocol_version": 1,
                "state": state,
                "heartbeat_at": protocol.format_time(NOW),
            },
            now=NOW,
        )
        assert beat["online"] is expected, f"{state} → online={beat['online']}"


def verify_terminal_states_are_final() -> None:
    """A restarted worker must not be able to revive a finished job.

    Without this the panel would poll a job that will never report again,
    which is exactly the "hanging job" the PoC has to rule out.
    """
    for terminal in ("completed", "failed", "expired"):
        assert protocol.state_transition_allowed(terminal, terminal) is True
        for target in ("queued", "claimed", "running"):
            assert protocol.state_transition_allowed(terminal, target) is False, (
                f"{terminal} → {target} darf nicht erlaubt sein"
            )
    assert protocol.state_transition_allowed("running", "completed") is True
    assert protocol.state_transition_allowed("queued", "running") is True


def verify_a_result_only_accepts_known_artifacts() -> None:
    body = b"hallo"
    good = {
        "schema_version": 1,
        "protocol_version": 1,
        "job_id": protocol.new_job_id(),
        "state": "completed",
        "artifacts": [
            {
                "kind": "text",
                "filename": protocol.ARTIFACT_TEXT,
                "size_bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        ],
    }
    parsed = protocol.validate_result(good, job_id=good["job_id"])
    assert parsed["artifacts"][0]["kind"] == "text"

    for hostile in ("../../etc/passwd", "result.json", "evil.sh", "/abs/path.txt"):
        payload = {**good, "artifacts": [{**good["artifacts"][0], "filename": hostile}]}
        try:
            protocol.validate_result(payload, job_id=good["job_id"])
        except protocol.RendererProtocolError:
            continue
        raise AssertionError(f"Artefaktname wurde akzeptiert: {hostile!r}")


def verify_declared_bytes_are_checked_against_real_bytes() -> None:
    body = b"echte Bytes"
    declared = {
        "filename": protocol.ARTIFACT_TEXT,
        "size_bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    assert protocol.verify_artifact(body, declared) == body

    for wrong in (b"andere Bytes!", b"echte Byte"):
        try:
            protocol.verify_artifact(wrong, declared)
        except protocol.RendererProtocolError:
            continue
        raise AssertionError(f"Manipulierte Bytes akzeptiert: {wrong!r}")


def verify_oversized_json_is_refused_before_it_is_parsed() -> None:
    """A broken writer must not be able to make the reader allocate."""
    payload = b'{"x":"' + b"a" * protocol.MAX_JSON_BYTES + b'"}'
    try:
        protocol.decode_json(payload)
    except protocol.RendererProtocolError as err:
        assert "Größengrenze" in str(err), err
        return
    raise AssertionError("Übergroßes JSON wurde gelesen")


def verify_a_status_for_another_job_is_refused() -> None:
    mine, other = protocol.new_job_id(), protocol.new_job_id()
    payload = {
        "schema_version": 1,
        "protocol_version": 1,
        "job_id": other,
        "state": "running",
    }
    try:
        protocol.validate_status(payload, job_id=mine)
    except protocol.RendererProtocolError as err:
        assert "anderen Auftrag" in str(err), err
        return
    raise AssertionError("Fremder Jobstatus wurde übernommen")


def verify_timestamps_survive_a_round_trip() -> None:
    assert protocol.format_time(NOW).endswith("Z")
    assert protocol.parse_time(protocol.format_time(NOW)) == NOW
    # Malformed is None, not an exception: for a heartbeat it simply means
    # "not fresh", and the caller decides.
    for broken in ("", None, "gestern", "2026-13-45T99:99:99Z"):
        assert protocol.parse_time(broken) is None, broken


verify_a_job_carries_data_and_never_a_path()
verify_the_filename_comes_only_from_a_generated_id()
verify_an_unknown_protocol_version_is_refused_not_interpreted()
verify_an_expired_job_is_not_processed()
verify_a_stale_heartbeat_never_reads_as_ready()
verify_a_stopping_app_is_not_online()
verify_terminal_states_are_final()
verify_a_result_only_accepts_known_artifacts()
verify_declared_bytes_are_checked_against_real_bytes()
verify_oversized_json_is_refused_before_it_is_parsed()
verify_a_status_for_another_job_is_refused()
verify_timestamps_survive_a_round_trip()
print("Renderer app protocol tests passed.")
