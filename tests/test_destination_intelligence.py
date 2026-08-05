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

# Common user spellings of a Park4Night place ID must become the same source
# hint even without a full URL.
for spelled_id in (
    "P4N 448383",
    "p4n#448383",
    "P4N-448383",
    "Park4Night 448383",
    "Park4Night: 448383",
    "Park4Night-ID 448383",
    "park 4 night Nr. 448383",
):
    spelled = module.analyze_destination(
        {},
        {"name": "Stellplatz am See", "notes": spelled_id},
        structured_address=StructuredAddress(),
    )
    assert spelled.kind == "camping", spelled_id
    assert spelled.strategy == "source_hint_then_typed_poi", spelled_id
    assert {
        (hint["provider"], hint.get("id")) for hint in spelled.source_hints
    } == {("park4night", "448383")}, spelled_id

# A Park4Night ID inside the stop name is recognized, removed from the
# resolved destination name and kept out of every provider query.
named_id = module.analyze_destination(
    {},
    {"name": "Stellplatz am See (Park4Night 448383)", "type": "wildcamp"},
    structured_address=StructuredAddress(),
)
assert named_id.name == "Stellplatz am See"
assert named_id.source_hints[0]["id"] == "448383"
assert all("448383" not in value for value in named_id.query_variants)

# The deterministic Park4Night identity outranks an AI text classification.
p4n_vs_ai = module.analyze_destination(
    {},
    {"name": "Stellplatz am See", "notes": "p4n 448383"},
    structured_address=StructuredAddress(),
    cleanup_suggestion={"place_kind": "restaurant"},
)
assert p4n_vs_ai.kind == "camping"
assert p4n_vs_ai.reason == "park4night_link"

# Provider hosts must match the exact domain or a real subdomain. A malicious
# suffix must remain a generic link and must not be trusted as OSM/Google.
malicious = module.analyze_destination(
    {},
    {
        "name": "Untrusted link",
        "notes": (
            "https://openstreetmap.org.evil.example/node/123 "
            "https://maps.google.com.evil.example/place/test"
        ),
    },
    structured_address=StructuredAddress(),
)
assert [hint["provider"] for hint in malicious.source_hints] == ["link", "link"]

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

# Real bug: two businesses can share one street address (a retail park). A
# stop with its own specific name must search by that name first, not by the
# bare shared address - an address-only search resolves ambiguously to
# whichever business a provider associates most strongly with that address,
# which silently picked the wrong (but nearby) one even though the stop's
# own name would have found the right business unambiguously.
named_at_shared_address = module.analyze_destination(
    {},
    {"name": "Minimani Rovaniemi", "type": "shopping"},
    structured_address=StructuredAddress(
        street="Teollisuustie",
        house_number="2",
        postal_code="96320",
        city="Rovaniemi",
        country_code="FI",
    ),
)
assert named_at_shared_address.kind != "address"
assert named_at_shared_address.primary_query.startswith("Minimani Rovaniemi")

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

# A stop with a personal name AND a full street address: the name goes
# first (a shop in a retail park is found by its name, not by the address
# several tenants share), but the address must not be thrown away. Live
# report: a stop named "Heimatort" carrying "Neuhäuser 40, 01844 Neustadt
# in Sachsen" resolved to the town hall at Markt 1, because street and
# house number never reached any provider.
home = module.analyze_destination(
    {"title": "Trelleborg -> Rostock (TT-Line Fähre)"},
    {"name": "Heimatort", "type": "", "notes": ""},
    structured_address=StructuredAddress(
        street="Neuhäuser",
        house_number="40",
        postal_code="01844",
        city="Neustadt in Sachsen",
        district="Krumhermsdorf",
        country="Deutschland",
        country_code="DE",
    ),
)
# The name-first classification is unchanged - that part was deliberate.
assert home.kind == "place", home.kind
# But the address is now a query of its own, and it is the LAST resort.
assert home.address_query, "the street address must become a query"
assert "Neuhäuser" in home.address_query and "40" in home.address_query
assert home.query_variants[0] != home.address_query, "the name still goes first"
assert home.address_query in home.query_variants, "and the address must survive the cap"
# Only the address variant may be sent as a STRUCTURED lookup - that is
# what separates "Neuhäuser 40" from "the town of Neustadt".
assert home.uses_structured_address(home.address_query)
assert not home.uses_structured_address(home.query_variants[0])

# A stop without any street address gains nothing and must stay untouched.
assert not ferry.address_query
assert not ferry.uses_structured_address("Fährterminal Tallinn")

# A pure address stop keeps its existing structured path.
pure = module.analyze_destination(
    {"title": "Anreise"},
    {"name": "Neuhäuser 40", "type": "", "notes": ""},
    structured_address=StructuredAddress(
        street="Neuhäuser", house_number="40", city="Neustadt in Sachsen",
        country="Deutschland", country_code="DE",
    ),
)
assert pure.kind == "address", pure.kind
assert pure.uses_structured_address(pure.primary_query)

print("Destination intelligence tests passed.")
