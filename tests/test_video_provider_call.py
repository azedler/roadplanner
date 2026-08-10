"""The request that actually goes to Gemini for one video window.

`video_analysis` builds the question and reads the answer; this is the
piece in between, and it is where the money and the privacy live.

Three things are worth a test here and nothing else is:

**What is sent is a proxy, not an original.** The size bound is the only
mechanical proof this module can offer that nobody wired a camera file
into it, and the error has to name the cause - a rejected request from
Google, arriving minutes later, does not.

**The cost dial is set the cheap way by default.** Video is billed by
what it costs to look at: roughly 300 tokens per second at default media
resolution against about 100 at low. A caller asks for the expensive
look deliberately, for the one short window it already cares about.

**A window is a window.** `startOffset`/`endOffset`/`fps` reach the API
in the shape it expects, or the model judges footage nobody asked about.

Nothing here touches a network. The client's own HTTP layer is replaced
by a recorder, so the assertion is about the request body Roadplanner
composes - the only part this project is responsible for.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True


def _module(name: str, **attributes) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules.setdefault(name, module)
    return module


_module("homeassistant").__path__ = []
_module("homeassistant.helpers").__path__ = []
_module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: None)
_module(
    "aiohttp",
    ClientError=type("ClientError", (Exception,), {}),
    ClientResponse=object,
    ClientSession=object,
)

_PACKAGE = "roadplanner_video_provider_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(ROOT / "custom_components" / "roadplanner_mcp")]
sys.modules[_PACKAGE] = _root

gemini = importlib.import_module(f"{_PACKAGE}.gemini_client")
provider = importlib.import_module(f"{_PACKAGE}.assistant_provider")
analysis = importlib.import_module(f"{_PACKAGE}.video_analysis")
errors = importlib.import_module(f"{_PACKAGE}.roadplanner")


def _client(answer: str) -> tuple[gemini.GeminiClient, list[dict]]:
    """A real client whose only fake part is the HTTP call itself."""
    client = gemini.GeminiClient(object(), api_key="k", model="gemini-3.5-flash")
    sent: list[dict] = []

    async def _post(body_factory, *, search_requested=False, models_override=None):
        variants = body_factory("gemini-3.5-flash")
        # The first variant is the one the client prefers; that is the
        # request under test.
        sent.append(variants[0][1] if isinstance(variants, list) else variants)
        return (
            {
                "candidates": [{"content": {"parts": [{"text": answer}]}}],
                "modelVersion": "gemini-3.5-flash",
            },
            {},
        )

    client._post = _post  # noqa: SLF001 - replacing exactly the network boundary
    return client, sent


# The field names the curation ACTUALLY stores, read out of
# `video_analysis.RESPONSE_SCHEMA` rather than invented here. Writing a
# plausible-looking shape instead is how this project once shipped a
# diagnosis that read `zeigt`/`motive` while the analyses stored
# `motifs`/`shows`: the test agreed with the bug and hid it.
GOOD_ANSWER = (
    '{"usable": true, "start_seconds": 1.0, "end_seconds": 5.0, '
    '"role": "hero", "visual_quality": 4, "story_value": 4, '
    '"subject": "Kinder am See", "reason": "ruhige Kamera", '
    '"original_sound_interesting": false}'
)


def _video(**overrides):
    base = {
        "video_id": "clip-1",
        "data": b"\x00" * 2_048,
        "mime_type": "video/mp4",
    }
    base.update(overrides)
    return provider.AssistantVideoInput(**base)


def verify_the_cheap_look_is_the_default() -> None:
    """Low media resolution costs about a third, so it is not opt-in."""
    client, sent = _client(GOOD_ANSWER)
    asyncio.run(
        client.async_analyze_video(
            system_instruction="s",
            prompt="p",
            video=_video(),
            schema=analysis.RESPONSE_SCHEMA,
        )
    )
    config = sent[0]["generationConfig"]
    assert config["mediaResolution"] == "MEDIA_RESOLUTION_LOW", config

    # ...and the closer look is available when a caller has a reason.
    client, sent = _client(GOOD_ANSWER)
    asyncio.run(
        client.async_analyze_video(
            system_instruction="s",
            prompt="p",
            video=_video(),
            schema=analysis.RESPONSE_SCHEMA,
            low_resolution=False,
        )
    )
    assert "mediaResolution" not in sent[0]["generationConfig"], sent[0]


def verify_the_window_reaches_the_api_in_its_own_shape() -> None:
    """Offsets are second-suffixed strings; fps is a number. Not prose."""
    client, sent = _client(GOOD_ANSWER)
    asyncio.run(
        client.async_analyze_video(
            system_instruction="s",
            prompt="p",
            video=_video(start_offset=12.0, end_offset=20.5, fps=2),
            schema=analysis.RESPONSE_SCHEMA,
        )
    )
    part = next(
        item
        for item in sent[0]["contents"][0]["parts"]
        if isinstance(item, dict) and "inlineData" in item
    )
    assert part["videoMetadata"] == {
        "startOffset": "12.000s",
        "endOffset": "20.500s",
        "fps": 2.0,
    }, part["videoMetadata"]
    assert part["inlineData"]["mimeType"] == "video/mp4"


def verify_an_original_sized_file_is_refused_with_its_reason() -> None:
    """The bound is a guardrail against a mistake upstream, and it says so."""
    client, _sent = _client(GOOD_ANSWER)
    try:
        asyncio.run(
            client.async_analyze_video(
                system_instruction="s",
                prompt="p",
                video=_video(data=b"\x00" * 15_000_001),
                schema=analysis.RESPONSE_SCHEMA,
            )
        )
    except errors.ValidationError as err:
        assert "Analyseproxy" in str(err), str(err)
    else:
        raise AssertionError("ein Original-großer Upload muss abgelehnt werden")

    # A format nobody can decode is refused before it is uploaded, too.
    for mime in ("image/jpeg", "application/octet-stream", ""):
        try:
            asyncio.run(
                client.async_analyze_video(
                    system_instruction="s",
                    prompt="p",
                    video=_video(mime_type=mime),
                    schema=analysis.RESPONSE_SCHEMA,
                )
            )
        except errors.ValidationError:
            continue
        raise AssertionError(f"Format {mime!r} hätte abgelehnt werden müssen")


def verify_an_unreadable_answer_is_not_paid_for_twice() -> None:
    """One window's worth of failure costs that window, not a repair call."""
    client, sent = _client("kein JSON, nur Prosa")
    try:
        asyncio.run(
            client.async_analyze_video(
                system_instruction="s",
                prompt="p",
                video=_video(),
                schema=analysis.RESPONSE_SCHEMA,
            )
        )
    except gemini.GeminiApiError as err:
        assert err.code == "invalid_video_output", err.code
    else:
        raise AssertionError("unlesbare Antwort muss ein Fehler sein")
    assert len(sent) == 1, "eine Reparaturanfrage wäre ein zweiter bezahlter Aufruf"


