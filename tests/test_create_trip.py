"""RP-410: creating a trip from the panel, against the REAL store.

Until now a second trip could only exist by hand-crafting directories on
the filesystem: the UI's whole multi-trip surface (list, view, activate)
managed trips no user could create. These tests pin the new primitive at
every layer that matters:

- the id is DERIVED, never typed: transliteration, collapsing, capping,
  collision suffixes, and a uuid fallback when nothing latin survives;
- creating without activating leaves the active trip byte-identical;
- the created document carries a correct content_hash, because the one
  observable symptom of a wrong hash is ``set_active_trip`` refusing the
  trip later with an error that names neither cause nor file;
- a failed write leaves NO directory fragment behind - ``list_trips``
  would show it and activation would crash on it.
"""
from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))

from simulation_harness import (  # noqa: E402
    ValidationError,
    check_invariants,
    load,
    make_store,
    revision,
)

trip_repository = load("trip_repository")
json_io = load("json_io")


def _pointer(store) -> str:
    return json.loads(store.pointer_path.read_text(encoding="utf-8"))["active_trip"]


def _trip_ids(store) -> set[str]:
    return {trip["id"] for trip in store.list_trips()["trips"]}


def verify_the_slug_table() -> None:
    cases = {
        "Nordkap 2027": "nordkap-2027",
        "Reise nach Österreich": "reise-nach-oesterreich",
        "Straße & Meer": "strasse-meer",
        # The CAPITAL sharp s: translate runs before lower(), so without
        # its own table entry it would sneak through as ß and be dropped.
        "STRAẞE": "strasse",
        "2027 Nordkap": "2027-nordkap",
        "  Ostsee   —   Rundfahrt!  ": "ostsee-rundfahrt",
        "ÄÖÜ": "aeoeue",
        "Путешествие": "",
        "": "",
        "a" * 300: "a" * 100,
    }
    for title, expected in cases.items():
        actual = trip_repository._slug_from_title(title)
        assert actual == expected, (title, actual, expected)
    # Whatever survives is always a valid identifier or empty - the
    # fallback and validate_identifier cover the empty case.
    for hostile in ("../../etc", "a/b\\c", "x\x00y", ".hidden", "-lead"):
        slug = trip_repository._slug_from_title(hostile)
        assert "/" not in slug and "\\" not in slug and ".." not in slug, slug
        assert not slug or slug[0].isalnum(), slug


def verify_create_without_activation_changes_nothing_active() -> None:
    with tempfile.TemporaryDirectory() as base:
        store = make_store(Path(base))
        before_pointer = _pointer(store)
        before_revision = revision(store)
        before_document = store.load_trip()

        result = store.create_trip(title="Nordkap 2027", actor="test")
        assert result["trip_id"] == "nordkap-2027"
        assert result["title"] == "Nordkap 2027"
        assert result["activated"] is False
        assert result["revision"] == 1
        # The manager only pushes the coordinator payload for "changed"
        # results. An inactive creation changes nothing an entity shows -
        # and an activation MUST say changed, or Home Assistant keeps
        # showing the previous trip until an unrelated refresh.
        assert result["changed"] is False

        trip_dir = Path(base) / "roadbook" / "trips" / "nordkap-2027"
        assert (trip_dir / "days").is_dir()
        document = json.loads((trip_dir / "trip.json").read_text(encoding="utf-8"))
        # The user's title, not the Title-Case form derived from the id.
        assert document["trip"]["title"] == "Nordkap 2027"
        assert document["days"] == []
        assert document["metadata"]["last_operation"] == "create_trip"

        assert _pointer(store) == before_pointer
        assert revision(store) == before_revision
        assert store.load_trip() == before_document
        assert "nordkap-2027" in _trip_ids(store)
        check_invariants(store)


def verify_the_new_trip_is_immediately_activatable() -> None:
    """The content_hash proof: loading with validation must not raise."""
    with tempfile.TemporaryDirectory() as base:
        store = make_store(Path(base))
        store.create_trip(
            title="Reise nach Österreich",
            actor="test",
            status="confirmed",
            start_date="2027-06-01",
            end_date="2027-06-20",
            notes="Über die Alpen.",
        )
        switched = store.set_active_trip(trip_id="reise-nach-oesterreich")
        assert switched["active_trip"] == "reise-nach-oesterreich"
        document = store.load_trip()
        assert document["trip"]["status"] == "confirmed"
        assert document["trip"]["start_date"] == "2027-06-01"
        assert document["trip"]["end_date"] == "2027-06-20"
        assert document["trip"]["notes"] == "Über die Alpen."
        check_invariants(store)


