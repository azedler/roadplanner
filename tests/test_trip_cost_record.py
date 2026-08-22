"""The trip's cost record: its categories, and the rate it is closed with.

Two behaviours are pinned here.

**Maut is a category.** The request that started this ("wieviel für tanken,
Essen, Restaurant/Imbiss, Maut, Fähre") named a category the archive did
not have - tolls landed under "Sonstiges" and disappeared from the split.

**A finished trip's rate is frozen.** The EUR total of a closed journey is
a record, not a live quote. It used to be recomputed from the current ECB
publication on every view, so the same trip showed a different total from
one week to the next. The freeze is a one-way door: once written, a later
freeze must not move it.
"""
from __future__ import annotations

from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys
import tempfile
import types

sys.dont_write_bytecode = True

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_cost_record_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
package.__spec__ = spec_from_file_location(
    PACKAGE_NAME, PACKAGE_ROOT / "__init__.py", submodule_search_locations=[str(PACKAGE_ROOT)]
)
sys.modules[PACKAGE_NAME] = package

aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
aiohttp_stub.ClientSession = object
aiohttp_stub.ClientTimeout = lambda *args, **kwargs: None
sys.modules.setdefault("aiohttp", aiohttp_stub)
homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object
sys.modules.setdefault("homeassistant.core", ha_core)
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers.__path__ = []
sys.modules.setdefault("homeassistant.helpers", ha_helpers)
ha_aiohttp = types.ModuleType("homeassistant.helpers.aiohttp_client")
ha_aiohttp.async_get_clientsession = lambda *a, **k: None
sys.modules.setdefault("homeassistant.helpers.aiohttp_client", ha_aiohttp)


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


archive = load("travel_archive")
currency = load("currency_rates")

ECB = {"base": "EUR", "date": "2026-08-20", "rates": {"SEK": 11.3, "PLN": 4.25}}


def store(root: Path):
    return archive.TravelArchiveStore(root)


def verify_maut_is_its_own_category() -> None:
    for raw in ["toll", "Maut", "maut", "MAUT", "Vignette", "road_toll", "tolls"]:
        assert archive.normalize_expense_category(raw) == "toll", raw
    assert "toll" in archive.EXPENSE_CATEGORIES


def verify_the_existing_categories_still_land_where_they_did() -> None:
    assert archive.normalize_expense_category("camping") == "campsite"
    assert archive.normalize_expense_category("imbiss") == "snack"
    assert archive.normalize_expense_category("transportmittel") == "transport"
    assert archive.normalize_expense_category("was auch immer") == "other"


def verify_a_snapshot_without_a_rate_is_no_snapshot() -> None:
    assert archive.normalize_rate_snapshot({"date": "2026-08-20", "rates": {}}) == {}
    assert archive.normalize_rate_snapshot({"date": "", "rates": {"SEK": 11.3}}) == {}
    assert archive.normalize_rate_snapshot(None) == {}
    assert archive.normalize_rate_snapshot({"date": "2026-08-20", "rates": {"SEK": -1}}) == {}
    kept = archive.normalize_rate_snapshot({"date": "2026-08-20", "rates": {"sek": 11.3, "XX": 2, "PLN": True}})
    assert kept["rates"] == {"SEK": 11.3}, kept
    assert kept["frozen_at"], "a snapshot always says when it was taken"


def verify_freezing_happens_once_and_then_holds() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        book = store(Path(tmp))
        first = book.freeze_rate_snapshot(trip_id="trip-a", rates=ECB, reason="trip_status:completed")
        assert first["date"] == "2026-08-20"
        later = book.freeze_rate_snapshot(
            trip_id="trip-a",
            rates={"base": "EUR", "date": "2026-12-01", "rates": {"SEK": 9.9}},
            reason="end_date:2026-08-19",
        )
        assert later["date"] == "2026-08-20", "a frozen rate must not move"
        assert later["rates"] == {"SEK": 11.3, "PLN": 4.25}
        assert book.rate_snapshot("trip-a")["date"] == "2026-08-20"


def verify_each_trip_freezes_on_its_own() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        book = store(Path(tmp))
        book.freeze_rate_snapshot(trip_id="trip-a", rates=ECB, reason="trip_status:completed")
        assert book.rate_snapshot("trip-b") == {}, "one trip's freeze says nothing about another"


