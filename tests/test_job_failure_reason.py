"""A failed job has to say why, in the words the protocol actually uses.

This exists because of a card that reported every failure as "(failed)"
and nothing more. The reason was never missing: the renderer writes
``error: {code, message}`` into the status file and always has. The
panel read ``job.reason || job.detail`` - and NEITHER FIELD EXISTS on a
status, so the fallback chain quietly collapsed to an empty string on
every single failure, for every kind of job.

That is this project's most repeated mistake in its purest form: a name
read off an object that never carried it, with a default that can lie.
It has now happened six times (``data`` vs ``value``, ``content_hash``
vs ``file_hash``, ``chapter_id`` vs ``linked_day_id``, ``zeigt`` vs
``motifs``, ``video_analysis_enabled``, and this one), and every
occurrence looked exactly like a feature that was simply not very
informative.

So the check reads BOTH sides: the field names ``validate_status``
really returns, and the names the panel reads off a job. A name the
panel expects that the protocol never produces is the bug, and it is
findable without running anything.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"
PROTOCOL = INTEGRATION / "renderer_app_protocol.py"
CARD = INTEGRATION / "frontend" / "features" / "story-editor.js"


def _status_fields() -> set[str]:
    """The keys `validate_status` actually returns, read from its code.

    From the syntax tree rather than from a list written down here: a
    second copy of these names is exactly the thing that drifts, and a
    test carrying its own copy would agree with itself while the panel
    disagreed with the protocol.
    """
    tree = ast.parse(PROTOCOL.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "validate_status":
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Return) or not isinstance(inner.value, ast.Dict):
                continue
            found = {
                key.value
                for key in inner.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            if found:
                return found
    raise AssertionError("validate_status gibt kein Wörterbuch mehr zurück")


def _code(text: str) -> str:
    """The body without its comments.

    The first version searched the raw text and tripped over the comment
    EXPLAINING which two names must never be read again - a check that
    fails on the prose describing the fix is worse than no check, and it
    is the third time that shape has appeared in this repository.
    """
    without_blocks = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return "\n".join(
        re.sub(r"//.*$", "", line) for line in without_blocks.splitlines()
    )


def verify_the_card_reads_only_fields_a_status_has() -> None:
    """Every `job.<name>` in the job line, against the real schema."""
    fields = _status_fields()
    # The two names that were read for months and never existed. Stated
    # explicitly, because the point is not "the set matches" but "these
    # particular inventions are gone".
    assert "error" in fields, fields
    assert "reason" not in fields and "detail" not in fields, fields

    source = CARD.read_text(encoding="utf-8")
    match = re.search(
        r"_renderStoryFilmJobLine\(\) \{(.*?)\n  \},", source, re.S
    )
    assert match, "die Auftragszeile heisst nicht mehr _renderStoryFilmJobLine"
    body = _code(match.group(1))

    used = set(re.findall(r"\bjob\??\.([A-Za-z_][A-Za-z0-9_]*)", body))
    assert used, "die Auftragszeile liest gar nichts mehr vom Auftrag"
    unknown = used - fields
    assert not unknown, (
        "die Karte liest Felder, die ein Status nie hat - genau so wurde "
        f"jeder Fehlschlag zu einem leeren Grund: {sorted(unknown)}"
    )


def verify_a_failure_shows_the_reason_the_renderer_gave() -> None:
    """And it is actually read, not merely readable.

    A card that stopped mentioning the error at all would pass the check
    above perfectly - it reads no unknown field because it reads none.
    """
    source = CARD.read_text(encoding="utf-8")
    match = re.search(r"_renderStoryFilmJobLine\(\) \{(.*?)\n  \},", source, re.S)
    assert match
    body = _code(match.group(1))
    assert re.search(r"job\??\.error\??\.message", body), (
        "die Fehlermeldung des Renderers wird nicht mehr angezeigt"
    )
    # And when there is genuinely none, that is said rather than shown as
    # an empty sentence - an absent answer must not look like a state.
    assert "keinen Grund" in body, body


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Job failure reason tests passed.")


if __name__ == "__main__":
    main()
