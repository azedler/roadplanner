"""Provider orchestration for reviewed Roadplanner place resolution."""

from __future__ import annotations

import logging
from typing import Any

from .geocoding import (
    GeocodingCandidate,
    GeocodingError,
    GeocodingProvider,
    StructuredAddress,
)
from .google_places import GooglePlacesClient

_LOGGER = logging.getLogger(__name__)


class CompositePlaceProvider:
    """Combine Nominatim with optional Google Places in a stable order."""

    def __init__(
        self,
        nominatim: GeocodingProvider | None,
        google: GooglePlacesClient | None,
        *,
        mode: str = "fallback",
    ) -> None:
        self.nominatim = nominatim
        self.google = google
        self.mode = "preferred" if str(mode).casefold() == "preferred" else "fallback"
        self.enabled = bool(
            (nominatim is not None and getattr(nominatim, "enabled", False))
            or (google is not None and google.enabled)
        )

    @property
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "nominatim": {
                "enabled": bool(
                    self.nominatim is not None
                    and getattr(self.nominatim, "enabled", False)
                )
            },
            "google_places": (
                self.google.status.as_dict()
                if self.google is not None
                else {
                    "enabled": False,
                    "configured": False,
                    "state": "not_configured",
                    "requests_today": 0,
                    "daily_limit": 0,
                    "last_error": None,
                }
            ),
        }

    def _ordered(self) -> list[GeocodingProvider]:
        values: list[GeocodingProvider] = []
        if self.mode == "preferred":
            if self.google is not None and self.google.enabled:
                values.append(self.google)
            if self.nominatim is not None and getattr(self.nominatim, "enabled", False):
                values.append(self.nominatim)
        else:
            if self.nominatim is not None and getattr(self.nominatim, "enabled", False):
                values.append(self.nominatim)
            if self.google is not None and self.google.enabled:
                values.append(self.google)
        return values

    @staticmethod
    def _identity(candidate: GeocodingCandidate) -> tuple[Any, ...]:
        provider_id = candidate.canonical_provider_id
        if provider_id:
            return (candidate.provider, provider_id)
        return (
            round(candidate.latitude, 6),
            round(candidate.longitude, 6),
            candidate.preferred_name.casefold(),
        )

    async def async_resolve(
        self,
        query: str,
        *,
        structured_address: StructuredAddress | None = None,
        language: str = "de",
        location_bias: tuple[float, float] | None = None,
    ) -> tuple[GeocodingCandidate | None, list[GeocodingCandidate]]:
        """Use the configured primary provider and a deliberate fallback."""
        if not self.enabled:
            return None, []
        ordered = self._ordered()
        candidates: dict[tuple[Any, ...], tuple[int, GeocodingCandidate]] = {}
        safe_default: GeocodingCandidate | None = None
        errors: list[GeocodingError] = []

        for provider_index, provider in enumerate(ordered):
            try:
                best, alternatives = await provider.async_resolve(
                    query,
                    structured_address=structured_address,
                    language=language,
                    location_bias=location_bias,
                )
            except GeocodingError as err:
                errors.append(err)
                _LOGGER.debug(
                    "Place provider %s failed: %s",
                    type(provider).__name__,
                    type(err).__name__,
                )
                continue
            for candidate in alternatives:
                identity = self._identity(candidate)
                previous = candidates.get(identity)
                if previous is None or candidate.score > previous[1].score:
                    candidates[identity] = (provider_index, candidate)
            if best is not None:
                safe_default = best
                # A safe result from the configured primary provider avoids an
                # unnecessary second billable/network request.  Weak or merely
                # local results have ``best is None`` and deliberately fall
                # through to the next provider.
                break

        if not candidates and safe_default is None and errors:
            raise errors[0]

        ordered_candidates = list(candidates.values())
        ordered_candidates.sort(
            key=lambda item: (
                int(safe_default is not None and item[1] is safe_default),
                int(item[1].auto_selectable),
                item[1].score,
                item[1].core_token_match,
                -item[0],
            ),
            reverse=True,
        )
        return safe_default, [candidate for _, candidate in ordered_candidates[:8]]

    async def async_reverse(
        self,
        latitude: float,
        longitude: float,
        *,
        language: str = "de",
    ) -> GeocodingCandidate | None:
        """Use Nominatim for durable coordinate validation and address context.

        Google Places is deliberately a transient discovery provider.  A
        selected Google result is normalized through this reverse lookup before
        Roadplanner writes a persistent place profile.
        """
        if self.nominatim is None or not getattr(self.nominatim, "enabled", False):
            return None
        return await self.nominatim.async_reverse(
            latitude,
            longitude,
            language=language,
        )


__all__ = ["CompositePlaceProvider"]
