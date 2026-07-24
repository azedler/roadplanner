"""Source contract for robust destination-gallery refreshes."""
from pathlib import Path

source = Path(
    "custom_components/roadplanner_mcp/experience_manager.py"
).read_text(encoding="utf-8")

for required in (
    "from .destination_intelligence import analyze_destination, destination_image_query",
    "return destination_image_query(day, stop, intent=intent)",
    "matches: list[tuple[dict[str, Any], dict[str, Any]]]",
    "if len(matches) == 1:",
    "Der ausgewählte Stopp ist mehreren Tagen zugeordnet",
):
    if required not in source:
        raise AssertionError(f"Missing destination refresh contract: {required}")

legacy_query_fragments = (
    'str(stop.get("notes") or "")[:300]',
    'day.get("title"),\n        ]',
)
for forbidden in legacy_query_fragments:
    if forbidden in source:
        raise AssertionError(
            "Destination image refresh still includes notes/day-title noise"
        )

print("Destination refresh contract tests passed.")
