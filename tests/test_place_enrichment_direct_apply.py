"""Source-level contract: confirmed place enrichments apply directly.

Live report: "Auch anreichern ändert nicht die Koordinaten." Root cause -
the enrichment submit only PARKED a review handoff pinned to the trip
revision at submit time; background writes (automatic route refresh,
photo assignment, ...) bump the revision within seconds, so the parked
handoff turned into a stale-revision conflict and the confirmed
coordinates never landed. Since every operation was already individually
confirmed by the user in the preview dialog, the ChangeSet is now applied
directly after a clean ingest; only a genuine race falls back to the
review handoff, with the reason surfaced to the user.

Second live gap: with the AI provider unavailable, the Park4Night page
read failed SILENTLY - the adoption button just never appeared and
nothing said why. A failed read of a recognized p4n link now carries an
explanation into the dialog.
"""
from pathlib import Path

ORCHESTRATOR = Path(
    "custom_components/roadplanner_mcp/place_enrichment_orchestrator.py"
).read_text(encoding="utf-8")
ENRICHMENT = Path(
    "custom_components/roadplanner_mcp/place_enrichment.py"
).read_text(encoding="utf-8")
FRONTEND = Path(
    "custom_components/roadplanner_mcp/frontend/features/place-enrichment.js"
).read_text(encoding="utf-8")

# --- direct apply after clean ingest ------------------------------------
assert "await self.manager.async_apply_handoff(" in ORCHESTRATOR
apply_block = ORCHESTRATOR.split("applied_handoff: dict[str, Any] | None = None")[1]
assert "if clean_ingest:" in apply_block, "apply only after a clean ingest"
assert "expected_revision=revision," in apply_block, (
    "the apply must pin the same revision the changeset was built against"
)
assert "handoff stays" in apply_block, "a failed direct apply falls back to review"
assert '"applied": applied_handoff is not None' in ORCHESTRATOR
assert '"apply_error": apply_error' in ORCHESTRATOR

# The frontend distinguishes both outcomes instead of always sending the
# user to Übergaben for a second confirmation round.
assert "übernommen und angewendet" in FRONTEND
assert "dort bitte anwenden" in FRONTEND
assert "result.applied" in FRONTEND

# --- p4n read failures are surfaced, not swallowed ----------------------
assert ENRICHMENT.count('"p4n_lookup_error": p4n_error') == 3, (
    "every preview payload shape carries the p4n error"
)
assert "tuple[dict[str, Any] | None, str | None]" in ENRICHMENT
assert "nicht konfiguriert" in ENRICHMENT
assert "konnte gerade nicht gelesen werden" in ENRICHMENT
# No-p4n-link stays silent - the warning only appears for a RECOGNIZED link.
facts_fn = ENRICHMENT.split("async def _p4n_page_facts")[1].split("async def ")[0]
assert "return None, None" in facts_fn

assert "Park4Night-Link erkannt, Seite nicht lesbar" in FRONTEND
assert "p4n_lookup_error" in FRONTEND

print("Place enrichment direct-apply contract tests passed.")
