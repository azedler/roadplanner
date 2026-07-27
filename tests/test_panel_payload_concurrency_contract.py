"""Source-level contract: independent panel-payload subsystems must be
fetched concurrently, not one after another, on every single panel load.

Regression guard for a real latency finding: websocket_get_panel_data used to
await the trip/day payload, then travel_archive, then experience, then crew,
strictly one after another, even though most of them have no dependency on
each other - turning every click into up to four sequential round trips
instead of two.
"""
from pathlib import Path

PANEL = Path("custom_components/roadplanner_mcp/panel.py").read_text(encoding="utf-8")

assert "import asyncio" in PANEL

# crew_state has no dependency on the selected trip at all, so it must be
# fetched alongside the main payload, not after it.
assert (
    "payload, crew_state = await asyncio.gather(\n"
    "            runtime.manager.async_get_panel_payload(msg.get(\"trip_id\")),\n"
    "            runtime.crew.async_panel_payload(),\n"
    "        )"
) in PANEL

# archive_state and experience_state both only depend on the already-resolved
# selected_trip_id (and experience_state on payload's own days), not on each
# other, so they must run together too.
assert (
    "archive_state, experience_state = await asyncio.gather(\n"
    "            runtime.travel_archive.async_panel_payload(selected_trip_id),\n"
) in PANEL

print("Panel payload concurrency contract tests passed.")
