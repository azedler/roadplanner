"""Which photographs tell a day, decided in stages that can be inspected.

The live report that started this: the day at the moose park came out of
the film **without a moose**, and the photographs were there the whole
time. Four things had to go wrong together, and all four were the same
mistake in different clothes - a decision made before there was anything
to decide it with.

1. **The series collapsed first.** Six moose photographs inside four
   seconds were treated as one burst, and the burst kept exactly one
   picture: the one with the best *metadata*. The other five were gone
   before any eye, human or otherwise, had seen them.

2. **The score never looked at the picture.** `media_quality_score`
   reads file size, megapixels, aspect ratio, how near the stop was.
   Every one of those is true of a sharp photograph of a car park. A
   moose two hundred metres further from the stop scores *worse* than
   gravel.

3. **Vision was never asked.** The semantic selection existed, but it
   ran per stop and reordered only what the local stage had already
   chosen - and the film's own path (`story_context_builder`) called the
   local selection directly and passed no curation at all. So the film
   has never once seen a Gemini opinion.

4. **Nothing knew a moose was expected.** A day whose stop is called
   "Elchpark" has an obvious subject, and nothing in the pipeline had a
   place to write that down, let alone to notice it was missing.

So this module separates the stages that were tangled:

- `technical_verdict` throws away what is broken, and *only* what is
  broken. When in doubt it keeps.
- `group_series` groups photographs taken together and **keeps every
  member**. Nearness in time is a grouping signal; it is not a verdict
  about which picture is better.
- `candidate_pool` builds a pool deliberately larger than the film needs,
  so the semantic stage has something to choose between.
- `visual_brief` writes down what a day is *about*, derived from the
  roadbook rather than invented.
- `coverage` says whether the chosen pictures actually show it.
- `select_for_chapter` picks the final set: pinned first, then coverage,
  then variety.

Nothing here calls anything or costs anything. It is arithmetic over
records, so every stage can be tested without a key and without a
network.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any, Iterable

CURATION_VERSION = 1

# --- stage 1: what is broken ------------------------------------------

REJECT_SCREENSHOT = "Bildschirmfoto"
REJECT_DUPLICATE = "exakte Dublette"
REJECT_TINY = "zu klein"
REJECT_BROKEN = "unlesbar"
REJECT_EXTREME_RATIO = "extremer Bildausschnitt"
REJECT_NOT_A_PHOTO = "kein Foto"

# Deliberately low. This stage answers "is the file broken?", not "is the
# picture good?" - a 640x480 holiday snap from 2009 is a memory, and the
# only thing that should die here is something nobody could ever want.
MIN_PIXELS = 300 * 300
# A panorama is a real photograph; 12:1 is a scan artefact or a stitched
# strip that no 16:9 frame can show.
MAX_ASPECT = 6.0

# --- stage 2: series ---------------------------------------------------

# Chained, not windowed: each photograph joins the run if it follows the
# *previous* one closely. Six shutter presses over eleven seconds are one
# series even though the first and last are far apart, which is exactly
# how somebody photographs an animal that will not hold still.
SERIES_GAP_SECONDS = 12.0
SERIES_DISTANCE_M = 60.0

# --- stage 3: pool -----------------------------------------------------

# How many of one series may enter the pool. More than this from a single
# moment is not a selection, it is the burst again - but two or three let
# the semantic stage compare rather than accept.
POOL_PER_SERIES = 3
POOL_PER_LONE_SERIES = 4
# The pool is a fraction of what exists, with a floor and a ceiling. A day
# with 80 photographs should offer 15-30; a day with 6 should offer all 6.
POOL_FRACTION = 0.4
POOL_MINIMUM = 8
POOL_CEILING = 30

_SCREENSHOT_MARKERS = ("screenshot", "bildschirmfoto", "screen shot", "screen-shot")

# --- the language bridge ----------------------------------------------
#
# German writes "Elchpark" as one word, and a model asked what it sees
# says "Elch". Splitting a compound on its tail is what connects the two.
#
# This is a list of *words*, not of places. It has no idea that Aron went
# to a moose park; it knows that German glues a generic tail onto a
# specific head, and that the head is the subject. Adding a place here
# would be the hard-coded special case the brief rules out.
_COMPOUND_TAILS = (
    "park", "gehege", "zoo", "garten", "wald", "see", "meer", "strand",
    "fall", "falle", "berg", "tal", "fjord", "gletscher", "insel",
    "hafen", "brucke", "brücke", "kirche", "dom", "kloster", "museum",
    "schloss", "burg", "turm", "denkmal", "markt", "platz", "dorf",
    "stadt", "haus", "hutte", "hütte", "bad", "quelle", "hohle", "höhle",
    "schlucht", "kanal", "bruecke", "station", "village", "center",
    "centre", "point", "beach", "falls", "bridge", "castle", "church",
)

# Words that name a category rather than a subject. They may still be
# worth showing, so they become "nice to cover" rather than required.
_GENERIC_MOTIFS = frozenset({
    "park", "platz", "stadt", "dorf", "haus", "markt", "station", "center",
    "centre", "point", "village", "camping", "campingplatz", "stellplatz",
    "parkplatz", "rastplatz", "hotel", "restaurant", "cafe", "supermarkt",
    "tankstelle", "waschsalon", "grenze", "start", "ziel", "ankunft",
    "abfahrt", "unterwegs", "wegpunkt", "tag", "etappe", "nacht",
})

_STOPWORDS = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer",
    "eines", "einem", "einen", "und", "oder", "aber", "mit", "ohne",
    "von", "vom", "zum", "zur", "nach", "bei", "beim", "auf", "aus",
    "fur", "für", "uber", "über", "unter", "vor", "hinter", "neben",
    "zwischen", "durch", "gegen", "ist", "sind", "war", "waren", "wir",
    "uns", "sich", "sein", "ihre", "ihr", "dann", "noch", "auch", "sehr",
    "the", "and", "for", "with", "from", "into", "our", "was", "were",
    "km", "kilometer", "stunden", "minuten", "uhr", "heute", "morgen",
    "gestern", "wieder", "weiter", "erste", "letzte", "nachster",
})

MIN_MOTIF_LENGTH = 4
MAX_MUST_COVER = 3
MAX_NICE_TO_COVER = 5


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fold(value: Any) -> str:
    """Casefold and flatten the umlauts, so one spelling matches another."""
    text = _text(value).casefold()
    for source, target in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss")):
        text = text.replace(source, target)
    return text


def _parse_datetime(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coordinate(item: dict[str, Any]) -> tuple[float, float] | None:
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    latitude = location.get("latitude", location.get("lat"))
    longitude = location.get("longitude", location.get("lon", location.get("lng")))
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return None
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return float(latitude), float(longitude)


def _distance_m(first: tuple[float, float], second: tuple[float, float]) -> float:
    radius = 6_371_000.0
    phi_1, phi_2 = math.radians(first[0]), math.radians(second[0])
    delta_phi = math.radians(second[0] - first[0])
    delta_lambda = math.radians(second[1] - first[1])
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_1) * math.cos(phi_2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1 - value)))


def technical_verdict(item: dict[str, Any]) -> dict[str, str]:
    """Is this file broken? Not: is this picture any good?

    Conservative on purpose. A photograph wrongly dropped here is a
    memory nobody will ever be offered again; a weak one wrongly kept
    costs a fraction of a cent and loses a comparison later.
    """
    if _fold(item.get("media_type") or "photo") not in {"photo", ""}:
        return {"usable": False, "reason": REJECT_NOT_A_PHOTO}
    name = _fold(item.get("name"))
    if item.get("is_screenshot") or any(mark in name for mark in _SCREENSHOT_MARKERS):
        return {"usable": False, "reason": REJECT_SCREENSHOT}
    size = item.get("size_bytes")
    if isinstance(size, int) and not isinstance(size, bool) and 0 < size < 8_000:
        return {"usable": False, "reason": REJECT_BROKEN}
    width, height = item.get("width"), item.get("height")
    if all(isinstance(value, int) and not isinstance(value, bool) and value > 0
           for value in (width, height)):
        if width * height < MIN_PIXELS:
            return {"usable": False, "reason": REJECT_TINY}
        ratio = max(width / height, height / width)
        if ratio > MAX_ASPECT:
            return {"usable": False, "reason": REJECT_EXTREME_RATIO}
    return {"usable": True, "reason": ""}


def duplicate_groups(items: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Byte-identical files, grouped by their hash.

    Only an exact hash counts. "Looks similar" is the semantic stage's
    job, and guessing at it here is how five moose disappeared.
    """
    groups: dict[str, list[str]] = {}
    for item in items:
        digest = _fold(item.get("file_hash"))
        if not digest:
            continue
        groups.setdefault(digest, []).append(_text(item.get("id")))
    return {key: values for key, values in groups.items() if len(values) > 1}


