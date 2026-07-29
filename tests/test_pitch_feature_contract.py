"""Wiring contract for the per-day overnight plan (Stellplatz-Optionen).

Source-level assertions that every layer of the feature stays connected:
pure logic -> atomic mutation -> facade -> manager -> panel action ->
frontend tab/dialog/Plan-B card. The architectural core being pinned down:
options are anchored on the DAY (day.details.overnight_plan), never on the
active overnight stop, and activation is one atomic commit.
"""
from pathlib import Path

COMPONENT = Path("custom_components/roadplanner_mcp")
FRONTEND = COMPONENT / "frontend"

pitch_options = (COMPONENT / "pitch_options.py").read_text(encoding="utf-8")
mutations = (COMPONENT / "trip_mutations.py").read_text(encoding="utf-8")
facade = (COMPONENT / "roadplanner.py").read_text(encoding="utf-8")
manager = (COMPONENT / "manager.py").read_text(encoding="utf-8")
panel = (COMPONENT / "panel.py").read_text(encoding="utf-8")
panel_js = (FRONTEND / "roadplanner-panel.js").read_text(encoding="utf-8")
pitches_js = (FRONTEND / "features" / "pitches.js").read_text(encoding="utf-8")
day_js = (FRONTEND / "features" / "trip-day-stop.js").read_text(encoding="utf-8")

# Day-anchored storage: the plan lives in day.details, the stop only carries
# a back-reference. (A stable stop_id preserves media/document links but not
# their semantic correctness - see the architecture review.)
assert 'PLAN_KEY = "overnight_plan"' in pitch_options
assert '"overnight_option_id"' in pitch_options
assert '"overnight_selection"' in pitch_options
assert "PLAN_SCHEMA_VERSION" in pitch_options
assert "MAX_OVERNIGHT_OPTIONS = 6" in pitch_options

# Atomic activation: one mutation commit touches day details AND stops.
assert "def mutate_overnight_plan(" in mutations
assert "activate_option" in mutations
assert "def update_pitch_preferences(" in mutations
assert 'operation=f"pitch_{operation}"' in mutations

# Facade and manager wiring.
assert "def mutate_overnight_plan(" in facade
assert "def update_pitch_preferences(" in facade
assert "async_mutate_overnight_plan" in manager
assert "async_update_pitch_preferences" in manager

# Panel actions: registered, edit-gated, and dispatched to the manager.
for action in (
    "pitch_option_save",
    "pitch_option_delete",
    "pitch_option_set_status",
    "pitch_set_strategy",
    "pitch_option_activate",
    "pitch_update_preferences",
):
    assert panel.count(f'"{action}"') >= 2, f"{action} must be in _ACTIONS and _EDIT_ACTIONS"
assert "async_mutate_overnight_plan" in panel
assert "async_update_pitch_preferences" in panel

# Frontend: tab, dispatcher entries, dialog, Plan-B card in the Heute tab.
assert '"pitches", "mdi:caravan", "Stellplätze"' in panel_js
assert "_renderPitches()" in panel_js
assert '"pitch-option"' in panel_js and "_renderPitchOptionForm" in panel_js
assert '"pitch-preferences"' in panel_js or "pitch-preferences" in panel_js
assert "pitch_option_activate" in panel_js, "activate must be trip-scoped (expected_trip_id)"
assert 'data-action="pitch-activate"' in pitches_js
assert 'data-action="pitch-strategy"' in pitches_js
assert "_renderPlanBCard" in pitches_js
assert "_renderPlanBCard(day)" in day_js, "the Heute tab must offer Plan B"

# Rejected options must be excluded from Plan B suggestions.
assert 'status !== "rejected"' in pitches_js

# The tab must default to the current/upcoming day and let the user jump to
# any other day, rather than stacking every day's card (a real usability
# regression the user hit once the feature saw daily use).
assert 'data-action="pitch-select-day"' in pitches_js
assert "_pitchCurrentDayId" in pitches_js
assert "next_day" in pitches_js

# Route context: where the day starts, tomorrow's first stop, and the
# options plotted on the day's map - not just a bare list of names.
assert "_pitchRouteContext" in pitches_js
assert "pitch-route-flow" in pitches_js

# Pros/cons must stand out visually, not be buried in one plain-text line.
assert "pitch-chip-pro" in pitches_js
assert "pitch-chip-con" in pitches_js

# The tool-tabs tray must never force itself open for an active tool tab -
# it used to eat the whole screen above the content on mobile.
assert 'tool-tabs" ${activeTool' not in panel_js, (
    "the tool-tabs <details> must not be conditionally forced open based on "
    "the active tab"
)

# Planning images for backup options ride on the existing gallery machinery
# through synthetic "option:<id>" keys - the active place is a normal stop
# and already gets internet images before the visit and personal OneDrive
# photos afterwards ("wie immer").
helpers_py = (COMPONENT / "experience_helpers.py").read_text(encoding="utf-8")
assert 'OPTION_STOP_PREFIX = "option:"' in helpers_py
assert "_find_pitch_option" in helpers_py
assert "_pitchOptionCover" in pitches_js
assert '"pitch-option-images"' in panel_js
assert "_searchPitchOptionImages" in pitches_js
assert "_deletePitchOptionGallery" in pitches_js, (
    "deleting or activating an option must clean up its option-keyed gallery"
)

print("Pitch feature wiring contract tests passed.")
