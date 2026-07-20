"""Road routing provider abstraction for Roadplanner.

Roadplanner stores only derived route metrics and a simplified geometry. The
provider can be replaced without changing the canonical trip/day/stop model.
The initial implementation uses an OSRM-compatible route endpoint.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import re
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import INTEGRATION_VERSION
from .roadplanner import RoadplannerError, ValidationError

_LOGGER = logging.getLogger(__name__)

_MAX_ROUTE_POINTS = 25
_MAX_GEOMETRY_POINTS = 5_000
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}
_PROFILE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class RoutingError(RoadplannerError):
    """Raised for a sanitized routing-provider failure."""


class RoutingUrlValidationError(ValueError):
    """Raised when a configured routing endpoint is unsafe or malformed."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def normalize_routing_url(value: str) -> str:
    """Return a normalized HTTPS OSRM-compatible server base URL.

    The configured URL is a server root or path prefix. Roadplanner appends the
    canonical ``/route/v1/<profile>`` service path itself. Query strings,
    fragments and embedded credentials are deliberately rejected.
    """
    raw = str(value or "").strip()
    if not raw or any(character.isspace() or ord(character) < 32 for character in raw):
        raise RoutingUrlValidationError("Die Routing-URL enthält ungültige Zeichen")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as err:
        raise RoutingUrlValidationError("Ungültiger Port in der Routing-URL") from err
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise RoutingUrlValidationError("Die Routing-URL muss eine HTTPS-URL sein")
    if parsed.username or parsed.password:
        raise RoutingUrlValidationError("Zugangsdaten sind in der Routing-URL nicht erlaubt")
    if parsed.query or parsed.fragment:
        raise RoutingUrlValidationError("Query und Fragment sind in der Routing-URL nicht erlaubt")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii")
    except UnicodeError as err:
        raise RoutingUrlValidationError("Ungültiger Hostname in der Routing-URL") from err
    netloc = hostname
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{hostname}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = parsed.path.rstrip("/")
    # Administrators sometimes paste the full OSRM route endpoint. Strip the
    # service suffix so profile changes remain possible in the options flow.
    match = re.search(r"/route/v1/[A-Za-z0-9_-]+$", path)
    if match:
        path = path[: match.start()]
    return urlunsplit(("https", netloc, path, "", ""))


def normalize_routing_profile(value: str) -> str:
    """Validate an OSRM profile name such as ``driving`` or ``car``."""
    profile = str(value or "").strip()
    if not _PROFILE_RE.fullmatch(profile):
        raise RoutingUrlValidationError(
            "Das Routing-Profil darf nur Buchstaben, Zahlen, '_' und '-' enthalten"
        )
    return profile


def coordinate_from_location(location: Any) -> tuple[float, float] | None:
    """Return ``(latitude, longitude)`` from a Roadplanner location object."""
    if not isinstance(location, dict):
        return None
    latitude = location.get("latitude", location.get("lat"))
    longitude = location.get("longitude", location.get("lon", location.get("lng")))
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return None
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        return None
    latitude = float(latitude)
    longitude = float(longitude)
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return latitude, longitude