def group_series(
    items: Iterable[dict[str, Any]],
    *,
    gap_seconds: float = SERIES_GAP_SECONDS,
    distance_m: float = SERIES_DISTANCE_M,
) -> list[dict[str, Any]]:
    """Group photographs taken in one go, keeping every member.

    This is the change the whole module exists for. The old burst logic
    grouped and *reduced* in the same breath: it kept the best-scoring
    member and dropped the rest, before anything had looked at them. Six
    attempts at a moose are one moment, and which of the six is the good
    one is precisely the question that cannot be answered from a file
    size.
    """
    ordered = sorted(
        (item for item in items if isinstance(item, dict)),
        key=lambda item: (
            _text(item.get("taken_at") or item.get("created_at")),
            _text(item.get("id")),
        ),
    )
    series: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for item in ordered:
        if previous is not None and _follows(previous, item, gap_seconds, distance_m):
            current.append(item)
        else:
            if current:
                series.append(_series_record(len(series), current))
            current = [item]
        previous = item
    if current:
        series.append(_series_record(len(series), current))
    return series


def _follows(
    previous: dict[str, Any],
    item: dict[str, Any],
    gap_seconds: float,
    distance_m: float,
) -> bool:
    first = _parse_datetime(previous.get("taken_at") or previous.get("created_at"))
    second = _parse_datetime(item.get("taken_at") or item.get("created_at"))
    if first is None or second is None:
        return False
    if abs((second - first).total_seconds()) > gap_seconds:
        return False
    here, there = _coordinate(previous), _coordinate(item)
    if here and there:
        return _distance_m(here, there) <= distance_m
    return True


