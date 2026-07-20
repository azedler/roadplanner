"""Plugin extension points for the native Roadplanner assistant.

Plugins are deliberately small and server-side. They may contribute bounded
context or enrich already validated assistant operations. They never apply
Roadbook changes themselves. This keeps future integrations (weather, routing,
costs, media, EVCC, OneDrive, Google Photos) isolated from the core assistant.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol

from .geocoding import GeocodingError, NominatimGeocoder, parse_coordinate_pair


@dataclass(slots=True)
class AssistantPluginDescriptor:
    """Public, non-secret plugin metadata."""

    plugin_id: str
    name: str
    enabled: bool
    capabilities: tuple[str, ...] = ()
    version: str = "1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.plugin_id,
            "name": self.name,
            "enabled": self.enabled,
            "capabilities": list(self.capabilities),
            "version": self.version,
        }


@dataclass(slots=True)
class AssistantPluginRunResult:
    """Operation enrichment result returned by one or more plugins."""

    operations: list[dict[str, Any]]
    open_questions: list[str]
    diagnostics: list[dict[str, Any]] = field(default_factory=list)


class AssistantPlugin(Protocol):
    """Provider-neutral assistant plugin contract."""

    @property
    def descriptor(self) -> AssistantPluginDescriptor:
        """Return stable plugin metadata."""

    async def async_context_fragment(
        self,
        *,
        purpose: str,
        context: dict[str, Any],
        user_text: str,
    ) -> dict[str, Any]:
        """Return an optional bounded fragment added to assistant context."""

    async def async_enrich_operations(
        self,
        *,
        operations: list[dict[str, Any]],
        open_questions: list[str],
        context: dict[str, Any],
    ) -> AssistantPluginRunResult:
        """Enrich safe operations without applying them."""


class AssistantPluginRegistry:
    """Ordered registry used by the Roadplanner domain layer."""

    def __init__(self) -> None:
        self._plugins: list[AssistantPlugin] = []

    def register(self, plugin: AssistantPlugin) -> None:
        plugin_id = plugin.descriptor.plugin_id
        if any(item.descriptor.plugin_id == plugin_id for item in self._plugins):
            raise ValueError(f"Assistant plugin already registered: {plugin_id}")
        self._plugins.append(plugin)

    def descriptors(self) -> list[dict[str, Any]]:
        return [plugin.descriptor.as_dict() for plugin in self._plugins]

    async def async_context_fragments(
        self,
        *,
        purpose: str,
        context: dict[str, Any],
        user_text: str,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for plugin in self._plugins:
            if not plugin.descriptor.enabled:
                continue
            fragment = await plugin.async_context_fragment(
                purpose=purpose,
                context=context,
                user_text=user_text,
            )
            if fragment:
                result[plugin.descriptor.plugin_id] = fragment
        return result

    async def async_enrich_operations(
        self,
        *,
        operations: list[dict[str, Any]],
        open_questions: list[str],
        context: dict[str, Any],
    ) -> AssistantPluginRunResult:
        current = deepcopy(operations)
        questions = list(open_questions)
        diagnostics: list[dict[str, Any]] = []
        for plugin in self._plugins:
            if not plugin.descriptor.enabled:
                continue
            result = await plugin.async_enrich_operations(
                operations=current,
                open_questions=questions,
                context=context,
            )
            current = result.operations
            questions = result.open_questions
            diagnostics.extend(result.diagnostics)
        return AssistantPluginRunResult(
            operations=current,
            open_questions=questions,
            diagnostics=diagnostics,
        )


class GeocodingAssistantPlugin:
    """Resolve new concrete stops with a Nominatim-compatible provider.

    A geocoding ambiguity must not discard an otherwise valid planning
    operation. Resolved points receive canonical coordinates. Unresolved text
    searches remain reviewable operations with an explicit open question and
    bounded candidate metadata. User-supplied coordinates are preserved even
    when reverse geocoding is unavailable, while being marked as unverified.
    """

    def __init__(
        self,
        geocoder: NominatimGeocoder,
        *,
        language: str = "de",
    ) -> None:
        self._geocoder = geocoder
        self._language = language or "de"

    @property
    def descriptor(self) -> AssistantPluginDescriptor:
        return AssistantPluginDescriptor(
            plugin_id="geocoding",
            name="GPS-Ortsauflösung",
            enabled=bool(self._geocoder.enabled),
            capabilities=("location_resolution", "operation_enrichment"),
            version="2",
        )

    async def async_context_fragment(
        self,
        *,
        purpose: str,
        context: dict[str, Any],
        user_text: str,
    ) -> dict[str, Any]:
        return {
            "enabled": bool(self._geocoder.enabled),
            "rule": (
                "Coordinates are resolved server-side. The model must not invent "
                "GPS values. Ambiguous text locations remain explicit review items."
            ),
        }

    @staticmethod
    def _candidate_payload(candidate: Any) -> dict[str, Any]:
        """Return bounded, review-safe candidate metadata."""
        location = candidate.as_location()
        return {
            "display_name": str(candidate.display_name)[:1_000],
            "latitude": float(candidate.latitude),
            "longitude": float(candidate.longitude),
            "city": str(location.get("city") or "")[:300],
            "country_code": str(location.get("country_code") or "")[:10],
            "score": round(float(candidate.score), 4),
            "importance": round(float(candidate.importance), 4),
            "category": str(candidate.category or "")[:100],
            "result_type": str(candidate.result_type or "")[:100],
            "source_url": str(candidate.source_url)[:1_000],
        }

    @classmethod
    def _mark_pending(
        cls,
        value: dict[str, Any],
        *,
        query: str,
        status: str,
        resolution_mode: str,
        alternatives: list[Any] | None = None,
        coordinate_query: tuple[float, float] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Keep an operation reviewable while marking unresolved GPS state."""
        changes = value.setdefault("changes", {})
        if not isinstance(changes, dict):
            changes = {}
            value["changes"] = changes
        details = (
            deepcopy(changes.get("details"))
            if isinstance(changes.get("details"), dict)
            else {}
        )
        candidates = [
            cls._candidate_payload(candidate)
            for candidate in (alternatives or [])[:3]
            if candidate is not None
        ]
        geocoding_details: dict[str, Any] = {
            "provider": "nominatim",
            "status": status,
            "query": query,
            "mode": resolution_mode,
            "requires_confirmation": True,
            "candidates": candidates,
            "attribution": "© OpenStreetMap contributors",
        }
        if error:
            geocoding_details["error"] = str(error)[:1_000]
        if coordinate_query is not None:
            latitude, longitude = coordinate_query
            existing_location = (
                deepcopy(changes.get("location"))
                if isinstance(changes.get("location"), dict)
                else {}
            )
            existing_location.update(
                {
                    "label": str(
                        existing_location.get("label")
                        or changes.get("name")
                        or query
                    )[:1_000],
                    "latitude": latitude,
                    "longitude": longitude,
                }
            )
            changes["location"] = existing_location
            geocoding_details["input_coordinates"] = {
                "latitude": latitude,
                "longitude": longitude,
            }
            geocoding_details["coordinates_preserved"] = True
        details["geocoding"] = geocoding_details
        changes["details"] = details
        return value

    async def async_enrich_operations(
        self,
        *,
        operations: list[dict[str, Any]],
        open_questions: list[str],
        context: dict[str, Any],
    ) -> AssistantPluginRunResult:
        result: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        questions = list(open_questions)
        for operation in operations:
            value = deepcopy(operation)
            query = str(value.pop("place_query", "") or "").strip()
            if value.get("entity_type") != "stop" or not query:
                result.append(value)
                continue

            coordinate_query = parse_coordinate_pair(query)
            resolution_mode = "reverse" if coordinate_query is not None else "search"

            if not self._geocoder.enabled:
                if coordinate_query is not None:
                    questions.append(
                        f"Die angegebenen GPS-Koordinaten für '{query}' wurden übernommen, "
                        "konnten aber nicht per Reverse-Geocoding geprüft werden, weil "
                        "Geocoding deaktiviert ist. Bitte den Kartenpunkt in der Vorschau prüfen."
                    )
                    status = "coordinates_unverified_geocoding_disabled"
                else:
                    questions.append(
                        f"Die GPS-Zuordnung für '{query}' ist noch offen, weil Geocoding "
                        "deaktiviert ist. Bitte Adresse oder Koordinaten ergänzen."
                    )
                    status = "text_unresolved_geocoding_disabled"
                result.append(
                    self._mark_pending(
                        value,
                        query=query,
                        status=status,
                        resolution_mode=resolution_mode,
                        coordinate_query=coordinate_query,
                    )
                )
                diagnostics.append(
                    {
                        "plugin": "geocoding",
                        "query": query,
                        "status": status,
                        "resolution_mode": resolution_mode,
                    }
                )
                continue

            try:
                best, alternatives = await self._geocoder.async_resolve(
                    query,
                    language=self._language,
                )
            except GeocodingError as err:
                if coordinate_query is not None:
                    questions.append(
                        f"Die angegebenen GPS-Koordinaten für '{query}' wurden übernommen, "
                        f"die Adressprüfung ist jedoch fehlgeschlagen: {err}. Bitte den "
                        "Kartenpunkt in der Vorschau prüfen."
                    )
                    status = "coordinates_unverified_error"
                else:
                    questions.append(
                        f"Die GPS-Zuordnung für '{query}' ist noch offen, weil die "
                        f"Ortssuche fehlgeschlagen ist: {err}."
                    )
                    status = "text_unresolved_error"
                result.append(
                    self._mark_pending(
                        value,
                        query=query,
                        status=status,
                        resolution_mode=resolution_mode,
                        coordinate_query=coordinate_query,
                        error=str(err),
                    )
                )
                diagnostics.append(
                    {
                        "plugin": "geocoding",
                        "query": query,
                        "status": status,
                        "resolution_mode": resolution_mode,
                    }
                )
                continue

            if best is None:
                options = "; ".join(
                    candidate.display_name for candidate in alternatives[:3]
                )
                suffix = f" Mögliche Treffer: {options}." if options else ""
                if coordinate_query is not None:
                    questions.append(
                        f"Die GPS-Koordinaten '{query}' wurden unverändert übernommen, "
                        f"konnten aber keiner sicheren Adresse zugeordnet werden.{suffix} "
                        "Bitte den Kartenpunkt in der Vorschau prüfen."
                    )
                    status = "coordinates_unverified"
                else:
                    questions.append(
                        f"Die GPS-Zuordnung für '{query}' ist noch nicht eindeutig.{suffix} "
                        "Bitte einen Treffer bestätigen oder genaue Koordinaten ergänzen."
                    )
                    status = "text_ambiguous" if alternatives else "text_not_found"
                result.append(
                    self._mark_pending(
                        value,
                        query=query,
                        status=status,
                        resolution_mode=resolution_mode,
                        alternatives=alternatives,
                        coordinate_query=coordinate_query,
                    )
                )
                diagnostics.append(
                    {
                        "plugin": "geocoding",
                        "query": query,
                        "status": status,
                        "resolution_mode": resolution_mode,
                        "candidate_count": len(alternatives),
                        "operation_preserved": True,
                    }
                )
                continue

            changes = value.setdefault("changes", {})
            changes["location"] = best.as_location()
            details = (
                changes.get("details")
                if isinstance(changes.get("details"), dict)
                else {}
            )
            geocoding_details = {
                **best.as_provenance(),
                "status": "resolved",
                "query": query,
                "mode": "reverse_coordinates" if coordinate_query else "text_search",
                "alternatives": [
                    self._candidate_payload(candidate)
                    for candidate in alternatives[1:3]
                ],
            }
            if coordinate_query is not None:
                geocoding_details["input_coordinates"] = {
                    "latitude": coordinate_query[0],
                    "longitude": coordinate_query[1],
                }
                geocoding_details["coordinates_preserved"] = True
            details["geocoding"] = geocoding_details
            changes["details"] = details
            result.append(value)
            diagnostics.append(
                {
                    "plugin": "geocoding",
                    "query": query,
                    "status": "resolved",
                    "resolution_mode": best.resolution_mode,
                    "display_name": best.display_name,
                    "latitude": best.latitude,
                    "longitude": best.longitude,
                }
            )
        return AssistantPluginRunResult(
            operations=result,
            open_questions=questions,
            diagnostics=diagnostics,
        )
