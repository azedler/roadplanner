"""Regression test: strip more soft constraints from Gemini's response schema.

Real bug, confirmed via a live HA debug log: Gemini rejected both the
schema_search and schema_no_search request shapes with a generic HTTP 400
("Request contains an invalid argument.", no further detail) across several
current models (gemini-3.6-flash, gemini-3.5-flash, gemini-3.5-flash-lite),
forcing every compile call all the way back to the unconstrained
json_mime_only mode - which can never include the google_search/url_context
tools, so a booking-link stop update could never actually get fetched.

Google's own guidance for this exact generic 400 says schema "complexity"
(long property lists combined with numeric/length constraints on many
fields) can trigger it even for an otherwise-valid schema, with no specific
violation reported back. GeminiClient._supported_schema() already stripped
maxLength/minLength/pattern for this reason; this extends it to the other
soft numeric-range keywords our compile/basket schemas also use
(minimum/maximum on distance_km, drive_minutes, position; minItems/maxItems
on every array). None of these are needed for correctness - every value is
still fully re-validated server-side regardless of what schema Gemini used
during generation.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_gemini_schema_complexity_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

# gemini_client.py imports aiohttp and homeassistant helpers at module level;
# only their names need to exist for a static-method-only import to work.
aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = Exception
aiohttp_stub.ClientResponse = object
aiohttp_stub.ClientSession = object
sys.modules["aiohttp"] = aiohttp_stub

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers.__path__ = []
sys.modules["homeassistant.helpers"] = ha_helpers
ha_aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
ha_aiohttp_client.async_get_clientsession = lambda *a, **k: None
sys.modules["homeassistant.helpers.aiohttp_client"] = ha_aiohttp_client


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


for module_name in (
    "bounded_json",
    "json_io",
    "identifiers",
    "json_tree_validation",
    "assistant_provider",
    "structured_output",
):
    load(module_name)

const_module = types.ModuleType(f"{PACKAGE_NAME}.const")
const_module.INTEGRATION_VERSION = "test"
sys.modules[f"{PACKAGE_NAME}.const"] = const_module

gemini_client = load("gemini_client")

SOFT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "maxLength": 500, "minLength": 1, "pattern": "^.+$"},
        "distance_km": {"type": "number", "minimum": 0, "maximum": 100000},
        "items": {"type": "array", "minItems": 0, "maxItems": 30, "items": {"type": "string"}},
    },
    "additionalProperties": False,
}


def verify_all_soft_constraint_keywords_are_stripped() -> None:
    result = gemini_client.GeminiClient._supported_schema(SOFT_SCHEMA)
    assert result["properties"]["name"] == {"type": "string"}
    assert result["properties"]["distance_km"] == {"type": "number"}
    assert result["properties"]["items"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    # Structural keywords needed for correctness must survive untouched.
    assert result["type"] == "object"
    assert result["additionalProperties"] is False


verify_all_soft_constraint_keywords_are_stripped()

print("Gemini schema complexity reduction tests passed.")
