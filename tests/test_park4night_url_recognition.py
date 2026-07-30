"""Regression test: real park4night.com page links must be recognized.

The shared Park4Night regex only matched shorthand forms ("p4n 506374",
"Park4Night: 506374") - a pasted page link like
https://park4night.com/lieu/506374/ was not recognized at all, although the
stop form's hint and the panel's error text both promise it works. The
server-side lookup gate (panel.py's park4night_lookup action) uses this very
regex, so a stop whose only hint was the page URL could never be read.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re

SOURCE = Path("custom_components/roadplanner_mcp/destination_intelligence.py").read_text(
    encoding="utf-8"
)

# Extract the regex pattern literally - importing the module needs stubs, and
# only the pattern itself is under test here.
tree = ast.parse(SOURCE)
pattern = None
for node in ast.walk(tree):
    if (
        isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "_PARK4NIGHT_ID_RE" for t in node.targets
        )
        and isinstance(node.value, ast.Call)
    ):
        pattern = ast.literal_eval(node.value.args[0])
assert pattern, "_PARK4NIGHT_ID_RE not found"
P4N_RE = re.compile(pattern)


def verify_page_links_are_recognized() -> None:
    for url in (
        "https://park4night.com/lieu/506374/",
        "https://www.park4night.com/lieu/506374",
        "https://park4night.com/de/place/506374",
        "park4night.com/lieu/506374",
    ):
        match = P4N_RE.search(url)
        assert match, f"URL must be recognized: {url}"
        assert match.group("id") == "506374", (
            f"the full ID must survive - the URL-path branch must never eat "
            f"leading digits (got {match.group('id')!r} for {url})"
        )


def verify_shorthand_forms_keep_working() -> None:
    for text in ("p4n 506374", "P4N-506374", "Park4Night: 506374", "(p4n #506374)"):
        match = P4N_RE.search(text)
        assert match and match.group("id") == "506374", f"shorthand broke: {text}"


def verify_bare_host_is_not_a_reference() -> None:
    assert P4N_RE.search("https://park4night.com/") is None


if __name__ == "__main__":
    verify_page_links_are_recognized()
    verify_shorthand_forms_keep_working()
    verify_bare_host_is_not_a_reference()
    print("Park4Night URL recognition tests passed.")
