"""Stable, bounded Roadplanner sensors for dashboards and mobile clients."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, INTEGRATION_VERSION, NAME
from .coordinator import RoadplannerCoordinator

MAX_DASHBOARD_DAYS = 60
MAX_DASHBOARD_STOPS = 100
MAX_STATE_TEXT_LENGTH = 250


@dataclass(frozen=True, kw_only=True)
class RoadplannerSensorDescription(SensorEntityDescription):
    """Describe how a Roadplanner sensor derives its value."""

    value_fn: Callable[[dict[str, Any]], Any]
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _state_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= MAX_STATE_TEXT_LENGTH else text[:247] + "…"


def _escape_markdown(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    for character in (
        "\\",
        "`",
        "*",
        "_",
        "{",
        "}",
        "[",
        "]",
        "(",
        ")",
        "<",
        ">",
        "#",
        "+",
        "-",
        "!",
        "|",
    ):
        text = text.replace(character, "\\" + character)
    return text


def _next_day(payload: dict[str, Any]) -> dict[str, Any] | None:
    days = payload.get("days", [])
    today = dt_util.now().date()
    for day in days:
        parsed = dt_util.parse_date(day.get("date")) if day.get("date") else None
        if parsed is not None and parsed >= today:
            return day
    return next((day for day in days if not day.get("date")), None)


def _next_stop(payload: dict[str, Any]) -> dict[str, Any] | None:
    day = _next_day(payload)
    if day is None:
        return None
    return next(
        (
            stop
            for stop in payload.get("stops", [])
            if stop.get("day_id") == day["id"]
        ),
        None,
    )


def _route_markdown(payload: dict[str, Any]) -> str:
    days = payload.get("days", [])
    stops = payload.get("stops", [])
    if not days:
        return "Noch keine Reisetage geplant. Öffne Assist, um zu beginnen."
    lines: list[str] = []
    written_stops = 0
    for day in days[:MAX_DASHBOARD_DAYS]:
        lines.append(
            f"### {day['sequence']}. {_escape_markdown(day['title'])}  \n"
            f"{_escape_markdown(day.get('date') or 'ohne Datum')} · "
            f"{_escape_markdown(day.get('start') or '?')} → "
            f"{_escape_markdown(day.get('end') or '?')}"
        )
        for stop in stops:
            if stop.get("day_id") != day["id"]:
                continue
            if written_stops >= MAX_DASHBOARD_STOPS:
                break
            lines.append(
                f"- {_escape_markdown(stop['name'])} "
                f"({_escape_markdown(stop['type'])})"
            )
            written_stops += 1
        lines.append("")
        if written_stops >= MAX_DASHBOARD_STOPS:
            break
    if len(days) > MAX_DASHBOARD_DAYS or len(stops) > written_stops:
        lines.append("_Weitere Einträge sind über den Reiseassistenten verfügbar._")
    return "\n".join(lines).rstrip()


def _next_stop_attributes(payload: dict[str, Any]) -> dict[str, Any]:
    stop = _next_stop(payload)
    day = _next_day(payload)
    if stop is None and day is None:
        return {}
    result: dict[str, Any] = {}
    if day is not None:
        result.update(
            {
                "day_id": day["id"],
                "day_sequence": day["sequence"],
                "day_date": day.get("date"),
                "day_title": _state_text(day.get("title")),
            }
        )
    if stop is not None:
        result.update(
            {
                "stop_id": stop["id"],
                "stop_type": stop.get("type"),
                "arrival_time": stop.get("arrival_time"),
                "departure_time": stop.get("departure_time"),
            }
        )
        location = stop.get("location")
        if isinstance(location, dict):
            for key in ("latitude", "longitude", "address", "city", "country_code"):
                value = location.get(key)
                if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                    result[key] = _state_text(value) if isinstance(value, str) else value
    return result


SENSOR_DESCRIPTIONS: tuple[RoadplannerSensorDescription, ...] = (
    RoadplannerSensorDescription(
        key="trip",
        translation_key="trip",
        icon="mdi:map-marker-path",
        value_fn=lambda payload: _state_text(payload["trip"]["title"]),
        attributes_fn=lambda payload: {
            "trip_id": payload["trip"]["id"],
            "status": payload["trip"]["status"],
            "start_date": payload["trip"].get("start_date"),
            "end_date": payload["trip"].get("end_date"),
            "updated_at": payload["metadata"]["updated_at"],
        },
    ),
    RoadplannerSensorDescription(
        key="days",
        translation_key="days",
        icon="mdi:calendar-range",
        value_fn=lambda payload: payload.get("day_count", 0),
    ),
    RoadplannerSensorDescription(
        key="stops",
        translation_key="stops",
        icon="mdi:map-marker-multiple",
        value_fn=lambda payload: payload.get("stop_count", 0),
    ),
    RoadplannerSensorDescription(
        key="next_stop",
        translation_key="next_stop",
        icon="mdi:map-marker-distance",
        value_fn=lambda payload: _state_text(
            (_next_stop(payload) or _next_day(payload) or {}).get("name")
            or (_next_day(payload) or {}).get("title")
        ),
        attributes_fn=_next_stop_attributes,
    ),
    RoadplannerSensorDescription(
        key="revision",
        translation_key="revision",
        icon="mdi:file-document-refresh",
        value_fn=lambda payload: payload["metadata"]["revision"],
        attributes_fn=lambda payload: {
            "updated_at": payload["metadata"]["updated_at"],
            "updated_by": payload["metadata"]["updated_by"],
            "last_operation": payload["metadata"].get("last_operation"),
        },
    ),
    RoadplannerSensorDescription(
        key="route",
        translation_key="route",
        icon="mdi:routes",
        value_fn=lambda payload: payload["metadata"]["revision"],
        attributes_fn=lambda payload: {
            "markdown": _route_markdown(payload),
            "day_count": payload.get("day_count", 0),
            "stop_count": payload.get("stop_count", 0),
            "total_distance_km": payload.get("total_distance_km"),
            "total_drive_minutes": payload.get("total_drive_minutes"),
            "route_metrics": payload.get("route_metrics", {}),
            "truncated": (
                payload.get("days_truncated", False)
                or payload.get("stops_truncated", False)
                or payload.get("day_count", 0) > MAX_DASHBOARD_DAYS
                or payload.get("stop_count", 0) > MAX_DASHBOARD_STOPS
            ),
        },
    ),
    RoadplannerSensorDescription(
        key="handoffs",
        translation_key="handoffs",
        icon="mdi:inbox-arrow-down",
        value_fn=lambda payload: payload.get("pending_handoff_count", 0),
        attributes_fn=lambda payload: {
            "status_counts": payload.get("handoff_status_counts", {}),
            "last_scan": payload.get("handoff_scan_result", {}),
            "last_auto_apply": payload.get("handoff_auto_apply_result", {}),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Roadplanner sensors from the shared coordinator."""
    runtime = entry.runtime_data
    async_add_entities(
        RoadplannerSensor(runtime.coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    )


class RoadplannerSensor(CoordinatorEntity[RoadplannerCoordinator], SensorEntity):
    """A coordinator-backed Roadplanner sensor."""

    entity_description: RoadplannerSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RoadplannerCoordinator,
        entry: ConfigEntry,
        description: RoadplannerSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_suggested_object_id = f"roadplanner_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=NAME,
            manufacturer="Roadplanner",
            model="Home Assistant Roadplanner",
            sw_version=INTEGRATION_VERSION,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.entity_description.key == "next_stop":
            self.async_on_remove(
                async_track_time_change(
                    self.hass,
                    self._handle_local_day_change,
                    hour=0,
                    minute=0,
                    second=5,
                )
            )

    @callback
    def _handle_local_day_change(self, _now: datetime) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)