def route_input_hash(points: list[dict[str, Any]], profile: str) -> str:
    """Return a deterministic hash for provider-independent route caching."""
    payload = {
        "profile": profile,
        "points": [
            {
                "day_id": point.get("day_id"),
                "stop_id": point.get("stop_id"),
                "latitude": round(float(point["latitude"]), 7),
                "longitude": round(float(point["longitude"]), 7),
                "inherited": bool(point.get("inherited")),
                "mode_to_next": str(point.get("mode_to_next") or "driving"),
            }
            for point in points
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _geometry(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("type") != "LineString":
        raise RoutingError("Der Routing-Dienst hat keine gültige Liniengeometrie geliefert")
    coordinates = payload.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise RoutingError("Der Routing-Dienst hat keine nutzbare Routengeometrie geliefert")
    if len(coordinates) > _MAX_GEOMETRY_POINTS:
        raise RoutingError("Die Routengeometrie ist unerwartet groß")
    normalized: list[list[float]] = []
    for index, coordinate in enumerate(coordinates):
        if (
            not isinstance(coordinate, list)
            or len(coordinate) < 2
            or isinstance(coordinate[0], bool)
            or isinstance(coordinate[1], bool)
            or not isinstance(coordinate[0], (int, float))
            or not isinstance(coordinate[1], (int, float))
        ):
            raise RoutingError(
                f"Ungültiger Geometriepunkt an Position {index + 1}"
            )
        longitude = float(coordinate[0])
        latitude = float(coordinate[1])
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise RoutingError("Die Routengeometrie enthält ungültige Koordinaten")
        normalized.append([round(longitude, 7), round(latitude, 7)])
    return {"type": "LineString", "coordinates": normalized}


def parse_osrm_response(
    payload: Any,
    *,
    points: list[dict[str, Any]],
    provider: str,
    profile: str,
    endpoint_host: str,
    input_hash: str,
) -> dict[str, Any]:
    """Validate and normalize one OSRM route response."""
    if not isinstance(payload, dict):
        raise RoutingError("Der Routing-Dienst hat keine gültige JSON-Antwort geliefert")
    code = str(payload.get("code") or "")
    if code != "Ok":
        message = str(payload.get("message") or code or "unbekannter Fehler")
        raise RoutingError(f"Routing fehlgeschlagen: {message[:300]}")
    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
        raise RoutingError("Der Routing-Dienst hat keine Route gefunden")
    raw_route = routes[0]
    distance = raw_route.get("distance")
    duration = raw_route.get("duration")
    if (
        isinstance(distance, bool)
        or isinstance(duration, bool)
        or not isinstance(distance, (int, float))
        or not isinstance(duration, (int, float))
        or distance < 0
        or duration < 0
    ):
        raise RoutingError("Der Routing-Dienst hat ungültige Distanzdaten geliefert")
    legs_raw = raw_route.get("legs")
    legs: list[dict[str, Any]] = []
    if isinstance(legs_raw, list):
        for index, leg in enumerate(legs_raw[: max(0, len(points) - 1)]):
            if not isinstance(leg, dict):
                continue
            leg_distance = leg.get("distance")
            leg_duration = leg.get("duration")
            if not isinstance(leg_distance, (int, float)) or not isinstance(
                leg_duration, (int, float)
            ):
                continue
            source = points[index]
            target = points[index + 1]
            legs.append(
                {
                    "from_day_id": source.get("day_id"),
                    "from_stop_id": source.get("stop_id"),
                    "to_day_id": target.get("day_id"),
                    "to_stop_id": target.get("stop_id"),
                    "distance_m": round(float(leg_distance), 1),
                    "duration_s": round(float(leg_duration), 1),
                }
            )
    return {
        "schema_version": 1,
        "status": "calculated",
        "provider": provider,
        "profile": profile,
        "endpoint_host": endpoint_host,
        "calculated_at": _utc_now_iso(),
        "input_hash": input_hash,
        "point_count": len(points),
        "distance_m": round(float(distance), 1),
        "duration_s": round(float(duration), 1),
        "geometry": _geometry(raw_route.get("geometry")),
        "legs": legs,
        "stop_refs": [
            {
                "day_id": point.get("day_id"),
                "stop_id": point.get("stop_id"),
                "inherited": bool(point.get("inherited")),
            }
            for point in points
        ],
        "managed_metrics": True,
    }


@dataclass(slots=True)
class RoutingHealth:
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    rate_limited_requests: int = 0
    last_success_at: str | None = None
    last_error_at: str | None = None
    last_error: str | None = None
    last_duration_ms: int | None = None


class OSRMRoutingClient:
    """Small, rate-limited OSRM route client."""

    def __init__(
        self,
        hass: Any,
        *,
        enabled: bool,
        base_url: str,
        profile: str,
        request_timeout: int = 45,
        min_request_interval: float = 1.1,
    ) -> None:
        self._session: ClientSession = async_get_clientsession(hass)
        self._enabled = bool(enabled)
        self._base_url = normalize_routing_url(base_url)
        self._profile = normalize_routing_profile(profile)
        self._request_timeout = max(10, min(int(request_timeout), 180))
        self._min_request_interval = max(0.2, min(float(min_request_interval), 30.0))
        self._request_lock = asyncio.Lock()
        self._last_dispatch = 0.0
        self._health = RoutingHealth()

    @property
    def name(self) -> str:
        return "osrm"

    @property
    def profile(self) -> str:
        return self._profile

    @property
    def configured(self) -> bool:
        return self._enabled and bool(self._base_url)

    @property
    def endpoint_host(self) -> str:
        return urlsplit(self._base_url).hostname or ""

    def health_snapshot(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "provider": self.name,
            "profile": self._profile,
            "endpoint_host": self.endpoint_host,
            "request_timeout": self._request_timeout,
            "min_request_interval": self._min_request_interval,
            **asdict(self._health),
        }

    def _endpoint(self, points: list[dict[str, Any]]) -> str:
        coordinate_path = ";".join(
            f"{float(point['longitude']):.7f},{float(point['latitude']):.7f}"
            for point in points
        )
        return (
            f"{self._base_url.rstrip('/')}/route/v1/{self._profile}/"
            f"{coordinate_path}"
        )

    async def async_calculate(
        self,
        points: list[dict[str, Any]],
        *,
        input_hash: str,
    ) -> dict[str, Any]:
        """Calculate one ordered route through all supplied points."""
        if not self.configured:
            raise RoutingError("Die Routenberechnung ist nicht aktiviert")
        if not isinstance(points, list) or len(points) < 2:
            raise ValidationError("Für eine Straßenroute werden mindestens zwei GPS-Punkte benötigt")
        if len(points) > _MAX_ROUTE_POINTS:
            raise ValidationError(
                f"Ein Reisetag darf für die Routenberechnung maximal {_MAX_ROUTE_POINTS} GPS-Punkte enthalten"
            )
        async with self._request_lock:
            wait = self._min_request_interval - (time.monotonic() - self._last_dispatch)
            if wait > 0:
                await asyncio.sleep(wait)
            endpoint = self._endpoint(points)
            params = {
                "overview": "simplified",
                "geometries": "geojson",
                "steps": "false",
            }
            headers = {
                "Accept": "application/json",
                "User-Agent": f"HomeAssistant-Roadplanner/{INTEGRATION_VERSION}",
            }
            last_error: Exception | None = None
            for attempt in range(2):
                self._last_dispatch = time.monotonic()
                self._health.total_requests += 1
                started = time.monotonic()
                try:
                    async with asyncio.timeout(self._request_timeout):
                        async with self._session.get(
                            endpoint,
                            params=params,
                            headers=headers,
                            allow_redirects=False,
                        ) as response:
                            raw_body = await response.content.read(_MAX_RESPONSE_BYTES + 1)
                            if len(raw_body) > _MAX_RESPONSE_BYTES:
                                self._health.failed_requests += 1
                                self._health.last_error_at = _utc_now_iso()
                                self._health.last_error = "Antwort zu groß"
                                raise RoutingError(
                                    "Der Routing-Dienst hat eine unerwartet große Antwort geliefert"
                                )
                            text = raw_body.decode("utf-8", errors="replace")
                            response_status = response.status
                            response_headers = dict(response.headers)
                except (TimeoutError, ClientError, OSError) as err:
                    last_error = err
                    if attempt == 0:
                        await asyncio.sleep(1.0)
                        continue
                    self._health.failed_requests += 1
                    self._health.last_error_at = _utc_now_iso()
                    self._health.last_error = "Netzwerk- oder Zeitüberschreitungsfehler"
                    raise RoutingError(
                        "Der Routing-Dienst ist derzeit nicht erreichbar"
                    ) from err
                self._health.last_duration_ms = int((time.monotonic() - started) * 1000)
                if response_status in _TRANSIENT_STATUS and attempt == 0:
                    if response_status == 429:
                        self._health.rate_limited_requests += 1
                    retry_after = str(response_headers.get("Retry-After") or "").strip()
                    try:
                        delay = max(1.0, min(float(retry_after), 30.0))
                    except ValueError:
                        delay = 1.5
                    await asyncio.sleep(delay)
                    continue
                if response_status != 200:
                    self._health.failed_requests += 1
                    self._health.last_error_at = _utc_now_iso()
                    self._health.last_error = f"HTTP {response_status}"
                    raise RoutingError(
                        f"Der Routing-Dienst hat die Anfrage abgelehnt (HTTP {response_status})"
                    )
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError as err:
                    self._health.failed_requests += 1
                    self._health.last_error_at = _utc_now_iso()
                    self._health.last_error = "Ungültige JSON-Antwort"
                    raise RoutingError(
                        "Der Routing-Dienst hat ungültige Daten geliefert"
                    ) from err
                try:
                    result = parse_osrm_response(
                        payload,
                        points=points,
                        provider=self.name,
                        profile=self._profile,
                        endpoint_host=self.endpoint_host,
                        input_hash=input_hash,
                    )
                except RoutingError:
                    self._health.failed_requests += 1
                    self._health.last_error_at = _utc_now_iso()
                    self._health.last_error = "Ungültige oder nicht routbare Antwort"
                    raise
                self._health.successful_requests += 1
                self._health.last_success_at = _utc_now_iso()
                self._health.last_error = None
                return result
            raise RoutingError("Die Routenberechnung ist fehlgeschlagen") from last_error


def haversine_distance_m(first: dict[str, Any], second: dict[str, Any]) -> float:
    """Return the great-circle distance between two normalized route points."""
    from math import asin, cos, radians, sin, sqrt

    lat1 = radians(float(first["latitude"]))
    lon1 = radians(float(first["longitude"]))
    lat2 = radians(float(second["latitude"]))
    lon2 = radians(float(second["longitude"]))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    value = sin(dlat / 2.0) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2.0) ** 2
    return round(6_371_000.0 * 2.0 * asin(min(1.0, sqrt(value))), 1)


def split_route_segments(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split ordered points into road, ferry, and intentionally disconnected segments."""
    if len(points) < 2:
        return []
    segments: list[dict[str, Any]] = []
    driving_points: list[dict[str, Any]] = [points[0]]

    def flush_driving() -> None:
        nonlocal driving_points
        if len(driving_points) >= 2:
            segments.append({"mode": "driving", "points": driving_points})
        driving_points = []

    for index, source in enumerate(points[:-1]):
        target = points[index + 1]
        mode = str(source.get("mode_to_next") or "driving").casefold()
        if mode not in {"driving", "ferry", "break"}:
            mode = "driving"
        if mode == "driving":
            if not driving_points:
                driving_points = [source]
            elif driving_points[-1].get("stop_id") != source.get("stop_id"):
                driving_points.append(source)
            driving_points.append(target)
            continue
        flush_driving()
        segments.append(
            {
                "mode": mode,
                "points": [source, target],
                "reason": str(source.get("mode_reason") or "")[:500] or None,
            }
        )
        driving_points = [target]
    flush_driving()
    return segments


def ferry_route_segment(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a non-routed straight-line ferry segment between two terminals."""
    source, target = points[0], points[-1]
    distance_m = haversine_distance_m(source, target)
    return {
        "mode": "ferry",
        "status": "calculated",
        "distance_m": distance_m,
        "duration_s": None,
        "geometry": {
            "type": "LineString",
            "coordinates": [
                [round(float(source["longitude"]), 7), round(float(source["latitude"]), 7)],
                [round(float(target["longitude"]), 7), round(float(target["latitude"]), 7)],
            ],
        },
        "from_day_id": source.get("day_id"),
        "from_stop_id": source.get("stop_id"),
        "to_day_id": target.get("day_id"),
        "to_stop_id": target.get("stop_id"),
    }


def disconnected_route_segment(segment: dict[str, Any]) -> dict[str, Any]:
    """Return a visible gap marker without drawing or inventing a route."""
    points = segment.get("points") or []
    source = points[0] if points else {}
    target = points[-1] if points else {}
    return {
        "mode": "break",
        "status": "incomplete",
        "distance_m": None,
        "duration_s": None,
        "geometry": None,
        "reason": str(segment.get("reason") or "Routenabschnitt ist noch nicht vollständig modelliert")[:500],
        "from_day_id": source.get("day_id"),
        "from_stop_id": source.get("stop_id"),
        "to_day_id": target.get("day_id"),
        "to_stop_id": target.get("stop_id"),
    }


def combine_route_segments(
    *,
    points: list[dict[str, Any]],
    segment_results: list[dict[str, Any]],
    provider: str,
    profile: str,
    endpoint_host: str,
    input_hash: str,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Combine provider road segments and local ferry/gap segments."""
    normalized_segments: list[dict[str, Any]] = []
    drive_distance = 0.0
    drive_duration = 0.0
    ferry_distance = 0.0
    ferry_duration = 0.0
    has_ferry = False
    has_gap = False
    legs: list[dict[str, Any]] = []

    for raw in segment_results:
        segment = dict(raw)
        mode = str(segment.get("mode") or "driving")
        segment["mode"] = mode
        if mode == "driving":
            drive_distance += float(segment.get("distance_m") or 0.0)
            drive_duration += float(segment.get("duration_s") or 0.0)
            for leg in segment.get("legs") or []:
                if isinstance(leg, dict):
                    legs.append({**leg, "mode": "driving"})
        elif mode == "ferry":
            has_ferry = True
            ferry_distance += float(segment.get("distance_m") or 0.0)
            ferry_duration += float(segment.get("duration_s") or 0.0)
            legs.append(
                {
                    "mode": "ferry",
                    "from_day_id": segment.get("from_day_id"),
                    "from_stop_id": segment.get("from_stop_id"),
                    "to_day_id": segment.get("to_day_id"),
                    "to_stop_id": segment.get("to_stop_id"),
                    "distance_m": segment.get("distance_m"),
                    "duration_s": segment.get("duration_s"),
                }
            )
        else:
            has_gap = True
            legs.append(
                {
                    "mode": "break",
                    "from_day_id": segment.get("from_day_id"),
                    "from_stop_id": segment.get("from_stop_id"),
                    "to_day_id": segment.get("to_day_id"),
                    "to_stop_id": segment.get("to_stop_id"),
                    "reason": segment.get("reason"),
                }
            )
        normalized_segments.append(segment)

    all_warnings = [str(item)[:500] for item in (warnings or []) if str(item).strip()]
    for segment in normalized_segments:
        if segment.get("mode") == "break" and segment.get("reason") and segment["reason"] not in all_warnings:
            all_warnings.append(segment["reason"])

    result: dict[str, Any] = {
        "schema_version": 2,
        "status": "partial" if has_gap else "calculated",
        "provider": "mixed" if has_ferry else provider,
        "road_provider": provider,
        "profile": profile,
        "endpoint_host": endpoint_host,
        "calculated_at": _utc_now_iso(),
        "input_hash": input_hash,
        "point_count": len(points),
        "distance_m": round(drive_distance, 1),
        "duration_s": round(drive_duration, 1),
        "ferry_distance_m": round(ferry_distance, 1),
        "ferry_duration_s": round(ferry_duration, 1) if ferry_duration else None,
        "total_movement_m": round(drive_distance + ferry_distance, 1),
        "segments": normalized_segments,
        "legs": legs,
        "warnings": all_warnings[:100],
        "gap_count": sum(1 for item in normalized_segments if item.get("mode") == "break"),
        "ferry_segment_count": sum(1 for item in normalized_segments if item.get("mode") == "ferry"),
        "stop_refs": [
            {
                "day_id": point.get("day_id"),
                "stop_id": point.get("stop_id"),
                "inherited": bool(point.get("inherited")),
            }
            for point in points
        ],
        "managed_metrics": True,
    }
    # Preserve a legacy single geometry only if there is exactly one drawable
    # segment. Multi-modal routes must remain split so no false connector is drawn.
    drawable = [item for item in normalized_segments if isinstance(item.get("geometry"), dict)]
    if len(drawable) == 1:
        result["geometry"] = drawable[0]["geometry"]
    return result
