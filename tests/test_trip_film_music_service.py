"""The music service, where the money is actually spent.

The plan module is arithmetic and easy to be sure about. This is the
part that can be wrong in expensive ways: an offer that quotes a price
the generate call does not honour, a cache key that never matches so a
second render pays a second time, or a film whose length wobbles by two
seconds and buys a whole new soundtrack.

So this drives the real service against a fake session that counts how
often it was asked to generate. Nothing reaches a network, and the
counter is the assertion that matters.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import importlib.machinery
import importlib.util
import re
from pathlib import Path
import sys
import tempfile
import types
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True


def _module(name: str, **attributes) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules.setdefault(name, module)
    return module


_module("homeassistant").__path__ = []
_module("homeassistant.core", HomeAssistant=object, callback=lambda fn: fn)
_module("homeassistant.exceptions", HomeAssistantError=type("E", (Exception,), {}))
_module("homeassistant.helpers").__path__ = []
_module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: None)
_module("homeassistant.helpers.storage", Store=object)
_module(
    "aiohttp",
    ClientError=type("ClientError", (Exception,), {}),
    ClientSession=object,
    ClientTimeout=lambda *a, **k: None,
)

_PACKAGE = "roadplanner_music_service_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(ROOT / "custom_components" / "roadplanner_mcp")]
sys.modules[_PACKAGE] = _root

service_module = importlib.import_module(f"{_PACKAGE}.trip_film_music_service")
# Read from the real client rather than repeated here: the number this
# test checks against is exactly the one that was wrong before.
lyria_module = importlib.import_module(f"{_PACKAGE}.trip_film_lyria")

MANIFEST = {
    "trip": {"title": "Ostsee-Runde 2026"},
    "narrative": {
        "arc": "23 Tage und eine große Runde um die Ostsee",
        "opening": "Seen und Lagerfeuer",
        "closing": "Tierische Begegnungen",
    },
}


class _Hass:
    async def async_add_executor_job(self, target, *args):
        return target(*args)


class _StoryContext:
    async def async_manifest(self, trip_id):
        assert trip_id, "die Reise-ID muss ankommen"
        return MANIFEST


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self._payload

    async def text(self):
        return ""


class _Session:
    """Counts every request that would have cost eight cents.

    A token exchange is not a generation and must not be counted as one -
    otherwise the cheapest possible mistake, minting a token per call,
    would look exactly like the most expensive one.
    """

    def __init__(self):
        self.calls = 0
        self.token_calls = 0
        self.get_calls = 0
        self.prompts: list[str] = []
        self.urls: list[str] = []

    def get(self, url, *, headers=None, params=None, timeout=None):
        """Reading the model list. Never billed, so never counted."""
        self.urls.append(str(url))
        self.get_calls += 1
        return _Response(
            {
                "models": [
                    {"name": "models/gemini-2.5-flash"},
                    {"name": "models/lyria-3-pro-preview"},
                ]
            }
        )

    def post(self, url, *, json=None, data=None, headers=None, timeout=None):
        self.urls.append(str(url))
        if data is not None:
            self.token_calls += 1
            return _Response({"access_token": "ya29.fake", "expires_in": 3600})
        self.calls += 1
        self.prompts.append(str(json))
        blob = base64.b64encode(b"ID3fake-audio-bytes").decode("ascii")
        # The shape Vertex actually answers `:predict` with. The fake used
        # to return the Gemini `candidates` shape, which is what the code
        # used to read - so both sides agreed with each other and neither
        # agreed with the API.
        return _Response(
            {"predictions": [{"bytesBase64Encoded": blob, "mimeType": "audio/mpeg"}]}
        )


class _Tokens:
    """Stands in for ServiceAccountTokens, with its real interface.

    Deliberately no field the production object does not have: a fake
    that carries an extra name is how this project has answered "off" for
    every configuration five separate times.
    """

    def __init__(self):
        self._token = ""

    def cached(self, *, now=None):
        return self._token

    def request(self, *, now=None):
        return ("https://oauth2.googleapis.com/token", {"grant_type": "x", "assertion": "y"})

    def store(self, payload, *, now=None):
        self._token = str(payload.get("access_token") or "")
        if not self._token:
            raise service_module.ServiceAccountError("kein Token")
        return self._token


def _service(root, session, *, api_key="k", vertex=True):
    # ONE holder for the whole service, because that is what the
    # integration builds: `_vertex_provider` keeps the ServiceAccount
    # tokens keyed by the credential and hands the same object out on
    # every read. A fake that minted a fresh holder per call would have
    # hidden a token exchange before every single generation.
    holder = _Tokens()
    return service_module.TripFilmMusicService(
        _Hass(),
        _StoryContext(),
        lambda: session,
        api_key_provider=lambda: api_key,
        music_root=root,
        vertex_provider=(
            (lambda: {"project": "reise-film-2026", "region": "us-central1", "tokens": holder})
            if vertex
            else (lambda: None)
        ),
    )



# The regional Vertex host, whole. `<region>-aiplatform.googleapis.com`
# is one hyphenated token, so this is a full match rather than a suffix
# test - and a full match is also the only form code scanning accepts as
# evidence about a destination, for the good reason that a suffix on an
# unparsed string proves nothing.
_VERTEX_HOST = re.compile(r"[a-z0-9-]+-aiplatform\.googleapis\.com\Z")
_STUDIO_HOST = "generativelanguage.googleapis.com"


def _host(url: str) -> str:
    """Where a request actually goes.

    Parsed rather than searched for: `"aiplatform" in url` is true of a
    URL pointing anywhere at all that merely mentions the name in a path
    or a query, so it is no evidence about the destination.
    """
    return urlsplit(url).hostname or ""


def _is_vertex(url: str) -> bool:
    return bool(_VERTEX_HOST.fullmatch(_host(url)))


FILM = 7 * 60 + 12.0


def verify_the_offer_costs_nothing_and_orders_nothing() -> None:
    """The only thing the panel may do before somebody agrees to a price."""
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        offer = asyncio.run(_service(root, session).async_offer("t1", film_seconds=FILM))
        assert session.calls == 0, "ein Angebot darf nichts erzeugen"
        # The COUNT is not written down here. It used to be, as a 4, and
        # that 4 came from a track length of 118 s that the API never
        # had - so the test agreed with the bug instead of catching it.
        # What matters is the property: enough sections to cover the
        # film, each one short enough for a single generation, and a
        # price that is exactly one charge per section.
        sections = offer["plan"]["sections"]
        assert offer["sections"] == len(sections), offer
        assert offer["new_generations"] == len(sections)
        assert abs(offer["estimated_cost"] - len(sections) * 0.08) < 0.001, offer
        assert sections[0]["start_seconds"] == 0.0, sections[0]
        # Against the length the plan was made FOR, not the raw film: the
        # planned length is rounded up to half-minutes on purpose, so a
        # re-render that shifts by a second does not buy a second
        # soundtrack. The film's own length is imposed later, by the mux,
        # which trims to what ffprobe measured.
        planned = offer["plan"]["film_seconds"]
        assert planned >= FILM, (planned, FILM)
        assert abs(sections[-1]["end_seconds"] - planned) < 0.01, sections[-1]
        for entry in sections:
            assert entry["seconds"] <= lyria_module.LYRIA_TRACK_SECONDS, (
                f"{entry['label']} fordert {entry['seconds']} s Musik, eine "
                f"Generierung liefert höchstens {lyria_module.LYRIA_TRACK_SECONDS} s "
                "- der Rest des Abschnitts wäre still"
            )
        assert offer["available"] is True
        assert not any(Path(root).iterdir()), "ein Angebot schreibt nichts"


def verify_generating_pays_once_and_a_second_render_is_free() -> None:
    """The whole reason the cache is the music folder."""
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        service = _service(root, session)
        first = asyncio.run(service.async_generate("t1", film_seconds=FILM))
        wanted = len(first["sections"])
        assert first["generated"] == wanted, first
        assert session.calls == wanted
        assert len(first["timeline"]) == wanted
        assert [entry["name"] for entry in first["timeline"]] == [
            entry["cached_name"] for entry in first["sections"]
        ]

        again = asyncio.run(service.async_generate("t1", film_seconds=FILM))
        assert again["generated"] == 0, again
        assert again["reused"] == wanted
        assert session.calls == wanted, "ein zweiter Lauf darf nichts kosten"

        offer = asyncio.run(service.async_offer("t1", film_seconds=FILM))
        assert offer["new_generations"] == 0
        assert offer["estimated_cost"] == 0.0
        assert offer["reused"] is True


def verify_a_film_that_lost_a_photo_does_not_buy_a_new_soundtrack() -> None:
    """The estimate wobbles by seconds. The plan must not.

    An unfetchable picture shortens its day. If that moved the plan, the
    next render would be four fresh generations at full price for a film
    nobody would hear a difference in.
    """
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        service = _service(root, session)
        first = asyncio.run(service.async_generate("t1", film_seconds=FILM))
        assert session.calls == len(first["sections"])
        for drift in (-6.0, -2.0, +3.0, +11.0):
            offer = asyncio.run(
                service.async_offer("t1", film_seconds=FILM + drift)
            )
            assert offer["new_generations"] == 0, (drift, offer)
        # A film a full minute longer is a different film, and it is
        # allowed to need different music.
        longer = asyncio.run(service.async_offer("t1", film_seconds=FILM + 90))
        assert longer["new_generations"] > 0, longer


def verify_the_music_is_never_shorter_than_the_film() -> None:
    """Rounded up, so the last seconds are not silent."""
    for seconds in (61.0, 200.0, 431.0, 7 * 60 + 12.0):
        planned = service_module.quantized_seconds(seconds)
        assert planned >= seconds, (seconds, planned)
        assert planned - seconds < service_module.LENGTH_QUANTUM_SECONDS
    assert service_module.quantized_seconds(0) == 0.0


def verify_without_a_key_nothing_is_promised() -> None:
    """The offer still works - it just says the button will not.

    And it is asked of the credential the generation actually uses. It
    used to read the Gemini API key, which Lyria refuses: the dialog
    reported "verfügbar", somebody agreed to a price, and only then did
    the call fail.
    """
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        # NEITHER route configured. Either one alone is enough, so this
        # has to remove both to mean "no access".
        service = _service(root, session, api_key="", vertex=False)
        offer = asyncio.run(service.async_offer("t1", film_seconds=FILM))
        assert offer["available"] is False
        # Not just "no": what would fix it, and that there are two ways.
        reason = offer["unavailable_reason"]
        assert "AI Studio" in reason and "Vertex" in reason, offer
        try:
            asyncio.run(service.async_generate("t1", film_seconds=FILM))
        except Exception as err:  # noqa: BLE001 - the message is the assertion
            assert "Zugang" in str(err), err
        else:  # pragma: no cover - a failure path that must not vanish
            raise AssertionError("ohne Schlüssel darf nicht erzeugt werden")
        assert session.calls == 0


def verify_either_route_is_enough_on_its_own() -> None:
    """Two ways in, because it is not settled which one Lyria answers on.

    Google's own accounts disagree - one says the Gemini Developer API
    does not serve Lyria at all, another that Lyria 3 is reachable from
    AI Studio's Interactions endpoint with an ordinary key. Rather than
    pick one from whichever sentence was read most recently, both are
    built: a configured project goes to Vertex, otherwise the key goes
    to AI Studio. The last guess shipped an endpoint that could not work
    AND a test that defended it.
    """
    with tempfile.TemporaryDirectory() as root:
        # Key only: AI Studio, no project needed.
        session = _Session()
        offer = asyncio.run(
            _service(root, session, vertex=False).async_offer("t1", film_seconds=FILM)
        )
        assert offer["available"] is True, offer
        asyncio.run(
            _service(root, session, vertex=False).async_generate("t1", film_seconds=FILM)
        )
        assert session.calls > 0
        assert session.token_calls == 0, "AI Studio braucht kein Token"
        assert all(_host(url) == _STUDIO_HOST for url in session.urls), (
            session.urls
        )

    with tempfile.TemporaryDirectory() as root:
        # Project configured: Vertex wins, and a token is minted once.
        session = _Session()
        asyncio.run(_service(root, session).async_generate("t1", film_seconds=FILM))
        assert session.token_calls == 1, (
            f"{session.token_calls} Token für einen Soundtrack - eines reicht"
        )
        assert any(_is_vertex(url) for url in session.urls), (
            session.urls
        )


def verify_the_model_probe_costs_nothing() -> None:
    """The system check may ask what is reachable. It may not buy music.

    This exists because the endpoint question was genuinely open - one
    account said the Gemini Developer API does not serve Lyria at all,
    another that AI Studio reaches it with an ordinary key. Asking the
    installation settles it, but only if asking is free: a check that
    quietly spends eight cents is one nobody runs twice.
    """
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        found = asyncio.run(_service(root, session).async_probe_models())
        assert found["state"] == "ok", found
        assert "lyria-3-pro-preview" in found["models"], found
        assert session.calls == 0, "eine Sonde darf nichts erzeugen"
        assert session.token_calls == 0
        assert session.get_calls == 1
        assert not any(Path(root).iterdir()), "eine Sonde schreibt nichts"


def verify_the_probe_says_what_to_do_when_lyria_is_absent() -> None:
    """"Nicht verfügbar" alone would send somebody checking the wrong thing."""

    class _NoLyria(_Session):
        def get(self, url, *, headers=None, params=None, timeout=None):
            self.get_calls += 1
            return _Response({"models": [{"name": "models/gemini-2.5-flash"}]})

    with tempfile.TemporaryDirectory() as root:
        found = asyncio.run(_service(root, _NoLyria()).async_probe_models())
        assert found["state"] == "warn", found
        # The next step, named: this is the whole reason to ask.
        assert "Dienstkonto" in found["detail"], found
        assert "Vertex" in found["detail"], found
        # And it says what is NOT affected, so nobody reads it as "the
        # film is broken".
        assert "nicht betroffen" in found["detail"], found


def verify_without_a_key_the_probe_is_skipped_not_failed() -> None:
    """Nothing configured is not a fault."""
    with tempfile.TemporaryDirectory() as root:
        found = asyncio.run(
            _service(root, _Session(), api_key="", vertex=False).async_probe_models()
        )
        assert found["state"] == "skipped", found


def verify_a_film_without_length_orders_nothing() -> None:
    """No length is not "one track by default"; it is nothing."""
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        service = _service(root, session)
        offer = asyncio.run(service.async_offer("t1", film_seconds=0))
        assert offer["sections"] == 0, offer
        assert offer["estimated_cost"] == 0.0
        result = asyncio.run(service.async_generate("t1", film_seconds=0))
        assert result["generated"] == 0
        assert result["timeline"] == []
        assert session.calls == 0


# --- the architecture comparison ---------------------------------------

plan_module = importlib.import_module(f"{_PACKAGE}.trip_film_plan")
music_package = importlib.import_module(f"{_PACKAGE}.trip_film_music")
arch_module = importlib.import_module(f"{_PACKAGE}.music_architecture")


def _scene(kind, seconds, chapter):
    return {
        "type": kind,
        "frames": int(round(seconds * plan_module.FILM_FPS)),
        "chapter_id": chapter,
    }


def _scene_plan():
    """A film long enough that the excerpt chooser has a choice."""
    scenes = [_scene(plan_module.SCENE_INTRO, 6, "intro")]
    for day in range(1, 9):
        chapter = f"day-{day}"
        scenes.extend(
            [
                _scene(plan_module.SCENE_CHAPTER_CARD, 3, chapter),
                _scene(plan_module.SCENE_MAP_LEG, 9, chapter),
                _scene(plan_module.SCENE_HERO, 6, chapter),
                _scene(plan_module.SCENE_COLLAGE, 7, chapter),
                _scene(plan_module.SCENE_TEXT, 5, chapter),
            ]
        )
    scenes.append(_scene(plan_module.SCENE_OUTRO, 7, "outro"))
    return {"fps": plan_module.FILM_FPS, "scenes": scenes}


def verify_the_comparison_costs_three_requests_and_then_nothing() -> None:
    """Three fassungen, three purchases, and a restart pays nothing.

    The cache IS the music folder, so "survives a restart" is not a
    hopeful phrase here - a second service object is built over the same
    directory and the request counter must not move. A cache that only
    lived in the first object would have been invisible in every test
    that reused one.
    """
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        plan = _scene_plan()

        offer = asyncio.run(_service(root, session).async_prototype_offer("t1", scene_plan=plan))
        assert offer["generation_count"] == 3, offer["assets"]
        assert session.calls == 0, "ein Angebot darf nichts kosten"

        made = asyncio.run(
            _service(root, session).async_prototype_generate("t1", scene_plan=plan)
        )
        assert made["generated"] == 3, made
        assert session.calls == 3, session.calls
        assert all(variant["ready"] for variant in made["variants"]), made["variants"]

        # A different service object over the same folder: a restart.
        again = asyncio.run(
            _service(root, session).async_prototype_generate("t1", scene_plan=plan)
        )
        assert again["generated"] == 0, again
        assert again["reused"] == 3
        assert session.calls == 3, "ein zweiter Lauf hat noch einmal bezahlt"


def verify_the_bed_is_one_file_on_disk() -> None:
    """B and C must name the same file, not two files that sound alike.

    Checked on the folder rather than on the plan: the plan promising one
    bed while the folder holds two would be the same comparison broken
    in a place no arithmetic test looks.
    """
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        plan = _scene_plan()
        made = asyncio.run(
            _service(root, session).async_prototype_generate("t1", scene_plan=plan)
        )
        beds = {
            layer["cached_name"]
            for variant in made["variants"]
            for layer in variant["layers"]
            if layer["role"] == arch_module.ROLE_BED
        }
        assert len(beds) == 1, beds
        assert len(list(Path(root).glob("*.mp3"))) == 3, sorted(Path(root).iterdir())


def verify_each_fassung_mixes_exactly_its_own_layers() -> None:
    """§31: A only the score, B bed plus accent, C only the bed.

    And the levels: summing a bed and an accent at one volume is not a
    layered mix, it is two tracks at once - which is the thing the
    comparison would then fail to be about.
    """
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        plan = _scene_plan()
        service = _service(root, session)
        asyncio.run(service.async_prototype_generate("t1", scene_plan=plan))

        packages = {}
        for name in arch_module.VARIANTS:
            found = asyncio.run(service.async_prototype_variant("t1", name, scene_plan=plan))
            assert found, name
            package, files = music_package.build_music_variant_package(
                found,
                root,
                target_lufs=found["target_lufs"],
                true_peak_dbtp=found["true_peak_ceiling_dbtp"],
            )
            assert len(files) == len(package["sections"])
            packages[name] = package

        roles = {
            name: [entry["role"] for entry in package["sections"]]
            for name, package in packages.items()
        }
        assert roles["A"] == [arch_module.ROLE_SCORE], roles
        assert roles["B"] == [arch_module.ROLE_BED, arch_module.ROLE_ACCENT], roles
        assert roles["C"] == [arch_module.ROLE_BED], roles

        levels = {entry["role"]: entry["volume"] for entry in packages["B"]["sections"]}
        assert levels[arch_module.ROLE_BED] < levels[arch_module.ROLE_ACCENT] / 2, levels
        # Alone the bed comes up, but not to where a background layer has
        # quietly become a lead track.
        alone = packages["C"]["sections"][0]["volume"]
        assert levels[arch_module.ROLE_BED] < alone < levels[arch_module.ROLE_ACCENT], alone

        # The loudness target travels with every fassung, or the
        # comparison is decided by whichever one is louder.
        for name, package in packages.items():
            assert package["target_lufs"] == arch_module.TARGET_LUFS, name
            assert package["variant"] == name
            for entry in package["sections"]:
                assert entry["fade_in_seconds"] > 0 and entry["fade_out_seconds"] > 0


def verify_a_fassung_missing_a_layer_is_refused_not_shortened() -> None:
    """The layered mix without its bed IS the single-score mix.

    Silently dropping the layer would have produced two fassungen that
    are the same audio, compared against each other, and reported as a
    finding about architecture.
    """
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        plan = _scene_plan()
        service = _service(root, session)
        made = asyncio.run(service.async_prototype_generate("t1", scene_plan=plan))
        layered = next(v for v in made["variants"] if v["variant"] == arch_module.VARIANT_B)
        bed = next(
            layer for layer in layered["layers"] if layer["role"] == arch_module.ROLE_BED
        )
        (Path(root) / bed["cached_name"]).unlink()

        # The read-only side says nothing rather than something shorter.
        assert (
            asyncio.run(
                service.async_prototype_variant("t1", arch_module.VARIANT_B, scene_plan=plan)
            )
            is None
        )
        # And the package builder refuses even if it is handed one.
        try:
            music_package.build_music_variant_package(layered, root)
        except music_package.MusicPackageError as err:
            assert "Ebene" in str(err), err
        else:
            raise AssertionError("eine fehlende Ebene haette abgelehnt werden muessen")


def verify_the_prototype_never_orders_a_whole_soundtrack() -> None:
    """§17, asked of what was actually REQUESTED.

    Every request asks for around the excerpt's own length. A prototype
    that quietly ordered twelve minutes would still count as three
    generations, so counting requests alone cannot see this.
    """
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        plan = _scene_plan()
        made = asyncio.run(
            _service(root, session).async_prototype_generate("t1", scene_plan=plan)
        )
        assert made["window_seconds"] <= 90.5, made["window_seconds"]
        for asset in made["assets"]:
            assert asset["requested_seconds"] <= made["window_seconds"] * 1.2 + 0.1, asset


# Every verify_ in the file, found rather than listed. The list used to
# be written out by hand and four checks had fallen off it - including
# the two that ask whether a probe costs money. A test nobody runs is
# indistinguishable from one that passes.
for _name, _check in sorted(dict(globals()).items()):
    if _name.startswith("verify_") and callable(_check):
        _check()

print("Trip film music service tests passed.")
