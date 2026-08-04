"""Behavioral + contract tests for trip PDF export orchestration.

The ticket lifecycle (create/resolve/expire/limited reuse) is pure asyncio
with no Home Assistant or network dependency, so it's exercised directly
against the real TripPdfExporter methods. The data-gathering/photo-fetch
path (async_generate) needs aiohttp/Home Assistant, so that part is only
covered by source-level contract checks below.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_trip_pdf_export_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
aiohttp_stub.ClientTimeout = lambda *args, **kwargs: None
sys.modules["aiohttp"] = aiohttp_stub

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object
sys.modules["homeassistant.core"] = ha_core
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


for module_name in ("bounded_json", "json_io", "identifiers", "json_tree_validation"):
    try:
        load(module_name)
    except FileNotFoundError:
        pass

load("canonical_day")
load("trip_pdf")
export_module = load("trip_pdf_export")


def _exporter():
    return export_module.TripPdfExporter(hass=None, manager=None, experience=None)


def verify_ticket_round_trip() -> None:
    async def scenario() -> None:
        exporter = _exporter()
        token = await exporter.async_create_ticket(b"%PDF-1234", user_id="user-1")
        resolved = await exporter.async_resolve_ticket(token)
        assert resolved == b"%PDF-1234"

    asyncio.run(scenario())


def verify_ticket_is_single_use_limited() -> None:
    async def scenario() -> None:
        exporter = _exporter()
        token = await exporter.async_create_ticket(b"%PDF-abc", user_id="user-1")
        for _ in range(export_module._MAX_TICKET_USES):
            await exporter.async_resolve_ticket(token)
        try:
            await exporter.async_resolve_ticket(token)
        except export_module.ValidationError:
            return
        raise AssertionError("expected the exhausted ticket to be rejected")

    asyncio.run(scenario())


def verify_unknown_token_is_rejected() -> None:
    async def scenario() -> None:
        exporter = _exporter()
        try:
            await exporter.async_resolve_ticket("does-not-exist")
        except export_module.ValidationError:
            return
        raise AssertionError("expected an unknown token to be rejected")

    asyncio.run(scenario())


def verify_expired_ticket_is_purged() -> None:
    async def scenario() -> None:
        exporter = _exporter()
        token = await exporter.async_create_ticket(b"%PDF-old", user_id="user-1")
        exporter._tickets[token].expires_monotonic = 0.0
        try:
            await exporter.async_resolve_ticket(token)
        except export_module.ValidationError:
            return
        raise AssertionError("expected an expired ticket to be purged and rejected")

    asyncio.run(scenario())


verify_ticket_round_trip()
verify_ticket_is_single_use_limited()
verify_unknown_token_is_rejected()
verify_expired_ticket_is_purged()


class _FakeContentReader:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def read(self, _max_bytes: int) -> bytes:
        return self._body


class _FakeResponse:
    def __init__(self, *, status: int = 200, body: bytes = b"", content_length: int | None = None) -> None:
        self.status = status
        self.content_length = content_length if content_length is not None else len(body)
        self.content = _FakeContentReader(body)

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


# The exporters now refuse an image no renderer can open, so a fixture
# must carry a real JPEG header.
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 12


class _FakeSession:
    def __init__(self, body_by_url: dict[str, bytes]) -> None:
        self._body_by_url = body_by_url
        self.requested_urls: list[str] = []

    def get(self, url: str, **_kwargs: object) -> _FakeResponse:
        self.requested_urls.append(url)
        body = self._body_by_url.get(url)
        if body is None:
            return _FakeResponse(status=404)
        return _FakeResponse(status=200, body=body)


class _FakeExperience:
    def __init__(self, redirect_url: str | None) -> None:
        self._redirect_url = redirect_url

    async def async_media_redirect_url(
        self, trip_id: str, media_id: str, kind: str, *, size: str = "large"
    ) -> str:
        # Rendered JPEG previews are requested before the (possibly HEIC)
        # original - see trip_export_photos.async_fetch_media_photo.
        if self._redirect_url is None:
            raise export_module.RoadplannerError("kein Foto gefunden")
        return self._redirect_url

    async def async_panel_payload(self, trip_id: str, *, days=None) -> dict:
        return {"destination_galleries": {}, "media": []}


class _FakeManager:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def async_get_assistant_payload(self, trip_id: str) -> dict:
        return self._payload


class _FakeHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


def verify_async_generate_reads_trip_from_the_real_nested_payload_shape() -> None:
    """Regression guard: the assistant payload has no top-level "trip" key.

    The real shape (confirmed against manager.py's _assistant_payload_sync)
    is {"summary": {"trip": {...}, ...}, "days": {...}, "selected_trip_id": ...}
    - there is no bare payload["trip"]. Reading the wrong key silently produced
    an empty trip dict, so the cover page fell back to a generic title and
    the crew/vehicle page never rendered at all.
    """
    async def scenario() -> None:
        payload = {
            "selected_trip_id": "trip-1",
            "summary": {
                "trip": {
                    "title": "Finnland / Baltikum 2026",
                    "start_date": "2026-07-17",
                    "end_date": "2026-08-08",
                    "travelers": [{"name": "Aron", "kind": "person", "note": "Fahrer"}],
                    "vehicle": {"name": "Nugget", "description": "Der Camper"},
                },
                "total_distance_km": 5550,
            },
            "days": {"days": []},
        }
        exporter = export_module.TripPdfExporter(
            hass=_FakeHass(),
            manager=_FakeManager(payload),
            experience=_FakeExperience(None),
        )
        captured = {}

        def fake_build_trip_pdf(data):
            captured["data"] = data
            return b"%PDF-fake"

        original_build = export_module.build_trip_pdf
        export_module.build_trip_pdf = fake_build_trip_pdf
        try:
            pdf_bytes = await exporter.async_generate("trip-1")
        finally:
            export_module.build_trip_pdf = original_build
        assert pdf_bytes == b"%PDF-fake"
        data = captured["data"]
        assert data.title == "Finnland / Baltikum 2026"
        assert data.start_date == "2026-07-17"
        assert len(data.crew) == 1 and data.crew[0].name == "Aron"
        assert data.vehicle is not None and data.vehicle.name == "Nugget"
        assert data.total_distance_km == 5550

    asyncio.run(scenario())


def verify_personal_photo_is_preferred_over_stock() -> None:
    async def scenario() -> None:
        exporter = export_module.TripPdfExporter(
            hass=None, manager=None, experience=_FakeExperience("https://graph.example/personal.jpg")
        )
        session = _FakeSession({"https://graph.example/personal.jpg": _JPEG + b"personal-bytes"})
        media_by_stop = {"stop-1": [{"id": "media-1", "is_cover": True}]}
        destination_galleries = {
            "stop-1": {
                "primary_image_id": "img-1",
                "images": [
                    {"id": "img-1", "provider": "wikimedia_commons", "image_url": "https://commons.example/stock.jpg"}
                ],
            }
        }
        photos = await exporter._async_fetch_day_photos(
            session, "trip-1", [{"id": "stop-1"}], media_by_stop, destination_galleries
        )
        assert photos == [_JPEG + b"personal-bytes"]
        assert "https://commons.example/stock.jpg" not in session.requested_urls

    asyncio.run(scenario())


def verify_stock_photo_is_the_fallback_when_no_personal_photo_exists() -> None:
    async def scenario() -> None:
        exporter = export_module.TripPdfExporter(
            hass=None, manager=None, experience=_FakeExperience(None)
        )
        session = _FakeSession({"https://commons.example/stock.jpg": _JPEG + b"stock-bytes"})
        destination_galleries = {
            "stop-1": {
                "primary_image_id": "img-1",
                "images": [
                    {"id": "img-1", "provider": "wikimedia_commons", "image_url": "https://commons.example/stock.jpg"}
                ],
            }
        }
        photos = await exporter._async_fetch_day_photos(
            session, "trip-1", [{"id": "stop-1"}], {}, destination_galleries
        )
        assert photos == [_JPEG + b"stock-bytes"]

    asyncio.run(scenario())


def verify_google_places_stock_photo_is_never_fetched() -> None:
    async def scenario() -> None:
        exporter = export_module.TripPdfExporter(
            hass=None, manager=None, experience=_FakeExperience(None)
        )
        session = _FakeSession({})
        destination_galleries = {
            "stop-1": {
                "primary_image_id": "img-1",
                "images": [{"id": "img-1", "provider": "google_places", "image_url": "https://places.example/x.jpg"}],
            }
        }
        photos = await exporter._async_fetch_day_photos(
            session, "trip-1", [{"id": "stop-1"}], {}, destination_galleries
        )
        assert photos == []
        assert not session.requested_urls

    asyncio.run(scenario())


verify_personal_photo_is_preferred_over_stock()
verify_stock_photo_is_the_fallback_when_no_personal_photo_exists()
verify_google_places_stock_photo_is_never_fetched()
verify_async_generate_reads_trip_from_the_real_nested_payload_shape()

# Source-level contract checks for the parts that need real aiohttp/Home
# Assistant network access to exercise behaviorally. The photo-fetch logic
# itself now lives in the shared trip_export_photos.py (used by both the
# PDF and video exporters), not in trip_pdf_export.py.
SOURCE = (PACKAGE_ROOT / "trip_export_photos.py").read_text(encoding="utf-8")
assert 'casefold() == "google_places"' in SOURCE, (
    "Google Places photos resolve to an internal, session-authenticated "
    "redirect - a server-side export job must not try to fetch them directly"
)
assert 'startswith("https://")' in SOURCE, (
    "only a plain, directly fetchable HTTPS image URL may be downloaded"
)
assert "MAX_PHOTO_BYTES" in SOURCE and "content_length" in SOURCE, (
    "a downloaded photo must be bounded in size"
)

# --- Personal crew cards and day highlight bullets (live request:
# "Bilder der Leute ... bissel persönliches" / "kleine Beschreibung des
# Tages ... als Stichpunkte") ---

MEDIA = [
    {"id": "m1", "caption": "Levi angelt am Saimaa-See", "media_type": "photo",
     "assignment_status": "manual", "linked_stop_id": "s1", "width": 4000, "height": 3000},
    {"id": "m2", "caption": "Levi und Peer auf der Düne", "media_type": "photo",
     "assignment_status": "automatic", "linked_stop_id": "s2", "width": 2000, "height": 1500},
    {"id": "m3", "caption": "Abendessen ohne Namen", "media_type": "photo",
     "assignment_status": "manual", "linked_stop_id": "s1"},
    {"id": "m4", "caption": "Levittata Brot backen", "media_type": "photo",
     "assignment_status": "manual", "linked_stop_id": "s1"},
]


def verify_media_mentioning_matches_whole_names_ranked() -> None:
    matches = export_module._media_mentioning(MEDIA, "Levi")
    ids = [item["id"] for item in matches]
    assert set(ids) == {"m1", "m2"}, (
        f"{ids}: 'Levittata' must NOT match the name 'Levi' (word boundary)"
    )
    assert export_module._media_mentioning(MEDIA, "L") == [], "1-char names never match"
    assert export_module._media_mentioning(MEDIA, "Nugget") == []


def verify_day_highlights_are_deterministic() -> None:
    stops = [
        {"id": "s1", "name": "Wanderdünen Łeba", "type": "activity"},
        {"id": "s2", "name": "Tanken", "type": "fuel"},
        {"id": "s3", "name": "Aussicht Söderåsen", "type": "viewpoint"},
    ]
    media_by_stop = {"s1": [{"id": "a"}, {"id": "b"}], "s3": [{"id": "c"}]}
    highlights = export_module._day_highlights(stops, media_by_stop)
    assert highlights == ["Wanderdünen Łeba", "Aussicht Söderåsen", "3 eigene Fotos"], highlights
    # Photos linked only to the DAY count too (live cause of "keine Fotos").
    with_day_media = export_module._day_highlights(
        stops, media_by_stop, [{"id": "c"}, {"id": "d"}]
    )
    assert with_day_media[-1] == "4 eigene Fotos", with_day_media
    # Without highlight-typed stops the overnight place is the keyword.
    overnight_only = [{"id": "s9", "name": "Lumsenkojan", "type": "wildcamp"}]
    assert export_module._day_highlights(overnight_only, {}) == ["Lumsenkojan"]
    assert export_module._day_highlights([], {}) == []


verify_media_mentioning_matches_whole_names_ranked()
verify_day_highlights_are_deterministic()

def verify_one_line_kills_the_notdef_boxes() -> None:
    # Live report: "Besitzer von Notbert[]Mag Natur" - an embedded newline
    # is drawn as a black .notdef box by reportlab.
    assert export_module._one_line("Besitzer von Norbert\nMag Natur, Sauna") == (
        "Besitzer von Norbert · Mag Natur, Sauna"
    )
    assert export_module._one_line("Lagotto\r\nSpitznamen: Nobbi\nBellt gern") == (
        "Lagotto · Spitznamen: Nobbi · Bellt gern"
    )
    assert export_module._one_line("  schon\tsauber  ") == "schon · sauber"
    assert export_module._one_line(None) == ""


verify_one_line_kills_the_notdef_boxes()

EXPORT_SOURCE = (PACKAGE_ROOT / "trip_pdf_export.py").read_text(encoding="utf-8")
assert "route_map=await self._async_route_map(" in EXPORT_SOURCE, (
    "the PDF route page must show the REAL route, not only a schematic"
)
assert "fit_center_zoom(" in EXPORT_SOURCE
PDF_SOURCE = (PACKAGE_ROOT / "trip_pdf.py").read_text(encoding="utf-8")
assert "real_map = _decode_photo(data.route_map)" in PDF_SOURCE
assert "Die tatsächlich gefahrene Route." in PDF_SOURCE
assert "member.photo = await async_fetch_media_photo" in EXPORT_SOURCE, (
    "crew members get their portrait from a caption-matched personal photo"
)
assert "_async_person_summary" in EXPORT_SOURCE
# "Wer ist wer" without captions: the reference photo assigned in the crew
# settings wins as portrait, and Vision recognizes the person on the day
# photos when no captions exist.
assert "reference_by_name" in EXPORT_SOURCE
assert "portrait_item = reference_item or (mentions[0] if mentions else None)" in EXPORT_SOURCE
assert "_async_person_vision_summary" in EXPORT_SOURCE
assert '"reference_media_id"' in (PACKAGE_ROOT / "crew_store.py").read_text(encoding="utf-8")
CREW_UI_SOURCE = (PACKAGE_ROOT / "frontend/features/crew.js").read_text(encoding="utf-8")
assert "crew-pick-reference" in CREW_UI_SOURCE
assert 'name="reference_media_id"' in CREW_UI_SOURCE
PANEL_UI_SOURCE = (PACKAGE_ROOT / "frontend/roadplanner-panel.js").read_text(encoding="utf-8")
assert "reference_media_id: cleanText(values.reference_media_id)" in PANEL_UI_SOURCE

print("Trip PDF export tests passed.")
