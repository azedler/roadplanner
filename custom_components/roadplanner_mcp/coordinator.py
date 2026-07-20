"""Roadplanner update coordinator."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .manager import RoadplannerManager
from .roadplanner import RoadplannerError

_LOGGER = logging.getLogger(__name__)


class RoadplannerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll only for external edits and suppress identical updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        manager: RoadplannerManager,
        refresh_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Roadplanner",
            config_entry=entry,
            update_interval=timedelta(seconds=refresh_interval),
            always_update=False,
        )
        self.manager = manager

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.manager.async_refresh_payload()
        except RoadplannerError as err:
            raise UpdateFailed(str(err)) from err
