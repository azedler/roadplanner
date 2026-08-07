"""Write the two fields the story editor is allowed to change.

The whole point of this module is what it *cannot* do. The story editor is
an editorial layer on top of the roadbook, not a second way of editing the
trip: it may replace a chapter's title and its story text, and nothing
else. Stops, times, places, distances and routes are facts, they belong to
the roadbook, and no path from the editor reaches them.

Three properties make that true rather than merely intended:

**One narrow patch, built here.** The service composes the ``details``
patch itself from the day's current details plus at most two keys. The
browser never sends a details object, so a stale or malicious client
cannot replace a day's details wholesale - which matters more than it
looks, because the mutation layer *replaces* ``details`` rather than
merging it. Sending the whole object from the client would mean any
browser tab could silently drop the stored summary it had never loaded.

**Every write goes through the existing revision-checked mutation layer.**
Nothing here touches the repository directly. A client that loaded the trip
before somebody else's edit carries the older revision, the mutation layer
refuses it, and the user is told - rather than the older text quietly
winning.

**Removing an override removes the key.** Not an empty string: the key
goes. That is what makes the fallback exact - the day's details end up
byte-identical to before the override existed, so the rebuilt manifest has
the same content hash it had before. An empty string left behind would
also read as "no override", but it would keep the manifest's inputs
subtly different forever.

The manifest itself is never stored. After a successful write the cached
description is dropped and rebuilt from the canonical data, which is the
only way the manifest can stay a derived thing rather than a second truth.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .roadplanner import ValidationError
from .story_context_builder import OVERRIDE_STORY_KEY, OVERRIDE_TITLE_KEY
from .travel_story_manifest import (
    MAX_STORY_LENGTH,
    MAX_TITLE_LENGTH,
    clean_line,
    clean_story,
)

_LOGGER = logging.getLogger(__name__)

# What the editor may address, and the detail key each one is stored in.
EDITABLE_FIELDS = {
    "title": OVERRIDE_TITLE_KEY,
    "story": OVERRIDE_STORY_KEY,
}


class StoryOverrideService:
    """Set or clear a chapter's hand-written title and story."""

    def __init__(
        self,
        hass: HomeAssistant,
        manager: Any,
        story_context: Any,
    ) -> None:
        self._hass = hass
        self._manager = manager
        self._story_context = story_context

    async def async_set(
        self,
        *,
        trip_id: str,
        day_id: str,
        changes: dict[str, Any],
        expected_revision: Any,
        actor: str,
    ) -> dict[str, Any]:
        """Apply the changes the editor made and return the new manifest.

        ``changes`` carries only the fields the user actually touched. A
        value that is empty or ``None`` means "remove this override and
        fall back"; anything else replaces it. A field that is absent is
        left exactly as it was, so editing a title cannot disturb a story.
        """
        trip_id = str(trip_id or "").strip()
        day_id = str(day_id or "").strip()
        if not trip_id or not day_id:
            raise ValidationError("Für die Bearbeitung fehlen Reise und Reisetag")
        if not isinstance(changes, dict) or not changes:
            raise ValidationError("Es wurde nichts zum Speichern übergeben")
        unknown = set(changes) - set(EDITABLE_FIELDS)
        if unknown:
            # Naming them is the point: this is the boundary of the
            # editorial layer, and a silent drop would hide that a client
            # tried to cross it.
            raise ValidationError(
                "Der Story-Editor darf diese Felder nicht ändern: "
                + ", ".join(sorted(unknown))
            )

        day = await self._async_day(trip_id, day_id)
        details = dict(day.get("details") or {})
        touched: list[str] = []
        removed: list[str] = []
        for field, key in EDITABLE_FIELDS.items():
            if field not in changes:
                continue
            value = (
                clean_line(changes[field], limit=MAX_TITLE_LENGTH)
                if field == "title"
                else clean_story(changes[field], limit=MAX_STORY_LENGTH)
            )
            if value:
                details[key] = value
                touched.append(field)
            elif key in details:
                # The key goes, not its value. See the module docstring:
                # this is what makes the fallback exact.
                details.pop(key)
                removed.append(field)

        if not touched and not removed:
            # Nothing to do is not an error, but it must not consume a
            # revision either - an edit that changed nothing would still
            # invalidate every other browser's expected revision.
            return await self._async_result(trip_id, changed=False)

        await self._manager.async_update_day(
            day_id=day_id,
            patch={"details": details},
            actor=actor,
            expected_revision=expected_revision,
            expected_trip_id=trip_id,
        )
        _LOGGER.debug(
            "Story-Override für %s: gesetzt %s, entfernt %s",
            day_id,
            touched or "-",
            removed or "-",
        )
        return await self._async_result(trip_id, changed=True)

    async def _async_result(self, trip_id: str, *, changed: bool) -> dict[str, Any]:
        """The rebuilt description, never a stored one."""
        if changed:
            self._story_context.invalidate(trip_id)
        manifest = await self._story_context.async_manifest(trip_id)
        return {"changed": changed, "story_manifest": manifest}

    async def _async_day(self, trip_id: str, day_id: str) -> dict[str, Any]:
        """The day as the roadbook currently has it.

        Read through the same payload the builder uses, so the editor and
        the description can never disagree about what a day contains.
        """
        payload = await self._manager.async_get_assistant_payload(trip_id)
        if str(payload.get("selected_trip_id") or "") != trip_id:
            raise ValidationError("Die Reise für den Story-Editor ist nicht ladbar")
        for day in (payload.get("days", {}) or {}).get("days", []) or []:
            if isinstance(day, dict) and str(day.get("id") or "") == day_id:
                return day
        raise ValidationError("Der Reisetag gehört nicht zu dieser Reise")


__all__ = ["EDITABLE_FIELDS", "StoryOverrideService"]
