"""The export library may live in /media, so films stay out of the backup.

Handover from ha-automatic Doc 92 §7: every Home Assistant backup packs
up `/config` and the backup goes to OneDrive. Seventeen finished films in
`/config/.roadplanner_trip_videos/` were 2.8 GB of a 4.1 GB nightly
upload - for data that is already on disk and can be rendered again. The
library therefore moved to `/media/roadplanner_trip_videos`, which needs
a Home Assistant login but is not part of a backup.

That makes this the one Roadplanner directory allowed outside `/config`,
and the escape protection still has to hold: two known roots, nothing
else. The checks below are about the SYMPTOM - what the normalizer
accepts and where the resolved library actually ends up - not about the
wording of the code that does it.

The last check is the deployable boundary: the renderer add-on must stay
out of this entirely. It knows `/share` and nothing else, so it needs no
media mapping - the integration copies the finished film from `/share`
into the library itself.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "roadplanner_mcp"
PACKAGE_NAME = "rp_library_location"

if PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(PACKAGE_ROOT)]
    sys.modules[PACKAGE_NAME] = package


def load(name: str):
    spec = spec_from_file_location(
        f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py"
    )
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


path_utils = load("path_utils")
const = load("const")

CONFIG_DIR = "/config"
MEDIA_DIRS = {"local": "/media"}
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        FAILURES.append(f"{label}{': ' + detail if detail else ''}")


def verify_the_default_library_is_outside_the_backed_up_config_dir() -> None:
    """The whole point of the move: /config is what gets backed up."""
    default = const.DEFAULT_TRIP_VIDEO_LIBRARY_PATH
    check(
        "default library must be an absolute path outside /config",
        Path(default).is_absolute(),
        f"got {default!r} - a relative path resolves inside /config again",
    )
    resolved = path_utils.resolve_library_path(
        CONFIG_DIR, default, media_dirs=MEDIA_DIRS
    )
    check(
        "default library must not resolve inside the config dir",
        not str(resolved).startswith("/config/"),
        f"resolved to {resolved}",
    )
    check(
        "default library must resolve inside a media dir",
        str(resolved).startswith("/media/"),
        f"resolved to {resolved}",
    )


def verify_a_media_path_is_accepted_and_a_stray_path_is_not() -> None:
    """Two known roots - not 'any absolute path on the host'."""
    accepted = path_utils.normalize_library_path(
        CONFIG_DIR, "/media/roadplanner_trip_videos", media_dirs=MEDIA_DIRS
    )
    check(
        "a path inside a configured media dir is accepted as absolute",
        accepted == "/media/roadplanner_trip_videos",
        f"got {accepted!r}",
    )
    for stray in ("/etc/roadplanner", "/share/roadplanner", "/media/../etc"):
        try:
            path_utils.normalize_library_path(
                CONFIG_DIR, stray, media_dirs=MEDIA_DIRS
            )
        except path_utils.PathValidationError:
            continue
        FAILURES.append(f"{stray!r} must be rejected - it is in neither root")


def verify_the_media_root_itself_is_not_a_library() -> None:
    """Pruning globs *.mp4 in this folder; /media itself is not ours."""
    try:
        path_utils.normalize_library_path(
            CONFIG_DIR, "/media", media_dirs=MEDIA_DIRS
        )
    except path_utils.PathValidationError:
        return
    FAILURES.append("/media itself must not be accepted as the library")


def verify_a_media_dir_that_is_not_configured_is_not_a_free_pass() -> None:
    """Accepted because Home Assistant names it, not because of its spelling."""
    try:
        path_utils.normalize_library_path(
            CONFIG_DIR, "/media/roadplanner_trip_videos", media_dirs={}
        )
    except path_utils.PathValidationError:
        return
    FAILURES.append(
        "/media must be rejected when no media dir is configured - the "
        "allowance comes from hass.config.media_dirs, not from the prefix"
    )


def verify_a_config_relative_library_still_works() -> None:
    """Nobody is forced to move; the old shape stays valid."""
    legacy = const.LEGACY_TRIP_VIDEO_LIBRARY_PATH
    normalized = path_utils.normalize_library_path(
        CONFIG_DIR, legacy, media_dirs=MEDIA_DIRS
    )
    check(
        "a config-relative library stays config-relative",
        normalized == legacy,
        f"got {normalized!r}",
    )
    resolved = path_utils.resolve_library_path(
        CONFIG_DIR, legacy, media_dirs=MEDIA_DIRS
    )
    check(
        "a config-relative library resolves under the config dir",
        resolved == Path(f"/config/{legacy}"),
        f"resolved to {resolved}",
    )


def verify_the_www_ban_survived_the_change() -> None:
    """Private data still must not land in the public www folder."""
    try:
        path_utils.normalize_library_path(
            CONFIG_DIR, "www/videos", media_dirs=MEDIA_DIRS
        )
    except path_utils.PathValidationError:
        return
    FAILURES.append("www must stay banned for the library")


def verify_an_untouched_install_follows_the_move() -> None:
    """The old default was never chosen, so it is not a preference."""
    source = (PACKAGE_ROOT / "__init__.py").read_text(encoding="utf-8")
    check(
        "the config-entry migration must handle the library path",
        "_migrate_trip_video_library_path(effective)" in source,
        "a stored '.roadplanner_trip_videos' would otherwise keep the "
        "library inside the backup after the update",
    )
    namespace: dict = {
        "CONF_TRIP_VIDEO_LIBRARY_PATH": const.CONF_TRIP_VIDEO_LIBRARY_PATH,
        "LEGACY_TRIP_VIDEO_LIBRARY_PATH": const.LEGACY_TRIP_VIDEO_LIBRARY_PATH,
        "DEFAULT_TRIP_VIDEO_LIBRARY_PATH": const.DEFAULT_TRIP_VIDEO_LIBRARY_PATH,
        "Any": object,
    }
    start = source.index("def _migrate_trip_video_library_path(")
    end = source.index("\nasync def async_migrate_entry(", start)
    exec(compile(source[start:end], "migration", "exec"), namespace)
    migrate = namespace["_migrate_trip_video_library_path"]
    key = const.CONF_TRIP_VIDEO_LIBRARY_PATH

    untouched = {key: const.LEGACY_TRIP_VIDEO_LIBRARY_PATH}
    migrate(untouched)
    check(
        "an install on the old default follows the move",
        untouched[key] == const.DEFAULT_TRIP_VIDEO_LIBRARY_PATH,
        f"got {untouched[key]!r}",
    )

    chosen = {key: ".my_own_films"}
    migrate(chosen)
    check(
        "a path the user chose is left alone",
        chosen[key] == ".my_own_films",
        f"got {chosen[key]!r}",
    )


def verify_the_renderer_needs_no_media_mapping() -> None:
    """The deployable boundary: the renderer only ever knows /share.

    The integration copies the finished film out of the exchange folder
    into the library, so the add-on never touches /media or /config. A
    media mapping here would be permission the renderer does not need.
    """
    config = (ROOT / "apps/roadplanner_renderer/config.yaml").read_text(
        encoding="utf-8"
    )
    mapped = [
        line.strip().removeprefix("- type:").strip()
        for line in config.splitlines()
        if line.strip().startswith("- type:")
    ]
    check(
        "the renderer must map the share folder and nothing else",
        mapped == ["share"],
        f"mapped volumes: {mapped}",
    )
    sources = sorted((ROOT / "apps/roadplanner_renderer/src").rglob("*.mjs"))
    check("renderer sources must be readable", bool(sources))
    for source in sources:
        text = source.read_text(encoding="utf-8")
        for forbidden in ("/media/", "roadplanner_trip_videos"):
            check(
                f"{source.name} must not reach into the library",
                forbidden not in text,
                f"found {forbidden!r}",
            )


for check_fn in (
    verify_the_default_library_is_outside_the_backed_up_config_dir,
    verify_a_media_path_is_accepted_and_a_stray_path_is_not,
    verify_the_media_root_itself_is_not_a_library,
    verify_a_media_dir_that_is_not_configured_is_not_a_free_pass,
    verify_a_config_relative_library_still_works,
    verify_the_www_ban_survived_the_change,
    verify_an_untouched_install_follows_the_move,
    verify_the_renderer_needs_no_media_mapping,
):
    check_fn()

if FAILURES:
    for failure in FAILURES:
        print(f"FAIL: {failure}")
    raise SystemExit(1)

print("Trip export library location tests passed.")