def verify_activate_true_switches_the_pointer() -> None:
    with tempfile.TemporaryDirectory() as base:
        store = make_store(Path(base))
        result = store.create_trip(
            title="Sofort los", actor="test", activate=True,
            expected_active_trip="new-trip",
        )
        assert result["activated"] is True
        assert result["changed"] is True, (
            "ohne 'changed' schiebt der Manager den neuen Zustand nie an "
            "Home Assistant - Entitäten und andere Panels blieben auf der "
            "alten Reise stehen"
        )
        assert _pointer(store) == "sofort-los"
        assert store.load_trip()["trip"]["title"] == "Sofort los"
        check_invariants(store)


def verify_a_stale_activation_guard_removes_the_new_trip_again() -> None:
    with tempfile.TemporaryDirectory() as base:
        store = make_store(Path(base))
        try:
            store.create_trip(
                title="Verwaist", actor="test", activate=True,
                expected_active_trip="somebody-else",
            )
        except ValidationError:
            pass
        else:
            raise AssertionError("stale expected_active_trip wurde akzeptiert")
        assert _pointer(store) == "new-trip"
        assert not (Path(base) / "roadbook" / "trips" / "verwaist").exists(), (
            "die fehlgeschlagene Aktivierung hat die halbe Reise stehen lassen"
        )


def verify_the_same_title_twice_makes_two_trips() -> None:
    with tempfile.TemporaryDirectory() as base:
        store = make_store(Path(base))
        first = store.create_trip(title="Nordkap 2027", actor="test")
        first_document = json.loads(
            (Path(base) / "roadbook" / "trips" / "nordkap-2027" / "trip.json")
            .read_text(encoding="utf-8")
        )
        second = store.create_trip(title="Nordkap 2027", actor="test")
        third = store.create_trip(title="Nordkap 2027", actor="test")
        assert first["trip_id"] == "nordkap-2027"
        assert second["trip_id"] == "nordkap-2027-2"
        assert third["trip_id"] == "nordkap-2027-3"
        unchanged = json.loads(
            (Path(base) / "roadbook" / "trips" / "nordkap-2027" / "trip.json")
            .read_text(encoding="utf-8")
        )
        assert unchanged == first_document, "die erste Reise wurde angefasst"
        assert {"nordkap-2027", "nordkap-2027-2", "nordkap-2027-3"} <= _trip_ids(store)


def verify_a_titleless_trip_is_refused_without_leaving_anything() -> None:
    with tempfile.TemporaryDirectory() as base:
        store = make_store(Path(base))
        trips_dir = Path(base) / "roadbook" / "trips"
        before = sorted(path.name for path in trips_dir.iterdir())
        for bad in ("", "   ", None):
            try:
                store.create_trip(title=bad, actor="test")
            except ValidationError:
                pass
            else:
                raise AssertionError(f"Titel {bad!r} wurde akzeptiert")
        assert sorted(path.name for path in trips_dir.iterdir()) == before


def verify_a_nonlatin_title_gets_the_uuid_fallback() -> None:
    with tempfile.TemporaryDirectory() as base:
        store = make_store(Path(base))
        result = store.create_trip(title="Путешествие", actor="test")
        assert result["trip_id"].startswith("trip-")
        assert len(result["trip_id"]) == len("trip-") + 12
        # The title survives verbatim even though the id could not use it.
        assert result["title"] == "Путешествие"
        store.set_active_trip(trip_id=result["trip_id"])


