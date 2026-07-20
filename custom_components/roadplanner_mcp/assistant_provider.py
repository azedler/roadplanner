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
