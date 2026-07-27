"""Async Home Assistant facade for the cross-trip crew registry."""

from __future__ import annotations

from functools import partial
from typing import Any

from homeassistant.core import HomeAssistant

from .crew_store import CrewStore


class CrewManager:
    """Serialize crew (people/vehicle) master-data mutations."""

    def __init__(self, hass: HomeAssistant, store: CrewStore) -> None:
        self.hass = hass
        self.store = store

    async def async_initialize(self) -> None:
        await self.hass.async_add_executor_job(self.store.initialize)

    async def async_panel_payload(self) -> dict[str, Any]:
        people = await self.hass.async_add_executor_job(self.store.load_people)
        vehicles = await self.hass.async_add_executor_job(self.store.load_vehicles)
        return {"people": people, "vehicles": vehicles}

    async def async_create_person(self, *, value: dict[str, Any]) -> dict[str, Any]:
        return await self.hass.async_add_executor_job(
            partial(self.store.create_person, value)
        )

    async def async_update_person(
        self, *, person_id: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.hass.async_add_executor_job(
            partial(self.store.update_person, person_id, patch)
        )

    async def async_set_person_active(
        self, *, person_id: str, active: bool
    ) -> dict[str, Any]:
        return await self.hass.async_add_executor_job(
            partial(self.store.set_person_active, person_id, active)
        )

    async def async_create_vehicle(self, *, value: dict[str, Any]) -> dict[str, Any]:
        return await self.hass.async_add_executor_job(
            partial(self.store.create_vehicle, value)
        )

    async def async_update_vehicle(
        self, *, vehicle_id: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.hass.async_add_executor_job(
            partial(self.store.update_vehicle, vehicle_id, patch)
        )

    async def async_set_vehicle_active(
        self, *, vehicle_id: str, active: bool
    ) -> dict[str, Any]:
        return await self.hass.async_add_executor_job(
            partial(self.store.set_vehicle_active, vehicle_id, active)
        )
