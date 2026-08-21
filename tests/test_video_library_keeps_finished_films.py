"""A trip's finished film survives another trip's test renders.

Live report: "die Videoerstellung in der Testreise hat das Video aus der
eigentlichen Reise verworfen". It had.

The library is ONE folder for every trip, capped at ten files and
emptied oldest-first - and every excerpt, review copy and music mix goes
into it beside the films. A handful of test renders on a second trip
therefore deleted the finished film of the first, and the renderer's
exchange folder keeps its copy for only a day, so after that the film
was unrecoverable and the panel could only report that there was none.

The cap stays (each file is tens of megabytes). What changed is what it
may consider: never the file that IS a trip's film.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import types

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "roadplanner_mcp"
PACKAGE_NAME = "rp_video_library"

if PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(PACKAGE_ROOT)]
    sys.modules[PACKAGE_NAME] = package

# player_film.py needs a Home Assistant name for its type hints only.
for name, attributes in (
    ("homeassistant", {}),
    ("homeassistant.core", {"HomeAssistant": type("HomeAssistant", (), {}), "callback": lambda fn: fn}),
    ("homeassistant.helpers", {}),
    ("homeassistant.helpers.event", {"async_call_later": None, "async_track_time_interval": None}),
    ("homeassistant.helpers.aiohttp_client", {"async_get_clientsession": lambda _hass: None}),
    ("homeassistant.util", {}),
):
    if name not in sys.modules:
        module = types.ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        sys.modules[name] = module


def load(name: str):
    full = f"{PACKAGE_NAME}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, PACKAGE_ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


player_film = load("player_film")
const = load("const")


class _Library:
    """The pruning half of TripVideoExport, with its real code.

    Importing the whole exporter would drag in aiohttp and the Home
    Assistant client stack for two methods; the two methods themselves
    are taken from the production class, not reimplemented - a copy of
    the rule here would be a test that agrees with itself.
    """

    def __init__(self, library_dir: Path) -> None:
        self.library_dir = library_dir


def _install_production_methods() -> None:
    source = (PACKAGE_ROOT / "trip_video_export.py").read_text(encoding="utf-8")
    import ast

    tree = ast.parse(source)
    wanted = {"set_protected_filenames", "_protected", "_prune_library"}
    namespace: dict[str, object] = {
        "MAX_STORED_TRIP_VIDEOS": const.MAX_STORED_TRIP_VIDEOS,
        "_LOGGER": type("L", (), {"debug": staticmethod(lambda *a, **k: None)})(),
        "Callable": None,
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            # Strip the annotation that needs typing.Callable at runtime.
            for argument in node.args.args:
                argument.annotation = None
            node.returns = None
            module = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(module)
            exec(compile(module, "<production>", "exec"), namespace)  # noqa: S102
            setattr(_Library, node.name, namespace[node.name])
    missing = wanted - set(dir(_Library))
    assert not missing, f"nicht gefunden in trip_video_export.py: {missing}"


_install_production_methods()


def _library(base: Path, names: list[str]) -> _Library:
    directory = base / "videos"
    directory.mkdir(parents=True, exist_ok=True)
    for index, name in enumerate(names):
        path = directory / name
        path.write_bytes(b"x")
        # Oldest first, in the order given.
        import os

        os.utime(path, (1_000_000 + index, 1_000_000 + index))
    return _Library(directory)


def _names(library: _Library) -> set[str]:
    return {path.name for path in library.library_dir.glob("*.mp4")}


def verify_the_cap_still_removes_working_material() -> None:
    cap = const.MAX_STORED_TRIP_VIDEOS
    with tempfile.TemporaryDirectory() as base:
        names = [f"excerpt-{index:02d}.mp4" for index in range(cap + 3)]
        library = _library(Path(base), names)
        library.set_protected_filenames(None)
        library._prune_library()
        assert len(_names(library)) == cap, _names(library)
        # The three oldest went, the newest stayed.
        assert names[0] not in _names(library)
        assert names[-1] in _names(library)


def verify_a_trips_film_is_never_the_oldest_thing_deleted() -> None:
    """The reported loss, reproduced and then prevented."""
    cap = const.MAX_STORED_TRIP_VIDEOS
    film = "finnland-film.mp4"
    with tempfile.TemporaryDirectory() as base:
        # The real film first, then a test session's worth of renders.
        names = [film] + [f"test-{index:02d}.mp4" for index in range(cap + 2)]
        library = _library(Path(base), names)

        # Without protection this is exactly what happened.
        library.set_protected_filenames(None)
        library._prune_library()
        assert film not in _names(library), (
            "der Befund liesse sich nicht mehr reproduzieren - Test prüft nichts"
        )

    with tempfile.TemporaryDirectory() as base:
        library = _library(Path(base), names)
        library.set_protected_filenames(lambda: {film})
        library._prune_library()
        assert film in _names(library), "der fertige Film wurde wieder gelöscht"
        # And the cap still did its work on everything else.
        assert len(_names(library)) == cap + 1, _names(library)


def verify_an_unreadable_record_deletes_nothing() -> None:
    """Not knowing which files are films may never mean "delete freely"."""
    cap = const.MAX_STORED_TRIP_VIDEOS

    def explode() -> set[str]:
        raise OSError("Aufzeichnung nicht lesbar")

    with tempfile.TemporaryDirectory() as base:
        names = [f"film-{index:02d}.mp4" for index in range(cap + 5)]
        library = _library(Path(base), names)
        library.set_protected_filenames(explode)
        library._prune_library()
        assert len(_names(library)) == cap + 5, "unter Unsicherheit wurde gelöscht"


def verify_the_record_names_the_files_it_protects() -> None:
    """The protected set comes from the player's own record, not a copy."""
    with tempfile.TemporaryDirectory() as base:
        store = player_film.PlayerFilmStore(Path(base) / "player")
        store.initialize()
        store.save(
            {
                "finnland": {
                    "latest": {"url": "/api/roadplanner/trip_video_library/aaa.mp4"}
                },
                "test": {
                    "latest": {"url": "/api/roadplanner/trip_video_library/bbb.mp4"}
                },
                # A trip whose film was pruned before this existed: the
                # record has no url left, so there is nothing to protect.
                "alt": {"latest": {"source_path": "/share/x.mp4"}},
                "kaputt": {"latest": "nicht einmal ein Objekt"},
                "leer": {},
            }
        )
        assert store.protected_filenames() == {"aaa.mp4", "bbb.mp4"}


def verify_the_protection_is_wired_at_startup() -> None:
    """A resolver nobody hands over protects nothing."""
    setup = (PACKAGE_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert "trip_video.set_protected_filenames(player_film_store.protected_filenames)" in setup, (
        "die Bibliothek erfährt beim Start nicht mehr, welche Dateien Filme sind"
    )


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Video library protection tests passed.")


if __name__ == "__main__":
    main()
