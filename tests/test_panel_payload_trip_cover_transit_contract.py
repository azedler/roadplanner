"""Source-level contract: the trip-level cover photo must never fall back to a
photo automatically linked to a pure transit/logistics stop (departure from
home, a fuel stop, a border crossing).

Regression guard for a real user report: with no personal photo explicitly
marked or Vision-curated as the trip cover, the panel fell back to the first
day's first stop with a confirmed destination-gallery image in iteration
order - which, for a road trip, is reliably a stop near home rather than
anything representative of the actual destination. The behavioral rules that
make this exclusion correct (transit-stop photos never entering the
automatic trip-cover candidate pool) are exercised directly against
media_intelligence.py in test_media_intelligence.py; this test only pins down
that panel_payload_builder.py actually computes and threads the exclusion set
through both fallback paths (Vision-eligible candidates and the raw
destination-gallery planning fallback).
"""
from pathlib import Path

SOURCE = Path("custom_components/roadplanner_mcp/panel_payload_builder.py").read_text(encoding="utf-8")

assert "from .media_intelligence import TRANSIT_ONLY_STOP_TYPES, build_media_presentation" in SOURCE

assert (
    "transit_stop_ids = frozenset(\n"
    "            str(stop.get(\"id\") or \"\")\n"
    "            for day in days or []\n"
    "            for stop in _stops(day)\n"
    "            if str(stop.get(\"type\") or \"\").casefold() in TRANSIT_ONLY_STOP_TYPES\n"
    "        )"
) in SOURCE, "the exclusion set must be derived from every day's stops before curation runs"

assert "transit_stop_ids=transit_stop_ids" in SOURCE, (
    "the personal-photo curation path must also honor the exclusion, in case "
    "a Vision pass later requests a transit-linked photo as the trip cover"
)

assert (
    'and str(stop.get("id") or "") not in transit_stop_ids\n'
    "                ):\n"
    '                    if profile.get("confirmed_at") or profile.get("verified"):\n'
    "                        planning_trip_cover = deepcopy(primary)"
) in SOURCE, "the raw destination-gallery planning fallback must skip transit-only stops too"

# transit_stop_ids must be computed before build_media_presentation is called,
# not after - the prior bug definitionally depended on `days` (and thus the
# stop types) not yet being available at that point.
transit_ids_pos = SOURCE.index("transit_stop_ids = frozenset(")
build_presentation_pos = SOURCE.index("presentation = build_media_presentation(")
assert transit_ids_pos < build_presentation_pos

print("Panel payload trip-cover transit-exclusion contract tests passed.")