def _series_record(index: int, members: list[dict[str, Any]]) -> dict[str, Any]:
    identifiers = [_text(item.get("id")) for item in members if _text(item.get("id"))]
    return {
        "series_id": f"s{index:03d}",
        "media_ids": identifiers,
        "size": len(identifiers),
        "started_at": _text(members[0].get("taken_at") or members[0].get("created_at")),
        "ended_at": _text(members[-1].get("taken_at") or members[-1].get("created_at")),
    }


def pool_size(
    *,
    photo_count: int,
    series_count: int,
    stop_count: int,
    media_budget: int | None = None,
) -> int:
    """How many photographs a day may offer the semantic stage.

    Derived rather than fixed, because "twelve" is wrong at both ends: a
    day with six pictures needs all six, and a day with eighty needs far
    more than twelve or the interesting ones never get compared.

    A day is also wider than its own photo count suggests when it has
    many stops or many separate moments, so both raise the floor.
    """
    if photo_count <= 0:
        return 0
    fraction = int(math.ceil(photo_count * POOL_FRACTION))
    moments = max(series_count, 1) + max(stop_count, 0)
    wanted = max(POOL_MINIMUM, fraction, moments)
    if media_budget:
        # The film will never show more than its budget, but the pool has
        # to be wider than the film or there is nothing to choose from.
        wanted = max(wanted, int(media_budget) * 2)
    return int(max(1, min(wanted, POOL_CEILING, photo_count)))


