"""Source-level contract for the handoff rebase-and-retry feature."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "roadplanner_mcp"
HANDOFF = (ROOT / "handoff.py").read_text(encoding="utf-8")
MANAGER = (ROOT / "manager.py").read_text(encoding="utf-8")
PANEL = (ROOT / "panel.py").read_text(encoding="utf-8")
PANEL_JS = (ROOT / "frontend" / "roadplanner-panel.js").read_text(encoding="utf-8")

assert "def rebase_pending" in HANDOFF
# A rebase must always keep the envelope's and the nested changeset's
# base_revision in agreement - _validate_envelope enforces this on every read.
assert '"base_revision": base_revision,' in HANDOFF
assert 'changeset["base_revision"] = base_revision' in HANDOFF

assert "async def async_rebase_handoff" in MANAGER
# The rebased changeset must be re-validated against the current trip before
# it's ever persisted - never a blind re-stamp.
assert "self.store.preview_changeset, rebased_changeset" in MANAGER
assert 'preview.get("status") != "ready"' in MANAGER

assert '"rebase_handoff"' in PANEL
assert "manager.async_rebase_handoff(" in PANEL

assert 'data-action="rebase-handoff"' in PANEL_JS
assert 'action === "rebase-handoff"' in PANEL_JS

print("Handoff rebase contract tests passed.")
