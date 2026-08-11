"""The switch the user ticked has to arrive where the decision is made.

The options screen showed "KI-Videoanalyse" ticked. The panel card, on
the same trip, said the analysis was switched off - and the button that
starts it was therefore never drawn. Nothing logged an error, because
nothing had failed: the service asked the assistant client for an
attribute the assistant client does not have, and `getattr(..., False)`
turned "I do not know" into "off".

That is this project's second failure pattern - an absent answer rendered
as a state - crossed with its third: the only object in the repository
that ever carried `video_analysis_enabled` was the test's own fake, which
wrote the assumption down again and made the arrangement look correct.

So these checks are about the chain, and about the pattern:

  options -> ExperienceManager -> VideoCurationService.enabled -> offer
  and                                                          -> runtime -> panel

and: whatever the service reads off the provider must exist on the real
provider, not only on a fake.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"

SERVICE = (INTEGRATION / "video_curation_service.py").read_text(encoding="utf-8")
MANAGER = (INTEGRATION / "experience_manager.py").read_text(encoding="utf-8")
SETUP = (INTEGRATION / "__init__.py").read_text(encoding="utf-8")
PANEL = (INTEGRATION / "panel.py").read_text(encoding="utf-8")
GEMINI = (INTEGRATION / "gemini_client.py").read_text(encoding="utf-8")
CARD = (
    INTEGRATION / "frontend" / "features" / "story-editor.js"
).read_text(encoding="utf-8")


def verify_the_service_does_not_ask_the_provider_whether_the_user_agreed() -> None:
    """The consent is the user's, and the assistant client never saw it."""
    assert 'getattr(self._provider, "video_analysis_enabled"' not in SERVICE
    assert "video_analysis_enabled: bool" in SERVICE


def verify_every_provider_attribute_the_service_reads_actually_exists() -> None:
    """The general form of the bug, not the one instance of it.

    Anything fetched off the provider with a default silently answers
    that default when the attribute is missing - so each name is looked
    for in the real client.
    """
    names = set(
        re.findall(r'getattr\(\s*self\._provider,\s*"([a-z_]+)"', SERVICE)
    )
    # Plain attribute access would raise, which is loud enough; only the
    # ones with a fallback can lie.
    for name in sorted(names):
        assert (
            f"self.{name}" in GEMINI
            or f"def {name}" in GEMINI
            or f"\n    {name} " in GEMINI
        ), (
            f"Der Service liest `{name}` vom Provider mit Standardwert, "
            "aber der echte Client hat dieses Attribut nicht - die Antwort "
            "ist dann immer der Standardwert"
        )


def verify_the_option_reaches_the_service() -> None:
    """Read on both sides, rather than trusting either."""
    assert "video_analysis_enabled: bool = False" in MANAGER, MANAGER[:0]
    assert "video_analysis_enabled=video_analysis_enabled," in MANAGER
    assert (
        "video_analysis_enabled=bool(\n            options.get(CONF_VIDEO_ANALYSIS_ENABLED"
        in SETUP
    ), "die Option wird dem Experience-Manager nicht übergeben"


def verify_the_panel_and_the_card_cannot_disagree() -> None:
    """One value, two readers - which is what the screenshot exposed.

    The panel reported the option and the card reported the service, so
    a break between the two showed up as two screens contradicting each
    other. The runtime field now comes from the service itself.
    """
    assert "video_analysis_enabled=experience.video_curation.enabled," in SETUP
    assert '"video_analysis_enabled": runtime.video_analysis_enabled,' in PANEL
    # And the card still reads the offer, which reads the same service.
    assert "offer.enabled" in CARD
    assert '"enabled": self.enabled,' in SERVICE


def verify_the_option_is_only_read_once() -> None:
    """A second `options.get(CONF_VIDEO_ANALYSIS_ENABLED)` is the next drift."""
    assert SETUP.count("CONF_VIDEO_ANALYSIS_ENABLED") == 2, (
        "erwartet: einmal importiert, einmal gelesen - "
        f"gefunden: {SETUP.count('CONF_VIDEO_ANALYSIS_ENABLED')}"
    )


def verify_the_default_is_off() -> None:
    """Footage goes to a cloud only when somebody said so."""
    tree = ast.parse(SERVICE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "__init__":
            for arg, default in zip(
                node.args.kwonlyargs, node.args.kw_defaults, strict=True
            ):
                if arg.arg == "video_analysis_enabled":
                    assert isinstance(default, ast.Constant) and default.value is False
                    return
    raise AssertionError("kein Standardwert für video_analysis_enabled gefunden")


for check in (
    verify_the_service_does_not_ask_the_provider_whether_the_user_agreed,
    verify_every_provider_attribute_the_service_reads_actually_exists,
    verify_the_option_reaches_the_service,
    verify_the_panel_and_the_card_cannot_disagree,
    verify_the_option_is_only_read_once,
    verify_the_default_is_off,
):
    check()

print("Video analysis opt-in wiring tests passed.")