def candidate_pool(
    items: list[dict[str, Any]],
    *,
    series: list[dict[str, Any]],
    stop_count: int = 0,
    media_budget: int | None = None,
    local_score: Any = None,
    pinned: Iterable[str] = (),
    excluded: Iterable[str] = (),
) -> dict[str, Any]:
    """The photographs worth a semantic look, with reasons for the rest.

    Every series contributes its best few rather than its best one, and
    what is left over is dropped for a reason that can be shown.
    """
    by_id = {_text(item.get("id")): item for item in items if _text(item.get("id"))}
    pinned_ids = {_text(value) for value in pinned if _text(value)}
    excluded_ids = {_text(value) for value in excluded if _text(value)}
    score = local_score if callable(local_score) else (lambda item: 0.0)

    rejected: list[dict[str, str]] = []
    usable: dict[str, dict[str, Any]] = {}
    for media_id, item in by_id.items():
        if media_id in excluded_ids:
            rejected.append({"media_id": media_id, "reason": "vom Nutzer ausgeschlossen"})
            continue
        verdict = technical_verdict(item)
        if not verdict["usable"] and media_id not in pinned_ids:
            rejected.append({"media_id": media_id, "reason": verdict["reason"]})
            continue
        usable[media_id] = item

    for members in duplicate_groups(list(usable.values())).values():
        keeper = max(members, key=lambda value: (value in pinned_ids, score(by_id[value])))
        for media_id in members:
            if media_id != keeper and media_id in usable:
                usable.pop(media_id)
                rejected.append({"media_id": media_id, "reason": REJECT_DUPLICATE})

    limit = pool_size(
        photo_count=len(usable),
        series_count=len(series),
        stop_count=stop_count,
        media_budget=media_budget,
    )

    chosen: list[str] = [media_id for media_id in pinned_ids if media_id in usable]
    # One round per rank: every series offers its best, then every series
    # offers its second best. A day with one long series still fills the
    # pool; a day with many moments is never crowded out by one of them.
    ranked_by_series: list[list[str]] = []
    for record in series:
        members = [
            media_id
            for media_id in record.get("media_ids") or []
            if media_id in usable and media_id not in chosen
        ]
        members.sort(key=lambda value: -float(score(by_id[value]) or 0.0))
        ceiling = POOL_PER_LONE_SERIES if len(series) == 1 else POOL_PER_SERIES
        ranked_by_series.append(members[:ceiling])
    for rank in range(max((len(entry) for entry in ranked_by_series), default=0)):
        for members in ranked_by_series:
            if rank < len(members) and len(chosen) < limit:
                chosen.append(members[rank])
    for media_id, item in sorted(
        usable.items(), key=lambda entry: -float(score(entry[1]) or 0.0)
    ):
        if len(chosen) >= limit:
            break
        if media_id not in chosen:
            chosen.append(media_id)

    for media_id in usable:
        if media_id not in chosen:
            rejected.append({"media_id": media_id, "reason": "über dem Tagesbudget"})
    return {
        "media_ids": chosen,
        "pool_size": len(chosen),
        "photo_count": len(by_id),
        "series_count": len(series),
        "rejected": rejected,
    }


# --- stage 4: what the day is about -----------------------------------


