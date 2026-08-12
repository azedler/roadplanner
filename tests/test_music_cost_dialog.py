"""What somebody sees before agreeing to pay, and what may not charge.

§38 asks for a dialog that names the model, how many generations, how
much audio, what it costs, that it is a paid call, and that what is
bought is kept. Every one of those is a separate sentence somebody could
have left out, and leaving one out is not visible from the code that
produces it - so each is checked against the real offer AND against the
panel that displays it.

§50 asks the complementary question: which actions may NOT reach a paid
call. Rendering a film, rendering a quality excerpt, making a review
copy, changing a size - none of them are a decision about money, and
none of them may quietly become one. That is checked structurally,
because a counter can only prove the paths somebody thought to drive.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "roadplanner_mcp"
FRONTEND = PACKAGE_ROOT / "frontend"
sys.dont_write_bytecode = True


def _module(name: str, **attributes) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules.setdefault(name, module)
    return module


_module("homeassistant").__path__ = []
_module("homeassistant.core", HomeAssistant=object, callback=lambda fn: fn)
_module("homeassistant.exceptions", HomeAssistantError=type("E", (Exception,), {}))
_module("homeassistant.helpers").__path__ = []
_module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: None)
_module("homeassistant.helpers.storage", Store=object)
_module(
    "aiohttp",
    ClientError=type("ClientError", (Exception,), {}),
    ClientSession=object,
    ClientTimeout=lambda *a, **k: None,
)

_PACKAGE = "roadplanner_cost_dialog_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(PACKAGE_ROOT)]
sys.modules[_PACKAGE] = _root

service_module = importlib.import_module(f"{_PACKAGE}.trip_film_music_service")
lyria_module = importlib.import_module(f"{_PACKAGE}.trip_film_lyria")

STORY_EDITOR = (FRONTEND / "features" / "story-editor.js").read_text(encoding="utf-8")
PANEL_SOURCE = (PACKAGE_ROOT / "panel.py").read_text(encoding="utf-8")
EXPORT_SOURCE = (PACKAGE_ROOT / "trip_film_export.py").read_text(encoding="utf-8")

MANIFEST = {
    "trip": {"title": "Ostsee-Runde 2026"},
    "narrative": {"arc": "Eine große Runde", "opening": "los", "closing": "an"},
}
FILM = 12 * 60 + 23.0


class _Hass:
    async def async_add_executor_job(self, target, *args):
        return target(*args)


class _StoryContext:
    async def async_manifest(self, trip_id):
        return MANIFEST


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self._payload

    async def text(self):
        return ""


class _Session:
    def __init__(self):
        self.calls = 0

    def get(self, url, *, headers=None, params=None, timeout=None):
        return _Response({"models": [{"name": "models/lyria-3-pro-preview"}]})

    def post(self, url, *, json=None, data=None, headers=None, timeout=None):
        self.calls += 1
        blob = base64.b64encode(b"ID3fake").decode("ascii")
        return _Response({"predictions": [{"bytesBase64Encoded": blob}]})


def _service(root, session):
    return service_module.TripFilmMusicService(
        _Hass(),
        _StoryContext(),
        lambda: session,
        api_key_provider=lambda: "k",
        music_root=root,
        vertex_provider=lambda: None,
    )


def _music_plan_view() -> str:
    """The part of the panel that renders the offer, and only that."""
    body = STORY_EDITOR.split("_renderStoryFilmMusicPlan() {", 1)[1]
    return body.split("\n  },", 1)[0]


def verify_the_offer_names_everything_a_decision_needs() -> None:
    """§38, field by field, against the real service.

    Each of these is a separate sentence in the dialog, and each could
    have been forgotten without anything failing: a price with no model
    beside it, or an amount of audio nobody can compare to the film.
    """
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        offer = asyncio.run(_service(root, session).async_offer("t1", film_seconds=FILM))
    assert session.calls == 0, "ein Angebot darf nichts erzeugen"
    assert offer["model"] == lyria_module.LYRIA_MODEL, offer
    assert offer["sections"] == offer["new_generations"] > 0, offer
    assert offer["cached"] == 0
    # Per REQUEST, which is what the bill says. Quoting a per-minute
    # price here once made the dialog show a third of the real cost.
    assert offer["price_per_generation"] == 0.08, offer
    assert abs(offer["estimated_cost"] - offer["sections"] * 0.08) < 0.001, offer
    assert offer["currency"] == "USD"
    # How much audio is ordered, which is not the film's length - the
    # sections overlap by a crossfade, so this is the larger number.
    assert offer["audio_seconds"] >= offer["seconds"] > 0, offer
    assert offer["planned_by"] in ("arithmetik", "gemini"), offer
    assert offer["assets_are_stored"] is True


def verify_the_panel_shows_each_of_those_and_says_it_is_paid() -> None:
    """A field carried to the browser and never drawn is not a dialog."""
    view = _music_plan_view()
    for field in (
        "offer.model",
        "offer.new_generations",
        "offer.cached",
        "offer.audio_seconds",
        "offer.price_per_generation",
        "offer.estimated_cost",
        "offer.planned_by",
        "offer.assets_are_stored",
    ):
        assert field in view, f"{field} steht im Angebot, aber nicht im Dialog"
    lowered = view.lower()
    assert "kostenpflichtig" in lowered, "der Dialog sagt nicht, dass es Geld kostet"
    assert "gespeichert" in lowered, "der Dialog sagt nicht, dass die Titel bleiben"
    # And "not available" says WHY, rather than naming a cause it cannot
    # know. The panel used to state a missing Gemini key regardless of
    # which of the two routes was actually missing.
    assert "unavailable_reason" in view, view
    assert "Google-Schlüssel konfiguriert" not in view, (
        "der Dialog nennt wieder einen erfundenen Grund statt des echten"
    )


def verify_the_panel_never_invents_a_planner_it_was_not_told_about() -> None:
    """Failure pattern 8, in the one place it would be silent.

    The panel translates `planned_by` for a reader. If it translated by
    guessing - "anything that is not the model is arithmetic" - then a
    third value, or a typo, would be displayed as a confident lie about
    who placed the boundaries.
    """
    view = _music_plan_view()
    written = {name for name in ("arithmetik", "gemini") if name in view}
    service_source = (PACKAGE_ROOT / "trip_film_music_service.py").read_text(
        encoding="utf-8"
    )
    produced = {
        name
        for name in ("arithmetik", "gemini")
        if f'plan["planned_by"] = "{name}"' in service_source
    }
    assert produced, "der Dienst schreibt kein planned_by mehr"
    assert produced <= written, f"der Dialog kennt {produced - written} nicht"


def verify_nothing_but_the_one_action_can_spend_money() -> None:
    """§50: rendering is not a decision about money.

    Structural rather than driven: a counter proves the paths somebody
    thought to try, and the risk here is exactly the path nobody thought
    of. `async_generate` on the music service may be reached from one
    action and no other.
    """
    lines = [
        line
        for line in PANEL_SOURCE.splitlines()
        if "film_music.async_generate" in line
    ]
    assert len(lines) == 1, lines
    # ... and that one is under the action named for it, not under a
    # render.
    before = PANEL_SOURCE.split("film_music.async_generate", 1)[0]
    owning_action = before.rsplit('if action == "', 1)[1].split('"', 1)[0]
    assert owning_action == "story_film_music_generate", owning_action
    for spender in ("async_generate", "TripFilmMusicService"):
        assert spender not in EXPORT_SOURCE, (
            f"der Filmexport erreicht {spender} - ein Rendern darf nicht zahlen"
        )


def verify_the_free_film_actions_stay_free() -> None:
    """Each of the four, named, so removing one from the list is visible."""
    free = (
        "story_film_render",
        "story_film_qa_render",
        "story_film_review_copy",
        "story_film_music_offer",
    )
    for action in free:
        body = PANEL_SOURCE.split(f'if action == "{action}":', 1)[1]
        body = body.split('\n    if action == "', 1)[0]
        assert "film_music.async_generate" not in body, action
        assert "async_generate_and_publish" not in body, action
    # And a size is a size: changing the render profile cannot reach the
    # planner at all, because the profile is not part of what music is
    # planned from.
    plan_source = (PACKAGE_ROOT / "trip_film_music_plan.py").read_text(encoding="utf-8")
    for size_word in ("render_profile", "profile_id", "width", "height", "crf"):
        assert size_word not in plan_source, (
            f"die Musikplanung liest {size_word} - dann kauft ein Profilwechsel Musik"
        )


def verify_a_cached_soundtrack_quotes_zero() -> None:
    """The sentence somebody reads before pressing a second time."""
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        service = _service(root, session)
        made = asyncio.run(service.async_generate("t1", film_seconds=FILM))
        paid = session.calls
        assert paid == made["generated"] > 0, made
        offer = asyncio.run(service.async_offer("t1", film_seconds=FILM))
        assert offer["reused"] is True, offer
        assert offer["new_generations"] == 0
        assert offer["estimated_cost"] == 0.0
        assert offer["cached"] == offer["sections"]
        # The dialog has a different sentence for this case: offering to
        # "generate for 0.00 USD" is a button nobody can read.
        view = _music_plan_view()
        assert "offer.reused" in view, "der Dialog unterscheidet den Fall nicht"
        assert session.calls == paid, "ein Angebot nach dem Kauf kostet erneut"


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Music cost dialog tests passed.")


if __name__ == "__main__":
    main()
