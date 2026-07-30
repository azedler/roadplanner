"""Regression test: a pasted link in the chat must enable url_context.

Live bug report (screenshot): "Prüfe den Link" with a Komoot share link
produced "Da ich den genauen Inhalt des Komoot-Links nicht direkt live
auslesen kann..." - the assistant literally could not open the link,
because the chat's search heuristic only looked for discovery-style
phrases ("suche", "empfiehl", "in der Nähe", ...) and a pasted URL matched
none of them. But a pasted link is the strongest research signal there is:
the url_context tool exists precisely to fetch a page the user hands over.
Google-Maps links stay excluded - they are resolved deterministically from
their own URL structure and never need a fetch (mirroring
_mentions_unresolved_link's long-standing semantics in the compile path).
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SOURCE_PATH = Path("custom_components/roadplanner_mcp/assistant.py")
source = SOURCE_PATH.read_text(encoding="utf-8")
tree = ast.parse(source)


def _find_method(name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"method {name} not found")


function_node = _find_method("_should_enable_search")
# Strip the @staticmethod decorator so it compiles as a plain function.
function_node.decorator_list = []
compiled = compile(
    ast.fix_missing_locations(ast.Module(body=[function_node], type_ignores=[])),
    str(SOURCE_PATH),
    "exec",
)

# Real regex + host check from destination_intelligence (pure module).
from importlib.util import module_from_spec, spec_from_file_location  # noqa: E402
import sys  # noqa: E402

spec = spec_from_file_location(
    "roadplanner_destination_intelligence_url",
    Path("custom_components/roadplanner_mcp/destination_intelligence.py"),
)
assert spec and spec.loader
intelligence = module_from_spec(spec)
sys.modules[spec.name] = intelligence
spec.loader.exec_module(intelligence)

namespace: dict[str, Any] = {
    "_URL_RE": intelligence._URL_RE,
    "_google_maps_host": intelligence._google_maps_host,
    "urlparse": urlparse,
}
exec(compiled, namespace)
should_enable_search = namespace["_should_enable_search"]

KOMOOT_LINK = (
    "https://www.komoot.com/de-DE/tour/3154718927"
    "?ref=itd&share_token=a40XnEo4ETN3VN6V11GeSjuZMAhA5ulR0MwFnlJYDrEsjzeL9d"
)


def verify_pasted_link_enables_search_regardless_of_phrasing() -> None:
    assert should_enable_search(
        f"Der Link ist für eine andere Wanderung. Die Wanderung möchten wir "
        f"morgen durchführen. Prüfe den Link {KOMOOT_LINK}"
    ), "the exact reported message must enable url_context"
    assert should_enable_search(f"{KOMOOT_LINK}"), "a bare link must be enough"
    assert should_enable_search(
        "Plane das ein: https://www.alltrails.com/de/trail/sweden/norrbotten/x"
    )


def verify_google_maps_links_stay_excluded() -> None:
    assert not should_enable_search(
        "Hier der Treffpunkt: https://maps.google.com/?q=63.8,20.25"
    ), "Google-Maps links resolve deterministically and never need a fetch"
    assert not should_enable_search(
        "https://www.google.de/maps/place/Ume%C3%A5"
    )


def verify_discovery_phrases_still_work_without_links() -> None:
    assert should_enable_search("Welche Restaurants gibt es in der Nähe?")
    assert not should_enable_search("Verschiebe den Stopp auf 18 Uhr")
    assert not should_enable_search("")


if __name__ == "__main__":
    verify_pasted_link_enables_search_regardless_of_phrasing()
    verify_google_maps_links_stay_excluded()
    verify_discovery_phrases_still_work_without_links()
    print("Chat link url_context enablement tests passed.")