def verify_the_answer_is_handed_on_in_the_shape_the_contract_reads() -> None:
    """The provider's job ends where `video_analysis.parse_analysis` starts."""
    client, _sent = _client(GOOD_ANSWER)
    result = asyncio.run(
        client.async_analyze_video(
            system_instruction="s",
            prompt="p",
            video=_video(),
            schema=analysis.RESPONSE_SCHEMA,
        )
    )
    parsed = analysis.parse_analysis(result.value, window_seconds=10.0)
    assert parsed is not None, result.value
    assert parsed["start_seconds"] == 1.0 and parsed["end_seconds"] == 5.0, parsed
    assert parsed["duration_seconds"] == 4.0, parsed
    assert parsed["role"] in analysis.CLIP_ROLES, parsed
    # A recommendation only - the film never switches sound on by itself.
    assert parsed["original_sound_interesting"] is False, parsed
    assert result.diagnostics["video_media_resolution"] == "low"


for check in (
    verify_the_cheap_look_is_the_default,
    verify_the_window_reaches_the_api_in_its_own_shape,
    verify_an_original_sized_file_is_refused_with_its_reason,
    verify_an_unreadable_answer_is_not_paid_for_twice,
    verify_the_answer_is_handed_on_in_the_shape_the_contract_reads,
):
    check()

print("Video provider call tests passed.")