def verify_a_frozen_snapshot_survives_a_reload() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store(Path(tmp)).freeze_rate_snapshot(trip_id="trip-a", rates=ECB, reason="trip_status:completed")
        again = store(Path(tmp))
        assert again.rate_snapshot("trip-a")["rates"] == {"SEK": 11.3, "PLN": 4.25}
        written = json.loads((Path(tmp) / "trips" / "trip-a" / "archive.json").read_text(encoding="utf-8"))
        assert written["rate_snapshot"]["reason"] == "trip_status:completed"


def verify_nothing_freezes_on_a_rate_that_never_arrived() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        book = store(Path(tmp))
        assert book.freeze_rate_snapshot(trip_id="trip-a", rates={}, reason="trip_status:completed") == {}
        assert book.rate_snapshot("trip-a") == {}, "an empty freeze must not close the record"


def verify_the_panel_sees_the_label_but_not_the_rate_table() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        book = store(Path(tmp))
        book.create_expense(
            trip_id="trip-a",
            value={"merchant": "Tankstelle", "amount": 80.5, "currency": "EUR", "category": "Maut"},
            actor="test",
            default_currency="EUR",
        )
        book.freeze_rate_snapshot(trip_id="trip-a", rates=ECB, reason="trip_status:completed")
        stats = book.panel_payload("trip-a")["stats"]
        assert stats["category_totals"] == {"toll": {"EUR": 80.5}}, stats["category_totals"]
        assert stats["rate_snapshot"] == {"date": "2026-08-20", "frozen_at": stats["rate_snapshot"]["frozen_at"]}
        assert "rates" not in stats["rate_snapshot"], "the panel asks the action for the numbers"


def verify_a_cancelled_expense_is_in_no_category() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        book = store(Path(tmp))
        book.create_expense(
            trip_id="trip-a",
            value={"merchant": "Faehre", "amount": 200, "currency": "EUR", "category": "ferry", "status": "cancelled"},
            actor="test",
            default_currency="EUR",
        )
        book.create_expense(
            trip_id="trip-a",
            value={"merchant": "Tanken", "amount": 50, "currency": "EUR", "category": "fuel"},
            actor="test",
            default_currency="EUR",
        )
        stats = book.panel_payload("trip-a")["stats"]
        assert stats["category_totals"] == {"fuel": {"EUR": 50.0}}, stats["category_totals"]


def verify_a_trip_is_over_when_it_says_so_or_when_its_last_day_has_passed() -> None:
    today = date(2026, 8, 22)
    assert currency.trip_finished_reason({"status": "completed"}, today) == "trip_status:completed"
    assert currency.trip_finished_reason({"status": "archived"}, today) == "trip_status:archived"
    assert currency.trip_finished_reason({"end_date": "2026-08-21"}, today) == "end_date:2026-08-21"
    assert currency.trip_finished_reason({"status": "planning", "end_date": "2026-08-22"}, today) == "", (
        "the last day still belongs to the trip"
    )
    assert currency.trip_finished_reason({"end_date": "2026-09-30"}, today) == ""
    assert currency.trip_finished_reason({"end_date": ""}, today) == ""
    assert currency.trip_finished_reason({"end_date": "irgendwann"}, today) == ""
    assert currency.trip_finished_reason({}, today) == ""
    assert currency.trip_finished_reason(None, today) == ""


CHECKS = [
    verify_maut_is_its_own_category,
    verify_the_existing_categories_still_land_where_they_did,
    verify_a_snapshot_without_a_rate_is_no_snapshot,
    verify_freezing_happens_once_and_then_holds,
    verify_each_trip_freezes_on_its_own,
    verify_a_frozen_snapshot_survives_a_reload,
    verify_nothing_freezes_on_a_rate_that_never_arrived,
    verify_the_panel_sees_the_label_but_not_the_rate_table,
    verify_a_cancelled_expense_is_in_no_category,
    verify_a_trip_is_over_when_it_says_so_or_when_its_last_day_has_passed,
]


def verify_every_check_in_this_module_is_registered() -> None:
    """A check that is written but never called covers nothing."""
    declared = {
        name
        for name, value in globals().items()
        if name.startswith("verify_") and callable(value) and name != "verify_every_check_in_this_module_is_registered"
    }
    registered = {check.__name__ for check in CHECKS}
    assert declared == registered, f"not run: {sorted(declared - registered)}"


if __name__ == "__main__":
    verify_every_check_in_this_module_is_registered()
    for check in CHECKS:
        check()
        print(f"ok - {check.__name__}")
    print(f"\n{len(CHECKS)} checks passed")