def motif_tokens(text: Any) -> list[str]:
    """The subjects hidden in a name, including inside German compounds.

    "Elchpark" is one word to a filesystem and two ideas to a reader.
    Splitting on a generic tail is what lets a picture the model called
    "Elch" answer a day the roadbook called "Elchpark".
    """
    words = [word for word in re.split(r"[^0-9a-zA-ZäöüÄÖÜß]+", _text(text)) if word]
    tokens: list[str] = []
    for word in words:
        folded = _fold(word)
        if len(folded) < MIN_MOTIF_LENGTH or folded in _STOPWORDS or folded.isdigit():
            continue
        # When a compound splits, the head replaces the whole word rather
        # than joining it. "Elchpark" asks for a moose, not for a moose
        # AND a moose park - and a model that answers "Elch" satisfies
        # both, so keeping both only makes the brief longer to read.
        split = ""
        for tail in _COMPOUND_TAILS:
            folded_tail = _fold(tail)
            if folded.endswith(folded_tail) and len(folded) > len(folded_tail) + 2:
                head = folded[: -len(folded_tail)]
                if len(head) >= MIN_MOTIF_LENGTH:
                    split = folded_tail
                    if head not in tokens:
                        tokens.append(head)
                    if folded_tail not in tokens:
                        tokens.append(folded_tail)
                break
        if not split and folded not in tokens:
            tokens.append(folded)
    return tokens


def visual_brief(chapter: dict[str, Any]) -> dict[str, Any]:
    """What this day should be seen to contain, from the roadbook alone.

    Derived, never invented, and never a demand: the brief says what
    would represent the day *if it was photographed*. Whether it was is
    what `coverage` answers, and "nobody took that picture" is a valid
    answer that changes nothing about the day.

    Deterministic on purpose. The same information is already written
    down in the stops and the title, and paying a model to read it back
    would be paying for a fact we have.
    """
    must: list[str] = []
    nice: list[str] = []
    for stop in chapter.get("stops") or []:
        if not isinstance(stop, dict):
            continue
        for token in motif_tokens(stop.get("name")):
            target = nice if token in _GENERIC_MOTIFS else must
            if token not in target:
                target.append(token)
        for token in motif_tokens(stop.get("kind") or stop.get("type")):
            if token not in nice and token not in must:
                nice.append(token)
    for token in motif_tokens(chapter.get("title")):
        if token in _GENERIC_MOTIFS:
            if token not in nice and token not in must:
                nice.append(token)
        elif token not in must:
            must.append(token)
    return {
        "must_cover": must[:MAX_MUST_COVER],
        "nice_to_cover": [token for token in nice if token not in must][:MAX_NICE_TO_COVER],
    }


def motif_matches(required: str, motifs: Iterable[str]) -> bool:
    """Does any observed motif answer this required one?

    Matched both ways round and folded, so "Elch" answers "elch",
    "Elche" answers "elch", and "Elchgehege" answers "elch" as well.
    """
    wanted = _fold(required)
    if len(wanted) < MIN_MOTIF_LENGTH:
        return False
    for motif in motifs:
        seen = _fold(motif)
        if len(seen) < MIN_MOTIF_LENGTH:
            continue
        if wanted in seen or seen in wanted:
            return True
    return False


def coverage(
    brief: dict[str, Any],
    analyses: dict[str, dict[str, Any]],
    media_ids: Iterable[str],
) -> dict[str, Any]:
    """Which of the day's subjects the chosen pictures actually show."""
    chosen = [str(value) for value in media_ids]
    seen: list[str] = []
    for media_id in chosen:
        analysis = analyses.get(media_id) or {}
        seen.extend(str(motif) for motif in analysis.get("motifs") or [])
    met, unmet = [], []
    for required in brief.get("must_cover") or []:
        (met if motif_matches(required, seen) else unmet).append(required)
    return {
        "met": met,
        "unmet": unmet,
        "complete": not unmet,
        "nice_met": [
            token
            for token in brief.get("nice_to_cover") or []
            if motif_matches(token, seen)
        ],
    }


