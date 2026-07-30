"""Behavioral tests for the automatic post-edit route refresh.

Live request: "Nach einer Änderung eines Stopps an einem Tag sollten wir
die Routenberechnung des Tages automatisch neustarten" - relevant only when
route-affecting data changed (GPS, order, transport), which the existing
per-day route input hash already decides deterministically. The new
RouteRefreshScheduler debounces a burst of edits into one refresh; the
refresh runs trip-wide with force=False so the hash skips every unchanged
day (and still covers a neighbouring day whose start moved via overnight
continuity).
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_auto_route_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
aiohttp_stub.ClientSession = object
aiohttp_stub.ClientTimeout = lambda *args, **kwargs: None
sys.modules.setdefault("aiohttp", aiohttp_stub)
homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers.__path__ = []
sys.modules.setdefault("homeassistant.helpers", ha_helpers)
ha_aiohttp = types.ModuleType("homeassistant.helpers.aiohttp_client")
ha_aiohttp.async_get_clientsession = lambda *a, **k: None
sys.modules.setdefault("homeassistant.helpers.aiohttp_client", ha_aiohttp)


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


routing = load("routing")


class FakeTimer:
    def __init__(self, callback):
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeLoop:
    def __init__(self):
        self.timers = []

    def call_later(self, _delay, callback):
        timer = FakeTimer(callback)
        self.timers.append(timer)
        return timer

    def fire_pending(self):
        for timer in list(self.timers):
            if not timer.cancelled:
                timer.cancelled = True
                timer.callback()


def _scheduler(loop, runs):
    async def refresh():
        runs.append(True)

    started = []

    def create_task(coro):
        started.append(coro)
        coro.close()
        runs.append("started")

    return routing.RouteRefreshScheduler(
        delay=5.0,
        call_later=loop.call_later,
        create_task=create_task,
        refresh=refresh,
    )


def verify_burst_of_edits_yields_one_refresh() -> None:
    loop = FakeLoop()
    runs: list = []
    scheduler = _scheduler(loop, runs)
    scheduler.schedule()
    scheduler.schedule()
    scheduler.schedule()
    live = [t for t in loop.timers if not t.cancelled]
    assert len(live) == 1, (
        f"three quick edits must leave exactly ONE armed timer, got {len(live)}"
    )
    loop.fire_pending()
    assert runs.count("started") == 1, "exactly one refresh task starts"


def verify_cancel_disarms_the_timer() -> None:
    loop = FakeLoop()
    runs: list = []
    scheduler = _scheduler(loop, runs)
    scheduler.schedule()
    scheduler.cancel()
    loop.fire_pending()
    assert runs == [], "a cancelled scheduler must not start a refresh"


def verify_schedule_after_fire_rearms() -> None:
    loop = FakeLoop()
    runs: list = []
    scheduler = _scheduler(loop, runs)
    scheduler.schedule()
    loop.fire_pending()
    scheduler.schedule()
    loop.fire_pending()
    assert runs.count("started") == 2, "the scheduler is reusable after firing"


def verify_manager_and_panel_wiring() -> None:
    manager_source = (PACKAGE_ROOT / "manager.py").read_text(encoding="utf-8")
    assert "def schedule_route_refresh(self)" in manager_source
    assert "if not self.router.configured:" in manager_source, (
        "the auto refresh must be a no-op without configured routing"
    )
    assert 'actor="roadplanner_auto_route"' in manager_source
    assert "expected_revision=None," in manager_source, (
        "the automatic refresh asserts no user intent about the revision"
    )
    panel_source = (PACKAGE_ROOT / "panel.py").read_text(encoding="utf-8")
    count = panel_source.count("manager.schedule_route_refresh()")
    assert count == 5, (
        "add_stop, remove_stop, activate_option and apply_handoff always "
        "schedule; update_stop conditionally - expected 5 call sites, got "
        f"{count}"
    )
    assert '("location", "details", "type")' in panel_source, (
        "a notes-/time-only stop update must NOT schedule a refresh"
    )


if __name__ == "__main__":
    verify_burst_of_edits_yields_one_refresh()
    verify_cancel_disarms_the_timer()
    verify_schedule_after_fire_rearms()
    verify_manager_and_panel_wiring()
    print("Automatic route refresh tests passed.")