def verify_a_failed_write_leaves_no_fragment() -> None:
    """Every failure point after the mkdir shares one cleanup - test two.

    The json write and the directory fsync both fail inside the same
    try; each must remove the fragment AND leave pointer and revision of
    the active trip exactly as they were.
    """
    def explode(*_args, **_kwargs):
        raise json_io.StorageError("Platte voll (simuliert)")

    for attribute in ("_write_json_atomic", "_fsync_dir"):
        with tempfile.TemporaryDirectory() as base:
            store = make_store(Path(base))
            before_pointer = _pointer(store)
            before_revision = revision(store)
            original = getattr(trip_repository, attribute)
            setattr(trip_repository, attribute, explode)
            try:
                try:
                    store.create_trip(title="Bruchpilot", actor="test")
                except json_io.StorageError:
                    pass
                else:
                    raise AssertionError(
                        f"der simulierte Fehler in {attribute} kam nicht an"
                    )
            finally:
                setattr(trip_repository, attribute, original)
            assert not (Path(base) / "roadbook" / "trips" / "bruchpilot").exists(), (
                f"{attribute}: das Verzeichnisfragment der gescheiterten "
                "Reise blieb liegen"
            )
            assert _pointer(store) == before_pointer
            assert revision(store) == before_revision
            # And the store still works afterwards.
            store.create_trip(title="Bruchpilot", actor="test")
            assert "bruchpilot" in _trip_ids(store)


def verify_the_roundtrip_into_normal_editing() -> None:
    with tempfile.TemporaryDirectory() as base:
        store = make_store(Path(base))
        store.create_trip(title="Rundreise", actor="test", activate=True)
        day = store.add_day(
            actor="test", expected_revision=revision(store),
            title="Anreise", day_date="2027-07-01",
        )
        check_invariants(store)
        store.add_stop(
            day_id=day["day"]["id"], name="Fährhafen", actor="test",
            expected_revision=revision(store), stop_type="waypoint",
        )
        check_invariants(store)
        store.update_trip(
            patch={"notes": "Endlich unterwegs."},
            actor="test", expected_revision=revision(store),
        )
        check_invariants(store)
        document = store.load_trip()
        assert document["trip"]["notes"] == "Endlich unterwegs."
        assert len(document["days"]) == 1


def verify_the_panel_gates_activation_behind_the_approver_role() -> None:
    """The backend half of the permission, not the hidden checkbox.

    Hiding the checkbox without can_activate is a courtesy; the GATE is
    the PanelPermissionError in panel.py. Deleting that block would let
    any editor hijack the active pointer through create_trip while every
    other test stayed green - so this one reads the real dispatcher.
    """
    import ast

    panel_path = (
        Path(__file__).resolve().parents[1]
        / "custom_components" / "roadplanner_mcp" / "panel.py"
    )
    tree = ast.parse(panel_path.read_text(encoding="utf-8"))

    def names(node) -> set[str]:
        return {
            inner.id for inner in ast.walk(node) if isinstance(inner, ast.Name)
        } | {
            inner.value
            for inner in ast.walk(node)
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str)
        }

    # The action is reachable and edit-gated.
    tables: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in (
                    "_ACTIONS", "_EDIT_ACTIONS", "_APPROVAL_ACTIONS",
                ):
                    tables[target.id] = {
                        el.value
                        for el in ast.walk(node.value)
                        if isinstance(el, ast.Constant) and isinstance(el.value, str)
                    }
    assert "create_trip" in tables["_ACTIONS"], "create_trip ist nicht erreichbar"
    assert "create_trip" in tables["_EDIT_ACTIONS"], "create_trip ist kein Edit"
    assert "create_trip" not in tables["_APPROVAL_ACTIONS"], (
        "create_trip pauschal hinter die Freigaberolle zu legen würde "
        "Editoren das blosse Anlegen nehmen"
    )

    # Inside the dispatcher: the create_trip branch raises
    # PanelPermissionError on a can_approve check before calling the
    # manager.
    for node in ast.walk(tree):
        if not (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)):
            continue
        comparators = names(node.test)
        if "action" not in comparators or "create_trip" not in comparators:
            continue
        branch = names(node)
        assert "PanelPermissionError" in branch and "can_approve" in branch, (
            "der create_trip-Zweig prüft can_approve nicht mehr - jeder "
            "Editor könnte über activate=true den aktiven Zeiger kapern"
        )
        return
    raise AssertionError("kein create_trip-Zweig im Panel-Dispatcher gefunden")


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Create trip tests passed.")


if __name__ == "__main__":
    main()
