"""The film endpoint streams, seeks, and only offers a download on request.

Live finding V2 (#376), measured against a 118 936 697-byte film:

    Range: bytes=1000000-1000999
    -> 200 OK          (not 206)
    -> accept-ranges:  (missing)
    -> content-length: 118936697

`video.currentTime = 26` therefore snapped back to 0 until the whole file
had arrived - 584 MB before minute eight of the real film - so the player,
which exists for a wall-mounted tablet, had no timeline at all. The
handler also read the entire film into one `bytes` object inside Home
Assistant, and forbade caching, so every open transferred it again.

`web.FileResponse` answers `Range` with 206 itself, streams with
sendfile, and holds nothing. The other half is the disposition: the same
URL is the player's `<video src>` AND the download link, so `attachment`
is now sent only when a download was actually asked for.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "roadplanner_mcp"
PACKAGE_NAME = "rp_library_endpoint"

if PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(PACKAGE_ROOT)]
    sys.modules[PACKAGE_NAME] = package

for name, attributes in (
    ("homeassistant", {}),
    ("homeassistant.core", {"HomeAssistant": object, "callback": lambda fn: fn}),
    ("homeassistant.components", {}),
    ("homeassistant.components.http", {"HomeAssistantView": type("HomeAssistantView", (), {})}),
    ("homeassistant.helpers", {}),
    ("homeassistant.helpers.aiohttp_client", {"async_get_clientsession": lambda _hass: None}),
    ("homeassistant.util", {}),
    ("aiohttp", {
        "ClientError": type("ClientError", (Exception,), {}),
        "ClientSession": object,
        "ClientTimeout": type("ClientTimeout", (), {"__init__": lambda self, **kw: None}),
    }),
    ("aiohttp.web", {}),
):
    if name not in sys.modules:
        module = types.ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        sys.modules[name] = module

# `web` is used for exceptions and responses only; a namespace with the
# names the module touches is enough to import it.
web_stub = sys.modules["aiohttp.web"]
for missing in ("Request", "Response", "StreamResponse", "FileResponse"):
    if not hasattr(web_stub, missing):
        setattr(web_stub, missing, type(missing, (), {}))
for missing in ("HTTPBadRequest", "HTTPNotFound"):
    if not hasattr(web_stub, missing):
        setattr(web_stub, missing, type(missing, (Exception,), {}))
sys.modules["aiohttp"].web = web_stub


def load(name: str):
    full = f"{PACKAGE_NAME}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = spec_from_file_location(full, PACKAGE_ROOT / f"{name}.py")
    module = module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


endpoint = load("trip_video_library_http")
video_export = load("trip_video_export")

SOURCE = (PACKAGE_ROOT / "trip_video_library_http.py").read_text(encoding="utf-8")


class FakeRequest:
    def __init__(self, query: dict[str, str] | None = None) -> None:
        self.query = dict(query or {})


def verify_the_film_is_never_read_into_memory() -> None:
    # The CALL, not the word: the handler's docstring explains what it
    # used to do, and searching for the bare name flagged that
    # explanation - the same mistake as reading a module's own comment as
    # a rule violation.
    assert "async_add_executor_job(path.read_bytes)" not in SOURCE, (
        "the whole film in one bytes object is what put 584 MB into Home Assistant"
    )
    assert "body=video_bytes" not in SOURCE, SOURCE.count("body=")
    assert "web.FileResponse(" in SOURCE, (
        "only a file response answers Range with 206 and streams with sendfile"
    )


def verify_the_film_may_be_cached_between_openings() -> None:
    # The HEADER, not the word: the docstring explains why it is gone.
    headers = SOURCE.split('headers = {')[1].split("}")[0]
    assert "no-store" not in headers, "no-store re-downloaded the whole film on every open"
    assert "private, max-age=" in SOURCE, "a film behind a fixed name may be cached"


def verify_playback_gets_no_attachment_disposition() -> None:
    assert endpoint._wants_download(FakeRequest()) is False
    assert endpoint._wants_download(FakeRequest({"download": "0"})) is False
    assert endpoint._wants_download(FakeRequest({"download": ""})) is False
    assert endpoint._wants_download(FakeRequest({"other": "1"})) is False


def verify_an_explicit_download_still_gets_one() -> None:
    for value in ("1", "true", "TRUE", "yes", " 1 "):
        assert endpoint._wants_download(FakeRequest({"download": value})) is True, value


def verify_the_url_is_built_in_one_place_and_says_its_purpose() -> None:
    assert (
        video_export.library_url("abc.mp4")
        == "/api/roadplanner/trip_video_library/abc.mp4"
    )
    assert (
        video_export.library_url("abc.mp4", download=True)
        == "/api/roadplanner/trip_video_library/abc.mp4?download=1"
    )
    # And nothing builds that path by hand any more, which is what let a
    # download link and a video source drift apart in the first place.
    source = (PACKAGE_ROOT / "trip_video_export.py").read_text(encoding="utf-8")
    handmade = source.count('f"/api/roadplanner/trip_video_library/')
    assert handmade == 0, f"{handmade} hand-built library URLs left"


def verify_a_filename_read_back_from_a_url_drops_the_query() -> None:
    """The download suffix must not become part of a filename.

    The player's record stores a URL, and two places read the filename
    back out of it: the prune protection that keeps a trip's film alive,
    and the recovery that finds the film again for the mux. A filename
    carrying "?download=1" matches nothing on disk, so the film would
    read as gone and the protection would guard a name that does not
    exist.
    """
    player_film = load("player_film")
    assert player_film.library_filename("/api/roadplanner/trip_video_library/abc.mp4") == "abc.mp4"
    assert (
        player_film.library_filename("/api/roadplanner/trip_video_library/abc.mp4?download=1")
        == "abc.mp4"
    )
    assert player_film.library_filename("") == ""


CHECKS = [
    verify_the_film_is_never_read_into_memory,
    verify_the_film_may_be_cached_between_openings,
    verify_playback_gets_no_attachment_disposition,
    verify_an_explicit_download_still_gets_one,
    verify_the_url_is_built_in_one_place_and_says_its_purpose,
    verify_a_filename_read_back_from_a_url_drops_the_query,
]


def verify_every_check_in_this_module_is_registered() -> None:
    declared = {
        name
        for name, value in globals().items()
        if name.startswith("verify_") and callable(value)
        and name != "verify_every_check_in_this_module_is_registered"
    }
    registered = {check.__name__ for check in CHECKS}
    assert declared == registered, f"not run: {sorted(declared - registered)}"


if __name__ == "__main__":
    verify_every_check_in_this_module_is_registered()
    for check in CHECKS:
        check()
        print(f"ok - {check.__name__}")
    print(f"\n{len(CHECKS)} checks passed")
