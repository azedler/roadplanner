"""Provider-neutral interfaces for the Roadplanner conversational assistant."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class AssistantSource:
    """One source returned by an assistant provider."""

    title: str
    url: str

    def as_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url}




@dataclass(slots=True)
class AssistantImageInput:
    """One bounded image supplied to a multimodal provider call."""

    image_id: str
    data: bytes
    mime_type: str
    label: str = ""

@dataclass(slots=True)
class AssistantVideoInput:
    """One bounded video clip supplied to a multimodal provider call.

    Always an ANALYSIS PROXY, never an original: smaller, stripped of
    metadata, and already cut to the window worth asking about. The
    original stays where it is - sending it would mean paying to look at
    footage nobody wants and handing a family's raw recording to a cloud
    for no gain.

    `fps` and `low_resolution` are the two cost dials the API gives us.
    Video is billed by what it costs to look at - roughly 300 tokens per
    second at default resolution and about 100 at low - so a first cheap
    pass over a wide window and a closer second look at the promising
    part of it is the difference between analysing a library and
    affording one.
    """

    video_id: str
    data: bytes
    mime_type: str
    start_offset: float | None = None
    end_offset: float | None = None
    fps: float | None = None
    label: str = ""


@dataclass(slots=True)
class AssistantTextResult:
    """Natural-language result plus optional grounding and call diagnostics."""

    text: str
    sources: list[AssistantSource] = field(default_factory=list)
    model_version: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AssistantJsonResult:
    """Structured result plus optional grounding and call diagnostics."""

    value: dict[str, Any]
    sources: list[AssistantSource] = field(default_factory=list)
    model_version: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AssistantImageResult:
    """One generated image plus provider metadata."""

    data: bytes
    mime_type: str
    model_version: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class AssistantProvider(Protocol):
    """Small provider contract used by the Roadplanner domain layer."""

    @property
    def name(self) -> str:
        """Return a stable provider identifier."""

    @property
    def model(self) -> str:
        """Return the configured primary model identifier."""

    @property
    def configured(self) -> bool:
        """Return whether the provider can currently be called."""

    def health_snapshot(self) -> dict[str, Any]:
        """Return sanitized provider health statistics."""

    async def async_generate_text(
        self,
        *,
        system_instruction: str,
        messages: list[dict[str, str]],
        enable_search: bool,
        max_output_tokens: int = 4096,
        temperature: float = 0.35,
    ) -> AssistantTextResult:
        """Generate a grounded natural-language response."""

    async def async_generate_json_result(
        self,
        *,
        system_instruction: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        enable_search: bool = False,
        max_output_tokens: int = 8192,
        temperature: float = 0.1,
    ) -> AssistantJsonResult:
        """Generate one JSON object plus provider metadata."""


    async def async_analyze_images(
        self,
        *,
        system_instruction: str,
        prompt: str,
        images: list[AssistantImageInput],
        schema: dict[str, Any],
        max_output_tokens: int = 4096,
    ) -> AssistantJsonResult:
        """Analyze several locally preselected images and return JSON."""

    async def async_analyze_video(
        self,
        *,
        system_instruction: str,
        prompt: str,
        video: AssistantVideoInput,
        schema: dict[str, Any],
        max_output_tokens: int = 2048,
        low_resolution: bool = True,
    ) -> AssistantJsonResult:
        """Analyze one locally prefiltered video window and return JSON."""

    async def async_generate_json(
        self,
        *,
        system_instruction: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        enable_search: bool = False,
        max_output_tokens: int = 8192,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        """Compatibility wrapper returning only the JSON object."""

    async def async_generate_image(
        self,
        *,
        prompt: str,
        reference: tuple[bytes, str] | None = None,
    ) -> AssistantImageResult:
        """Generate one image from a text prompt and an optional reference.

        ``reference`` is ``(bytes, mime_type)`` of a picture the result
        should be based on. It is what makes "our camper" possible at
        all: a prompt alone can only describe a category, and every
        high-roof van matches the description of every other one.
        """
