"""Ordering music from Lyria, deliberately and at most once per brief.

Kept apart from the film exporter on purpose, and the separation is the
feature rather than tidiness. The exporter must have no way to reach a
paid call: not a fallback, not a "generate if missing", not a helpful
retry. It reads a folder of audio files. This service is the only thing
in Roadplanner that can put a new file in that folder, and it runs only
when somebody asked for it after being shown a price.

That the cache *is* the music folder is the other half of it. A generated
track lands beside whatever the user put there themselves, under a name
derived from the trip's brief - so re-rendering the same film finds it
the same way it finds any other track, through the ordinary "pick a
name" path. There is no second cache to keep in step with the first, and
no code anywhere that has to remember to look in two places.

Failure is always the same shape: no music, and a film that renders.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

from .roadplanner import ValidationError
from .music_cue_sheet import CueSheetError, build_cue_sheet
from .music_plan_director import (
    MAX_SECTIONS as DIRECTOR_MAX_SECTIONS,
    PlanDirectorError,
    build_brief,
    validate_proposal,
)
from .trip_film_music_plan import (
    CROSSFADE_SECONDS,
    FADE_IN_SECONDS,
    FADE_OUT_SECONDS,
    build_plan,
    section_prompt,
    cost_notice as plan_cost_notice,
    section_cache_key,
)
from .google_service_account import ServiceAccountError
from .trip_film_lyria import (
    GEMINI_MODELS_ENDPOINT,
    LYRIA_REGION,
    lyria_models_in,
    LYRIA_ESTIMATED_COST_USD,
    LYRIA_MODEL,
    LYRIA_PRICE_NOTE,
    LYRIA_TIMEOUT_SECONDS,
    LYRIA_TRACK_SECONDS,
    LyriaError,
    audio_from_response,
    build_request,
    extension_for,
    track_filename,
)
from .trip_film_music import DEFAULT_MUSIC_ROOT, MAX_TRACK_BYTES

_LOGGER = logging.getLogger(__name__)

# The film's length is an estimate: a photo that cannot be fetched
# shortens its day by a second or two. If the plan reacted to that, a
# re-render with one missing picture would be a second full soundtrack
# at full price. So the length is rounded UP to whole half-minutes
# before anything is planned - up, so the music is never shorter than
# the film, and coarse enough that ordinary drift changes nothing.
LENGTH_QUANTUM_SECONDS = 30.0

# How loud the music sits under the pictures. Named here rather than in
# the renderer so the timeline carries it: after this, the mux places
# what it is told and decides nothing. Conservative on purpose - there
# is no speech to duck under yet, and a soundtrack that has to be turned
# down is worse than one that could have been louder.
DEFAULT_MUSIC_VOLUME = 0.42


def quantized_seconds(film_seconds: float) -> float:
    """The length the soundtrack is planned for."""
    total = max(0.0, float(film_seconds or 0.0))
    if total <= 0:
        return 0.0
    return math.ceil(total / LENGTH_QUANTUM_SECONDS) * LENGTH_QUANTUM_SECONDS


class TripFilmMusicService:
    """Generate one soundtrack for one trip, and only when asked."""

    def __init__(
        self,
        hass: Any,
        story_context: Any,
        session_factory: Any,
        *,
        api_key_provider: Any = None,
        music_root: str | Path = DEFAULT_MUSIC_ROOT,
        vertex_provider: Any = None,
        plan_director: Any = None,
    ) -> None:
        self._hass = hass
        self._story_context = story_context
        self._session_factory = session_factory
        # The AI Studio route: the key this integration already has,
        # against the Interactions endpoint.
        self._api_key_provider = api_key_provider
        # The Vertex route: a project, a region and a service account.
        # Both are callables rather than values, so a credential added
        # after startup takes effect without rebuilding the service.
        self._vertex_provider = vertex_provider
        # Optional by design: without it the arithmetic plan stands, and
        # a film still gets music. A planner that could stop a render is
        # not worth having.
        self._plan_director = plan_director
        self._root = Path(music_root)

    def _route(self) -> dict[str, Any]:
        """Which way this generation goes, and whether it can go at all.

        Two routes exist because Google's own accounts of where Lyria
        lives disagree, and the documentation that would settle it is not
        reachable from the environment this was built in. A configured
        Vertex project wins; otherwise the AI Studio key does. Naming
        both beats guessing one - the last guess shipped an endpoint that
        could not work and a test that defended it.
        """
        found = self._vertex_provider() if callable(self._vertex_provider) else None
        if isinstance(found, dict) and str(found.get("project") or "").strip():
            if not found.get("tokens"):
                raise ValidationError(
                    "Für Vertex AI fehlt der Dienstkonto-Schlüssel. Ohne Projekt-ID "
                    "läuft die Musik über den Gemini-Schlüssel aus AI Studio."
                )
            return {"kind": "vertex", **found}
        key = str(self._api_key_provider() or "") if callable(self._api_key_provider) else ""
        if key:
            return {"kind": "gemini", "api_key": key}
        raise ValidationError(
            "Für die Musikgenerierung fehlt ein Zugang: entweder ein "
            "Google-API-Schlüssel aus AI Studio oder ein Vertex-Projekt mit "
            "Dienstkonto."
        )

    def _vertex_state(self) -> tuple[bool, str]:
        """Can music be generated, and if not, which part is missing?

        The same question `_route` answers by raising, in the form an
        offer needs: a read-only dialog must not fail just because
        nothing is configured yet.
        """
        try:
            self._route()
        except ValidationError as err:
            return False, str(err)
        return True, ""

    async def async_probe_models(self) -> dict[str, Any]:
        """Which Lyria models this installation can actually reach.

        Read-only and free: it lists the models the configured key may
        use. Deliberately NOT a small generation - a system check that
        quietly spends eight cents is one nobody runs twice, and the
        question here is reachability, not sound.

        It exists because the answer has been genuinely unclear. One
        account says the Gemini Developer API does not serve Lyria at
        all; another that Lyria 3 is reachable from AI Studio with an
        ordinary key. This asks the installation instead of arguing.
        """
        key = str(self._api_key_provider() or "") if callable(self._api_key_provider) else ""
        if not key:
            return {"state": "skipped", "detail": "kein Google-Schlüssel konfiguriert"}
        session = self._session_factory()
        try:
            async with session.get(
                GEMINI_MODELS_ENDPOINT,
                headers={"x-goog-api-key": key},
                params={"pageSize": "200"},
            ) as response:
                if response.status >= 400:
                    detail = (await response.text())[:160]
                    return {
                        "state": "fail",
                        "detail": f"Modell-Liste nicht lesbar ({response.status}): {detail}",
                    }
                payload = await response.json(content_type=None)
        except Exception as err:  # noqa: BLE001 - a probe is never an exception
            _LOGGER.debug("Lyria-Sonde fehlgeschlagen: %s", type(err).__name__)
            return {"state": "fail", "detail": f"nicht erreichbar: {type(err).__name__}"}
        found = lyria_models_in(payload)
        if found:
            return {
                "state": "ok",
                "models": found,
                "detail": (
                    "über AI Studio erreichbar: "
                    + ", ".join(found[:3])
                    + " - kein Google-Cloud-Projekt nötig"
                ),
            }
        return {
            "state": "warn",
            "models": [],
            "detail": (
                "Dieser Schlüssel sieht kein Lyria-Modell. Für KI-Musik braucht es "
                "dann ein Google-Cloud-Projekt mit Vertex AI und ein Dienstkonto "
                "(Rolle „Vertex AI User\u201c). Alles andere am Film ist davon "
                "nicht betroffen."
            ),
        }

    async def _async_access_token(self, tokens: Any) -> str:
        """A bearer token, minted only when the cached one has gone stale."""
        cached = tokens.cached()
        if cached:
            return cached
        url, form = tokens.request()
        session = self._session_factory()
        try:
            async with session.post(url, data=form, timeout=LYRIA_TIMEOUT_SECONDS) as response:
                payload = await response.json(content_type=None)
        except Exception as err:  # noqa: BLE001 - transport failures read the same
            _LOGGER.warning("Vertex-Token nicht erhalten: %s", type(err).__name__)
            raise ValidationError(
                "Google hat kein Zugriffstoken ausgestellt. Der Film läuft ohne Musik."
            ) from err
        try:
            return tokens.store(payload)
        except ServiceAccountError as err:
            # The reason Google gave is the difference between "it does
            # not work" and "the Vertex AI API is not enabled for this
            # project", so it is carried through rather than flattened.
            raise ValidationError(str(err)) from err

    async def _async_plan(
        self,
        trip_id: str,
        film_seconds: float,
        *,
        scene_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The soundtrack this film would get, without ordering any of it.

        With a scene plan it can do better than arithmetic: a cue sheet
        says where the journey turns, and a model may put the boundaries
        there. Without one - or when the model's answer breaks a rule -
        the deterministic plan stands. Both produce the same SHAPE, so
        nothing downstream has to know which one it got.
        """
        trip_id = str(trip_id or "").strip()
        if not trip_id:
            raise ValidationError("Für die Musik fehlt die Reise-ID")
        manifest = await self._story_context.async_manifest(trip_id)
        chapters = manifest.get("chapters") or []
        plan = build_plan(
            trip=manifest.get("trip") or {},
            narrative=manifest.get("narrative") or {},
            film_seconds=quantized_seconds(film_seconds),
            track_seconds=LYRIA_TRACK_SECONDS,
            chapters=chapters,
        )
        # Set FIRST, then overwritten on success. It was set on two of
        # the three exits and missing on the third, which is how a field
        # becomes a KeyError for one caller and an empty string that
        # reads as a third state for another.
        plan["planned_by"] = "arithmetik"
        if not isinstance(scene_plan, dict) or not scene_plan.get("scenes"):
            return plan
        directed = await self._async_directed_sections(
            scene_plan, plan, manifest=manifest, chapters=chapters
        )
        if directed is None:
            return plan
        plan["sections"] = directed
        plan["generation_count"] = len(directed)
        plan["planned_by"] = "gemini"
        return plan

    async def _async_directed_sections(
        self,
        scene_plan: dict[str, Any],
        plan: dict[str, Any],
        *,
        manifest: dict[str, Any],
        chapters: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        """Ask a model where the music should turn. Never trust the answer.

        Returns sections in the same shape the arithmetic planner
        produces, or None - and None means "use that one", not "fail".
        Music is the last thing that should stop a film from rendering.
        """
        director = self._plan_director
        if director is None:
            return None
        try:
            sheet = build_cue_sheet(scene_plan, chapters=chapters)
        except CueSheetError as err:
            _LOGGER.debug("Kein Cue Sheet: %s", err)
            return None
        brief = build_brief(
            sheet,
            trip_title=str(plan.get("trip_title") or ""),
            narrative=manifest.get("narrative") or {},
            motifs=list(plan.get("motifs") or []),
            max_sections=DIRECTOR_MAX_SECTIONS,
            style=str(plan.get("style") or ""),
        )
        try:
            proposal = await director(brief)
        except Exception as err:  # noqa: BLE001 - a planner is never fatal
            _LOGGER.info("Musikplanung ohne Modell: %s", type(err).__name__)
            return None
        try:
            chosen = validate_proposal(
                proposal, sheet, track_seconds=LYRIA_TRACK_SECONDS
            )
        except PlanDirectorError as err:
            # Recorded rather than repaired. A proposal nudged into shape
            # is a plan nobody chose, and the reason belongs in the log
            # where somebody can see WHY the film sounds arithmetic.
            _LOGGER.info("Musikvorschlag abgelehnt: %s", err)
            return None

        sections: list[dict[str, Any]] = []
        for index, entry in enumerate(chosen):
            mood = str(entry.get("mood") or "")
            sections.append(
                {
                    "section": f"cue-{entry['starts_at_cue']}",
                    "label": entry.get("label") or f"Abschnitt {index + 1}",
                    "start_seconds": entry["start_seconds"],
                    "end_seconds": entry["end_seconds"],
                    "seconds": entry["seconds"],
                    "fade_in_seconds": FADE_IN_SECONDS if index == 0 else CROSSFADE_SECONDS,
                    "fade_out_seconds": (
                        FADE_OUT_SECONDS if index == len(chosen) - 1 else CROSSFADE_SECONDS
                    ),
                    "mood": mood,
                    "prompt": section_prompt(
                        {"mood": mood},
                        motifs=list(plan.get("motifs") or []),
                        seconds=entry["seconds"],
                    ),
                }
            )
        return sections

    async def async_offer(
        self, trip_id: str, *, film_seconds: float = 0.0
    ) -> dict[str, Any]:
        """What generating would cost and what would be ordered.

        Reads the manifest and the music folder, writes nothing. A
        section already generated for this plan is named as cached, and
        "four sections, three already there, one new" is a materially
        different decision from "four new" - so it is the one shown.
        """
        plan = await self._async_plan(trip_id, film_seconds)
        sections = await self._async_section_state(plan)
        cached = sum(1 for entry in sections if entry["cached_name"])
        notice = plan_cost_notice(
            plan,
            model=LYRIA_MODEL,
            price_per_generation=LYRIA_ESTIMATED_COST_USD,
            cached=cached,
        )
        ready, reason = self._vertex_state()
        notice.update(
            {
                "plan": plan,
                "section_state": sections,
                "price_note": LYRIA_PRICE_NOTE,
                # Whether the button can work, asked of what the button
                # actually needs. It used to read the Gemini API key -
                # which Lyria refuses - so the dialog promised a
                # generation that could not happen, and the failure only
                # appeared after somebody agreed to pay for it.
                "available": ready,
                # And WHY not, when not. "Nicht verfügbar" without a
                # reason is an absent answer dressed as a state: it sends
                # somebody checking the one thing that is fine.
                "unavailable_reason": reason,
            }
        )
        return notice

    async def _async_section_state(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        """For each section: its key, and the file already generated for it."""
        state: list[dict[str, Any]] = []
        for section in plan.get("sections") or []:
            key = section_cache_key(section, model=LYRIA_MODEL)
            existing = await self._hass.async_add_executor_job(self._find_cached, key)
            state.append(
                {
                    "section": section.get("section"),
                    "label": section.get("label"),
                    "seconds": section.get("seconds"),
                    "start_seconds": section.get("start_seconds"),
                    "end_seconds": section.get("end_seconds"),
                    "fade_in_seconds": section.get("fade_in_seconds"),
                    "fade_out_seconds": section.get("fade_out_seconds"),
                    "key": key,
                    "cached_name": existing or "",
                }
            )
        return state

    async def async_timeline(
        self,
        trip_id: str,
        *,
        film_seconds: float = 0.0,
        scene_plan: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """What is already generated for this film, and when it plays.

        Read-only by construction, and that is the point: this is the
        one method the film exporter is given. It can see the tracks
        that exist; it has no way to order one. A section nobody paid
        for is simply absent from the timeline, and the film renders
        with the sections that are there.
        """
        plan = await self._async_plan(trip_id, film_seconds, scene_plan=scene_plan)
        state = await self._async_section_state(plan)
        entries = [entry for entry in state if entry["cached_name"]]
        timeline: list[dict[str, Any]] = []
        for position, entry in enumerate(entries):
            # Named per entry rather than derived at mux time: the mux
            # must be able to place every section without deciding
            # anything, so a crossfade is a number here and not a rule
            # somewhere else. Two neighbours that are actually adjacent
            # cross; a gap left by an ungenerated section does not.
            follows = position > 0 and (
                abs(entry["start_seconds"] - entries[position - 1].get("end_seconds", -1))
                < CROSSFADE_SECONDS + 0.5
            )
            timeline.append(
                {
                    "name": entry["cached_name"],
                    "music_asset_id": entry["key"],
                    "start_seconds": entry["start_seconds"],
                    "seconds": entry["seconds"],
                    # Where in the generated track this section starts.
                    # Always zero today - one generation per section - and
                    # named anyway, so a later change that reuses one
                    # track twice has somewhere to say so instead of
                    # inventing a field.
                    "asset_offset_seconds": 0.0,
                    "volume": DEFAULT_MUSIC_VOLUME,
                    "fade_in_seconds": entry["fade_in_seconds"],
                    "fade_out_seconds": entry["fade_out_seconds"],
                    "crossfade_seconds": CROSSFADE_SECONDS if follows else 0.0,
                }
            )
        return timeline

    async def async_generate(
        self, trip_id: str, *, film_seconds: float = 0.0
    ) -> dict[str, Any]:
        """Order the sections that are missing. The only paid method here.

        Sections already generated for this plan short-circuit before any
        request is built. That is what makes a re-render free and what
        makes two films of the same trip sound the same - and it is per
        section, so a film that grew by a minute regenerates the section
        whose length changed rather than the whole score.
        """
        plan = await self._async_plan(trip_id, film_seconds)
        state = await self._async_section_state(plan)
        missing = [entry for entry in state if not entry["cached_name"]]
        if missing:
            # Asked of the route this generation would actually take.
            # This used to read the API key alone, which would have
            # refused a perfectly configured Vertex project for missing
            # a credential it does not use - the same "wrong question"
            # the availability flag had.
            ready, why = self._vertex_state()
            if not ready:
                raise ValidationError(why)


        generated = 0
        for section, entry in zip(plan.get("sections") or [], state):
            if entry["cached_name"]:
                continue
            blob, mime_type = await self._async_order(
                str(section.get("prompt") or ""),
                # Asked for explicitly: a section that comes back shorter
                # than planned is silence at its end, and that is the
                # failure this whole area just had.
                seconds=float(section.get("seconds") or 0) or None,
            )
            name = track_filename(entry["key"], extension_for(mime_type))
            written = await self._hass.async_add_executor_job(self._store, name, blob)
            if not written:
                raise ValidationError("Die erzeugte Musik konnte nicht gespeichert werden")
            entry["cached_name"] = name
            generated += 1

        return {
            "plan": plan,
            "sections": state,
            "generated": generated,
            "reused": len(state) - generated,
            # What the renderer needs: which file plays when, and how it
            # arrives and leaves. Built here rather than in the renderer
            # so the timeline and the prices come from the same plan.
            "timeline": [
                {
                    "name": entry["cached_name"],
                    "start_seconds": entry["start_seconds"],
                    "seconds": entry["seconds"],
                    "fade_in_seconds": entry["fade_in_seconds"],
                    "fade_out_seconds": entry["fade_out_seconds"],
                }
                for entry in state
                if entry["cached_name"]
            ],
        }

    async def _async_order(self, prompt: str, *, seconds: float | None = None) -> tuple[bytes, str]:
        """One generation. Everything that can cost money passes here."""
        route = self._route()
        if route["kind"] == "vertex":
            token = await self._async_access_token(route["tokens"])
            headers = {"Authorization": f"Bearer {token}"}
            url, body = build_request(
                prompt,
                project=str(route["project"]),
                region=str(route.get("region") or LYRIA_REGION),
                seconds=seconds,
            )
        else:
            headers = {"x-goog-api-key": str(route["api_key"])}
            url, body = build_request(prompt)
        try:
            session = self._session_factory()
            async with session.post(
                url,
                json=body,
                headers={**headers, "Content-Type": "application/json"},
                timeout=LYRIA_TIMEOUT_SECONDS,
            ) as response:
                if response.status >= 400:
                    detail = (await response.text())[:300]
                    raise LyriaError(
                        f"Lyria hat den Auftrag abgelehnt ({response.status}): {detail}"
                    )
                payload = await response.json(content_type=None)
        except LyriaError:
            raise
        except Exception as err:  # noqa: BLE001 - any transport failure reads the same
            _LOGGER.warning("Musikgenerierung fehlgeschlagen: %s", type(err).__name__)
            raise ValidationError(
                "Die Musik konnte nicht erzeugt werden. Der Film läuft ohne."
            ) from err

        found = audio_from_response(payload)
        if not found:
            raise ValidationError(
                "Lyria hat keine Audiodaten zurückgegeben. Der Film läuft ohne Musik."
            )
        blob, mime_type = found
        if len(blob) > MAX_TRACK_BYTES:
            raise ValidationError("Die erzeugte Musikdatei ist zu groß")
        return blob, mime_type

    # --- blocking helpers, run in the executor --------------------------

    def _find_cached(self, key: str) -> str:
        """A track already generated for this brief, by name."""
        for extension in ("mp3", "wav"):
            try:
                candidate = self._root / track_filename(key, extension)
            except ValueError:
                return ""
            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    return candidate.name
            except OSError:
                continue
        return ""

    def _store(self, name: str, data: bytes) -> str:
        """Write the track into the folder the film already reads."""
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            target = self._root / name
            temporary = target.with_suffix(target.suffix + ".part")
            temporary.write_bytes(data)
            temporary.replace(target)
        except OSError as err:
            _LOGGER.warning("Erzeugte Musik nicht speicherbar: %s", err)
            return ""
        return name


__all__ = ["TripFilmMusicService"]