def photos_for(brief: dict[str, Any], analyses: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """For each required motif, the photographs that show it, best first."""
    found: dict[str, list[str]] = {}
    for required in brief.get("must_cover") or []:
        matches = [
            media_id
            for media_id, analysis in analyses.items()
            if motif_matches(required, analysis.get("motifs") or [])
        ]
        matches.sort(key=lambda value: -semantic_score(analyses.get(value) or {}))
        found[required] = matches
    return found


def semantic_score(analysis: dict[str, Any]) -> float:
    """How much a film wants this photograph.

    Story value counts double, as with clips and for the same reason: a
    technically perfect picture of nothing is what a camera roll is full
    of, and the point of all of this is to find the moments.
    """
    def _value(key: str) -> float:
        raw = analysis.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return 0.0
        return max(0.0, min(5.0, float(raw)))

    return round(
        _value("story_value") * 2.0
        + _value("visual_quality")
        + _value("emotion") * 0.5
        + _value("uniqueness") * 0.5,
        3,
    )


# --- stage 6: the final set -------------------------------------------


def select_for_chapter(
    *,
    candidates: Iterable[str],
    analyses: dict[str, dict[str, Any]],
    brief: dict[str, Any],
    series_by_media: dict[str, str] | None = None,
    target: int,
    pinned: Iterable[str] = (),
    excluded: Iterable[str] = (),
) -> dict[str, Any]:
    """The photographs this day shows, and why each one is there.

    Order of authority, and it is an order rather than a score:

    1. What the person pinned. A decision by hand outranks every rule.
    2. One picture for each subject the day is about, best first. This is
       the whole moose fix: a day at a moose park shows a moose before it
       shows a fourth pretty tree.
    3. Everything else by merit, one per series at a time, so six good
       photographs of one moment cannot crowd out the other five moments.

    Step 3 loops over series in rounds rather than taking each series'
    best in turn - a day that genuinely *is* one animal encounter still
    ends up with several of it, because when the rounds run out the
    remaining ranks are still there.
    """
    excluded_ids = {str(value) for value in excluded if str(value)}
    ordered = [str(value) for value in candidates if str(value) not in excluded_ids]
    known = set(ordered)
    series_by_media = series_by_media or {}
    limit = max(0, int(target))

    selected: list[str] = []
    reasons: dict[str, str] = {}

    for media_id in (str(value) for value in pinned):
        if media_id and media_id not in excluded_ids and media_id not in selected:
            selected.append(media_id)
            reasons[media_id] = "vom Nutzer ausgewählt"

    for required, matches in photos_for(brief, {
        media_id: analysis for media_id, analysis in analyses.items() if media_id in known
    }).items():
        if any(media_id in selected for media_id in matches):
            continue
        for media_id in matches:
            if media_id not in selected and len(selected) < max(limit, 1):
                selected.append(media_id)
                reasons[media_id] = f"zeigt {required}"
                break

    remaining = [media_id for media_id in ordered if media_id not in selected]
    remaining.sort(key=lambda value: -semantic_score(analyses.get(value) or {}))
    by_series: dict[str, list[str]] = {}
    for media_id in remaining:
        by_series.setdefault(series_by_media.get(media_id, media_id), []).append(media_id)
    runs = list(by_series.values())
    for rank in range(max((len(run) for run in runs), default=0)):
        for run in runs:
            if len(selected) >= limit:
                break
            if rank < len(run):
                media_id = run[rank]
                selected.append(media_id)
                reasons[media_id] = (
                    "bestes Bild dieses Moments" if rank == 0 else "weiteres gutes Bild"
                )
        if len(selected) >= limit:
            break

    selected = selected[: max(limit, len(list(pinned)))]
    return {
        "media_ids": selected,
        "reasons": reasons,
        "coverage": coverage(brief, analyses, selected),
    }


__all__ = [
    "CURATION_VERSION",
    "POOL_CEILING",
    "POOL_MINIMUM",
    "SERIES_GAP_SECONDS",
    "candidate_pool",
    "coverage",
    "duplicate_groups",
    "group_series",
    "motif_matches",
    "motif_tokens",
    "photos_for",
    "pool_size",
    "select_for_chapter",
    "semantic_score",
    "technical_verdict",
    "visual_brief",
]
