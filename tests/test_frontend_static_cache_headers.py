"""Behavioral tests for the panel's always-revalidating static file view.

Root cause this exists to fix: only the panel ENTRY file
(roadplanner-panel.js) was ever cache-busted, via the "?v=<version>" query
parameter panel_custom appends to module_url. Its own static imports of
./features/*.js and ./lib/*.js carry no such parameter, so a browser
(especially a mobile Companion app WebView) could keep serving a stale,
heuristically-cached submodule after a HACS update - the entry file looks
fresh, but Stellplätze (or any other feature) silently keeps running
previous-release code with no error. This view sends an explicit
Cache-Control: no-cache on every file instead, forcing revalidation on
every load regardless of the URL.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import tempfile
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_frontend_static_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package


class _HTTPError(Exception):
    pass


aiohttp_stub = types.ModuleType("aiohttp")
web_stub = types.ModuleType("aiohttp.web")


class _HTTPNotFound(_HTTPError):
    pass


class _HTTPForbidden(_HTTPError):
    pass


class _FileResponse:
    def __init__(self, path, *, headers=None):
        self.path = path
        self.headers = headers or {}


web_stub.HTTPNotFound = _HTTPNotFound
web_stub.HTTPForbidden = _HTTPForbidden
web_stub.FileResponse = _FileResponse
web_stub.StreamResponse = object
web_stub.Request = object
aiohttp_stub.web = web_stub
sys.modules["aiohttp"] = aiohttp_stub
sys.modules["aiohttp.web"] = web_stub

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object
sys.modules["homeassistant.core"] = ha_core
ha_components = types.ModuleType("homeassistant.components")
ha_components.__path__ = []
sys.modules.setdefault("homeassistant.components", ha_components)
ha_http = types.ModuleType("homeassistant.components.http")


class _HomeAssistantView:
    requires_auth = True


ha_http.HomeAssistantView = _HomeAssistantView
sys.modules["homeassistant.components.http"] = ha_http


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


module = load("frontend_static_http")


def _run(coro):
    return asyncio.run(coro)


def verify_serves_a_real_file_with_no_cache_header() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "features").mkdir()
        (root / "features" / "pitches.js").write_text("export const x = 1;")
        view = module.RoadplannerFrontendStaticView("/x/{filename:.+}", "test", root)
        response = _run(view.get(None, "features/pitches.js"))
        assert response.path == (root / "features" / "pitches.js").resolve()
        assert response.headers["Cache-Control"] == "no-cache", (
            "without this header, browsers heuristically cache submodules "
            "and silently keep serving a stale release after an update"
        )
        assert response.headers["X-Content-Type-Options"] == "nosniff"


def verify_missing_file_raises_not_found() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        view = module.RoadplannerFrontendStaticView("/x/{filename:.+}", "test", Path(tmp))
        try:
            _run(view.get(None, "does-not-exist.js"))
        except module.web.HTTPNotFound:
            pass
        else:
            raise AssertionError("a missing file must 404")


def verify_path_traversal_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "frontend"
        root.mkdir()
        secret = Path(tmp) / "secret.txt"
        secret.write_text("do not serve me")
        view = module.RoadplannerFrontendStaticView("/x/{filename:.+}", "test", root)
        for traversal in ("../secret.txt", "../../secret.txt", "sub/../../secret.txt"):
            try:
                _run(view.get(None, traversal))
            except (module.web.HTTPForbidden, module.web.HTTPNotFound):
                continue
            raise AssertionError(f"path traversal must be rejected: {traversal}")


def verify_directory_request_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "features").mkdir()
        view = module.RoadplannerFrontendStaticView("/x/{filename:.+}", "test", root)
        try:
            _run(view.get(None, "features"))
        except module.web.HTTPNotFound:
            pass
        else:
            raise AssertionError("a directory must not be served as a file")


def verify_view_does_not_require_auth() -> None:
    assert module.RoadplannerFrontendStaticView.requires_auth is False, (
        "matches the previous plain static-path registration - panel JS/CSS "
        "is public-ish, like Home Assistant's own frontend assets"
    )


def verify_panel_wiring_uses_the_new_view() -> None:
    panel_source = (PACKAGE_ROOT / "panel.py").read_text(encoding="utf-8")
    assert "async_register_frontend_static_view" in panel_source
    assert "StaticPathConfig" not in panel_source, (
        "the plain static-path helper sends no explicit Cache-Control at "
        "all and must not come back - see frontend_static_http.py"
    )


if __name__ == "__main__":
    verify_serves_a_real_file_with_no_cache_header()
    verify_missing_file_raises_not_found()
    verify_path_traversal_is_rejected()
    verify_directory_request_is_rejected()
    verify_view_does_not_require_auth()
    verify_panel_wiring_uses_the_new_view()
    print("Frontend static cache-header tests passed.")
