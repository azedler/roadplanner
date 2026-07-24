"""Contract tests for geodata-first destination intelligence."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

MODULE_PATH = Path("custom_components/roadplanner_mcp/destination_intelligence.py")
spec = spec_from_file_location("roadplanner_destination_intelligence_test", MODULE_PATH)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


@dataclass
class StructuredAddress:
    street: str = ""
    house_number: str = ""
    postal_code: str = ""
    city: str = ""
    district: str = ""
    state: str = ""
    country: str = ""
    country_code: str = ""
    name: str = ""


ferry = module.analyze_destination(
    {"title": "Pärnu, Tallinn & Fähre nach Helsinki"},
    {
        "name": "Fährterminal Tallinn",
        "type": "ferry",
        "notes": "Tallink-Abfahrt um 19:30 Uhr",
    },
    structured_address=StructuredAddress(),
)
assert ferry.kind == "ferry_terminal"
assert ferry.locality == "Tallinn"
assert ferry.strategy == "typed_poi"
assert 1 <= len(ferry.query_variants) <= 3
assert ferry.query_variants[0] == "Fährterminal Tallinn"
assert any("ferry terminal" in value.casefold() for value in ferry.query_variants)
assert all(len(value) <= 240 for value in ferry.query_variants)
assert not any("19:30" in value for value in ferry.query_variants)
assert not any("Pärnu" in value for value in ferry.query_variants)

hike = module.analyze_destination(
    {},
    {
        "name": "Haukkankierros-Wanderung",
        "type": "attraction",
        "notes": "Ca. 4 km lange Wanderrunde über Holzstege.",
    },
    structured_address=StructuredAddress(city="Espoo", country_code="FI"),
)
assert hike.kind == "hike"
assert hike.label == "Wanderung"
assert any("hiking trail" in value.casefold() for value in hike.query_variants)

# The explicit destination name must correct a broad/wrong imported category.
nature_center = module.analyze_destination(
    {},
    {
        "name": "Finnisches Naturzentrum Haltia",
        "type": "restaurant",
        "location": {"city": "Espoo", "country_code": "FI"},
    },
    structured_address=StructuredAddress(city="Espoo", country_code="FI"),
)
assert nature_center.kind == "nature_center"
assert nature_center.label == "Natur- oder Besucherzentrum"
assert any("nature center" in value.casefold() for value in nature_center.query_variants)

wolfsschanze = module.analyze_destination(
    {},
    {"name": "Wolfsschanze (Wilczy Szaniec)", "type": "Stadtbesichtigung"},
    structured_address=StructuredAddress(city="Gierłoż", country_code="PL"),
)
assert wolfsschanze.kind == "attraction"
assert wolfsschanze.label == "Sehenswürdigkeit"

decathlon = module.analyze_destination(
    {},
    {"name": "Decathlon Kaunas", "type": "Einkauf"},
    structured_address=StructuredAddress(city="Kaunas", country_code="LT"),
)
assert decathlon.kind == "retail"

park4night = module.analyze_destination(
    {},
    {
        "name": "Stellplatz am See",
        "type": "wildcamp",
        "notes": "Details: https://park4night.com/lieu/448383/",
    },
    structured_address=StructuredAddress(),
)
assert park4night.kind == "camping"
assert park4night.strategy == "source_hint_then_typed_poi"
assert park4night.source_hints == (
    {
        "provider": "park4night",
        "url": "https://park4night.com/lieu/448383/",
        "id": "448383",
    },
)

address = module.analyze_destination(
    {},
    {"name": "Krumhermsdorf Neuhäuser 40"},
    structured_address=StructuredAddress(
        street="Neuhäuser",
        house_number="40",
        postal_code="01844",
        city="Neustadt in Sachsen",
        district="Krumhermsdorf",
        country_code="DE",
    ),
)
assert address.kind == "address"
assert address.strategy == "structured_address"
assert "Neuhäuser 40" in address.primary_query
assert "01844" in address.primary_query
assert "Neustadt in Sachsen" in address.primary_query

very_long_notes = "Interne Notiz " * 100
candidate = {
    "name": "Tallinn Terminal D",
    "category": "Fährterminal",
    "location": {
        "city": "Tallinn",
        "country_code": "EE",
        "latitude": 59.44327,
        "longitude": 24.76154,
    },
}
image_query = module.destination_image_query(
    {"title": "Dieser Tagestitel darf nicht in die Bildsuche"},
    {
        "name": "Fährterminal Tallinn",
        "notes": very_long_notes,
        "location": {},
    },
    intent=ferry,
    candidate=candidate,
)
assert image_query.startswith("Tallinn Terminal D")
assert "Tallinn" in image_query
assert "Estland" in image_query
assert "Interne Notiz" not in image_query
assert "Tagestitel" not in image_query
assert len(image_query) <= 180

print("Destination intelligence tests passed.")
