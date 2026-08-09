"""Turn a described trip into a shot list. Deterministic, and pure.

Film v0 rendered every day the same way, so a three-week journey came out
as twenty-three identical cards followed by a slideshow. The story
director then started deciding which days matter - importance,
story_role, visual_style - and the film ignored all three. This module is
where that judgement finally reaches the screen.

The division of labour is the whole design, and it is worth stating
because each part is bad at the other two:

- **Gemini decides what matters.** It is the only one that read the
  trip as a whole.
- **Roadplanner supplies facts and pictures.** It is the only one that
  knows what actually happened.
- **This module decides how that looks**, from fixed rules. No model is
  called here and none may be: the same manifest has to produce the same
  film, or a re-render is a lottery and nothing about the result can be
  reasoned about.

Why a scene plan at all
-----------------------

It could have been done inside the composition. It is done here because
a rule like "a transition day gets one photo and five seconds" is a
sentence about editing, not about React - and because a plan built in
Python can be tested without a browser, a bundler or a render. The plan
is a **rendering derivation**: it is computed from the manifest, travels
in the render package, and never goes back into the manifest. Frame
counts have no business in a description of a journey.

The finite scene library
------------------------

Six kinds of scene and nothing else. ``visual_style`` selects among them;
it cannot describe a layout. That boundary is deliberate: a model that
could invent scene shapes would eventually invent one that cannot be
rendered, and the failure would arrive as a broken film rather than as a
refused value.
"""

from __future__ import annotations

from typing import Any

FILM_FPS = 30
PLAN_VERSION = 1

# --- the library ---------------------------------------------------------

SCENE_INTRO = "intro"
SCENE_CHAPTER_CARD = "chapter_card"
SCENE_PHOTO = "photo"
SCENE_HERO = "hero"
SCENE_COLLAGE = "collage"
# A real video clip, cut to the moment somebody chose. Its own scene type
# rather than a photo with a flag: the composition has to play it, and a
# renderer that mistakes one for the other shows a frozen frame.
SCENE_CLIP = "clip"
SCENE_TEXT = "text"
SCENE_OUTRO = "outro"
SCENE_OUTRO_COLLAGE = "outro_collage"
# The map. Three scenes rather than one, because the map does three
# different jobs: it opens the journey, it carries a travelling day, and
# it closes the whole thing.
SCENE_MAP_START = "map_start"
SCENE_MAP_LEG = "map_leg"
SCENE_MAP_FULL = "map_full"
# The crew, once, near the opening. Its own type because it is neither a
# chapter nor a map, and because a film without portraits must simply not
# contain it rather than render an empty row of circles.
SCENE_CREW = "crew"
SCENE_TYPES = (
    SCENE_CLIP,
    SCENE_INTRO,
    SCENE_CHAPTER_CARD,
    SCENE_PHOTO,
    SCENE_HERO,
    SCENE_COLLAGE,
    SCENE_TEXT,
    SCENE_OUTRO,
    SCENE_OUTRO_COLLAGE,
    SCENE_MAP_START,
    SCENE_MAP_LEG,
    SCENE_MAP_FULL,
    SCENE_CREW,
)

IMPORTANCE_TRANSITION = "transition"
IMPORTANCE_NORMAL = "normal"
IMPORTANCE_HIGHLIGHT = "highlight"
IMPORTANCE_MAJOR = "major_highlight"
_IMPORTANCE_ORDER = (
    IMPORTANCE_TRANSITION,
    IMPORTANCE_NORMAL,
    IMPORTANCE_HIGHLIGHT,
    IMPORTANCE_MAJOR,
)

# How long each kind of scene runs, per importance. A table rather than a
# solver: every number here can be argued about on its own, and the total
# for a day follows from the scenes it actually got. A day with one photo
# stays short even when it is a highlight - padding a thin day is exactly
# the "artificially extended" the brief rules out.
# A title card is read, so its floor comes from reading rather than from
# taste. These are the minimums; `reading_frames` raises them for a long
# title, because a card nobody finished reading is a card nobody read.
_CARD_FRAMES = {
    IMPORTANCE_TRANSITION: 66,
    IMPORTANCE_NORMAL: 84,
    IMPORTANCE_HIGHLIGHT: 100,
    IMPORTANCE_MAJOR: 120,
}
_PHOTO_FRAMES = {
    IMPORTANCE_TRANSITION: 105,
    IMPORTANCE_NORMAL: 105,
    IMPORTANCE_HIGHLIGHT: 110,
    IMPORTANCE_MAJOR: 120,
}
_HERO_FRAMES = {
    IMPORTANCE_TRANSITION: 120,
    IMPORTANCE_NORMAL: 150,
    IMPORTANCE_HIGHLIGHT: 165,
    IMPORTANCE_MAJOR: 210,
}
_COLLAGE_FRAMES = {
    IMPORTANCE_TRANSITION: 105,
    IMPORTANCE_NORMAL: 135,
    IMPORTANCE_HIGHLIGHT: 165,
    IMPORTANCE_MAJOR: 195,
}
# A day without photographs is not an error message. It is a page of
# writing, and it needs long enough to be read.
_TEXT_FRAMES = {
    IMPORTANCE_TRANSITION: 105,
    IMPORTANCE_NORMAL: 135,
    IMPORTANCE_HIGHLIGHT: 165,
    IMPORTANCE_MAJOR: 180,
}

# How many pictures a day is worth - the MEDIA budget.
#
# Raised sharply from 1/2/3/4, because the film it produced was thin in a
# way the numbers did not show: most days were "title card, one
# photograph, next day", over and over, while the library held hundreds
# of curated pictures that never appeared. A day of a real journey is not
# one picture.
#
# This is deliberately no longer the same decision as how LONG a day
# runs. They were the same number before - each picture bought its own
# seconds - so more memories could only mean a longer film. Separating
# them is what makes "a more detailed film shows more good material"
# possible without "a more detailed film is the same film, slower", and
# it is the foundation the planned length control needs.
# Raised again once the curation could be trusted. The earlier numbers
# were a hedge against a selection that scored file sizes: showing more
# of THAT is showing more gravel. Now that a day's pictures are chosen
# for what is in them, more of them is more of the day.
PHOTO_WEIGHTS = {
    IMPORTANCE_TRANSITION: 3,
    IMPORTANCE_NORMAL: 6,
    IMPORTANCE_HIGHLIGHT: 10,
    IMPORTANCE_MAJOR: 14,
}

# How long a day runs, before its map - the TIME budget, in frames at 30
# fps. A day is worth this much screen time because of what it *was*, not
# because of how many photographs survived the cull.
#
# The pictures are then fitted into it: many pictures means each is shown
# more briefly and more of them are grouped, few pictures means each gets
# room. What must not happen is a chapter growing linearly with its
# library, which is what a per-picture duration guarantees.
# Raised across the board after the first full watch-through: "das
# gesamte Tempo ist zu hoch, Bilder, Text und Karte wechseln zu
# schnell". A film that has to be paused to be read is not a film.
# Length is explicitly the cheaper currency here - five to seven minutes
# for three weeks is fine if it is comfortable to watch.
_CHAPTER_FRAMES = {
    IMPORTANCE_TRANSITION: 300,
    IMPORTANCE_NORMAL: 450,
    IMPORTANCE_HIGHLIGHT: 600,
    IMPORTANCE_MAJOR: 780,
}

# What a shot is worth when nothing is competing for the time: the
# comfortable duration, used as a CEILING rather than as a price.
#
# Without this the time budget alone would pad a thin day: a highlight
# with one photograph would run as long as a highlight with eight, which
# is inflating a day with screen time and says something untrue about it.
# So a day is worth the smaller of "what its importance buys" and "what
# its material can honestly fill".
_NATURAL_FRAMES = {
    SCENE_HERO: 210,
    SCENE_COLLAGE: 240,
    SCENE_PHOTO: 140,
    # A clip is worth what it was cut to, so it has no natural length of
    # its own - the value here is only a fallback for a clip that arrived
    # without one.
    SCENE_CLIP: 150,
}

# The shortest a single picture may be held, and the shortest a group may.
# Below the first a photograph is a flicker rather than a memory; a group
# needs longer because there is more in it to look at.
# Raised from 52/95. At 30 fps the old floor held a photograph for 1.7
# seconds - long enough to register that something was there, not long
# enough to look at it. A collage at 3.2 seconds asked for four pictures
# to be taken in at under a second each.
#
# These floors are what makes a rich day LONGER rather than faster. That
# is the intended trade: the day's importance buys a budget, and when
# the budget cannot pay for its pictures at a watchable pace, the day
# overruns instead of the pictures flickering.
MIN_PHOTO_FRAMES = 78
MIN_GROUP_FRAMES = 140
# Above this many single pictures in one day, the rest are grouped. Four
# stills in a row is a sequence; nine is a slideshow.
MAX_SINGLES_PER_CHAPTER = 3
# How many pictures one grouped scene holds.
GROUP_SIZE = 4

# How many video clips a day may show. Movement is an addition to a day,
# not a replacement for it: a day whose whole record is one long phone
# video is not a day anybody remembers in one long phone video.
_CLIPS_BY_IMPORTANCE = {
    IMPORTANCE_TRANSITION: 1,
    IMPORTANCE_NORMAL: 1,
    IMPORTANCE_HIGHLIGHT: 2,
    IMPORTANCE_MAJOR: 3,
}

# How long the map gets, as a SHARE of the day it belongs to.
#
# This was a fixed table first, and the table was wrong. A transfer day
# with one photograph runs about five seconds; giving it five and a half
# seconds of map more than doubled it, and across a trip the film grew by
# a third - the exact "simply longer" the brief rules out. Measured on
# the 25-day test film: 239 s became 325 s.
#
# A share fixes that by construction, because a thin day is short and its
# map is short with it. The numbers still say the same thing the table
# meant to: a transfer day IS the journey, so its map may take half of
# it; a major highlight wants to arrive and then be about the place, so
# the approach is as brief as it can be.
# How many pictures a day of this importance keeps when the trip budget
# has to give way. Not a target - a floor, and only ever as many as the
# day actually has.
_COVERAGE_FLOOR = {
    IMPORTANCE_TRANSITION: 1,
    IMPORTANCE_NORMAL: 2,
    IMPORTANCE_HIGHLIGHT: 4,
    IMPORTANCE_MAJOR: 5,
}

_MAP_SHARE = {
    IMPORTANCE_TRANSITION: 0.50,
    IMPORTANCE_NORMAL: 0.40,
    IMPORTANCE_HIGHLIGHT: 0.30,
    IMPORTANCE_MAJOR: 0.25,
}
# map_focus is the one style where the map is the act rather than the
# introduction to it.
MAP_FOCUS_SHARE = 0.60
# Below the first, a map is a flash nobody can read; above the second, it
# is a screensaver. The floor was raised when the richer picture budget
# started squeezing the map: a leg is recap, then a glide, then the drive,
# and at a second and a half the recap is a sixth of a second. A map that
# short is not a short map, it is a glitch.
MAP_FRAMES_MIN = 105
MAP_FRAMES_MAX = 210
# How short the pictures of a mapped day may be scaled, and how short any
# single scene may become. Below these a photograph is a flicker.
MIN_PICTURE_SHARE = 0.6
MIN_SCENE_FRAMES = 66

# --- how long a text has to stay on screen ----------------------------
#
# The complaint was not "the film is fast", it was "es wechselt zu
# schnell" - and a text that changes before it is finished is the worst
# case of that, because the viewer knows exactly what they missed.
#
# So a text's duration comes from the text. Fifteen characters a second
# is a deliberately unhurried on-screen reading pace for German (roughly
# 180 words a minute); screen text is read slower than a book, and a
# viewer is also looking at a photograph.
#
# The two constants around it matter as much as the rate: something has
# to be noticed before it can be read, and past a point a still frame
# with a sentence on it stops being a scene.
READING_CHARS_PER_SECOND = 15.0
READING_LEAD_IN_FRAMES = 24
READING_MIN_FRAMES = 78
READING_MAX_FRAMES = 260


def reading_frames(
    text: Any,
    *,
    minimum: int = READING_MIN_FRAMES,
    maximum: int = READING_MAX_FRAMES,
) -> int:
    """How long this text needs to be readable, in frames at 30 fps.

    Derived rather than fixed, because "a caption" is anything from four
    words to three lines, and one duration for both is either a flicker
    or a wait. A short caption may go by quickly; a real sentence gets
    the seconds it takes.
    """
    length = len(" ".join(str(text or "").split()))
    if not length:
        return 0
    needed = READING_LEAD_IN_FRAMES + round(length / READING_CHARS_PER_SECOND * FILM_FPS)
    return int(max(minimum, min(maximum, needed)))


# How much text still reads over a photograph, and where it stops being a
# caption and becomes a page.
#
# Above this, a caption over a picture is a paragraph with a photograph
# behind it: the scrim has to cover half the frame to stay legible, the
# picture is gone anyway, and the text is still clamped. The planner
# gives that text its own scene instead - which also fixes the reported
# "Text läuft unten aus dem Bild", because the fix for text that does not
# fit is not a smaller font, it is a different scene.
CAPTION_OVER_PHOTO_CHARS = 105


# What the map costs is taken back out of the same day. A day keeps at
# least one picture - a map that ate the last photograph would have
# turned a travel film into an atlas - so the day still grows a little,
# and that residue is the price of having a map at all.
MAP_TIME_TAKEN_BACK = 0.75

MAP_START_FRAMES = 120
MAP_FULL_FRAMES = 195
# The crew line-up. Long enough to read the names, short enough that
# nobody is waiting for the journey to start.
CREW_FRAMES = 225

# How a map leg is divided. The brief asks for one continuous movement:
# where we have got to so far, then a glide down into today, then the
# drive itself. These are shares of the leg rather than fixed lengths, so
# the shape is the same whether a day gets three seconds or eight.
MAP_RECAP_SHARE = 0.26
MAP_APPROACH_SHARE = 0.16

INTRO_FRAMES = 135
OUTRO_FRAMES = 135
OUTRO_COLLAGE_FRAMES = 120
OUTRO_COLLAGE_PHOTOS = 6

# story_role does one job and only one: how a scene arrives. Making it a
# second sizing system would put it in a fight with importance, and the
# brief asks for the three fields to complement each other.
_ENTER_BY_ROLE = {
    "opening": "rise",
    "journey": "fade",
    "transition": "cut",
    "highlight": "push",
    "finale": "settle",
}
DEFAULT_ENTER = "fade"


class FilmPlanError(ValueError):
    """The plan cannot be built from this manifest."""


# --- readable places -----------------------------------------------------

# What a stop is called in the roadbook is what you navigate to. What a
# stop is called in a film is what you would say out loud. These are the
# prefixes real Roadplanner data actually carries.
_PROVIDER_PREFIXES = ("park4night", "p4n", "campercontact", "stellplatz", "camping")
_MAX_PLACE_LENGTH = 42


def _strip_parentheses(text: str) -> str:
    out: list[str] = []
    depth = 0
    for character in text:
        if character in "([":
            depth += 1
        elif character in ")]":
            depth = max(0, depth - 1)
        elif not depth:
            out.append(character)
    return " ".join("".join(out).split())


def readable_place(stop: dict[str, Any]) -> str:
    """A stop name a film can show.

    The real example this exists for is
    ``park4night - (595 50) Mjölby - 24 Vetagatan``, which reached a title
    card verbatim and read like a database export. The editor's
    ``story_name`` wins when there is one; otherwise the canonical name is
    reduced conservatively - provider prefix, postcode in brackets, the
    street segment - and if none of that applies the name is returned
    untouched, because most of them are already fine.
    """
    story_name = str(stop.get("story_name") or "").strip()
    if story_name:
        return story_name[:_MAX_PLACE_LENGTH]

    text = " ".join(str(stop.get("name") or "").split())
    if not text:
        return ""
    lowered = text.casefold()
    for prefix in _PROVIDER_PREFIXES:
        for separator in (" - ", ": ", " – "):
            marker = f"{prefix}{separator}"
            if lowered.startswith(marker):
                text = text[len(marker) :].strip()
                lowered = text.casefold()
                break
    text = _strip_parentheses(text)
    segments = [part.strip() for part in text.replace(" – ", " - ").split(" - ")]
    segments = [part for part in segments if part]
    if not segments:
        return ""
    if len(segments) > 1:
        # A segment without digits is a place; one with them is an address.
        without_digits = [part for part in segments if not any(c.isdigit() for c in part)]
        text = without_digits[0] if without_digits else segments[0]
    else:
        text = segments[0]
    # A trailing house number says nothing on screen.
    words = text.split()
    while len(words) > 1 and any(c.isdigit() for c in words[-1]):
        words.pop()
    return " ".join(words)[:_MAX_PLACE_LENGTH]


def readable_places(stops: list[dict[str, Any]], *, limit: int = 3) -> list[str]:
    """The day's route, as few readable names, in order and without repeats."""
    names: list[str] = []
    for stop in stops:
        name = readable_place(stop if isinstance(stop, dict) else {})
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


# --- photo budget --------------------------------------------------------


def allocate_photos(
    chapters: list[dict[str, Any]], *, total_budget: int, per_chapter_cap: int
) -> dict[str, int]:
    """How many pictures each day gets, weighted by what it is worth.

    Flat allocation is what made film v0 feel like a contact sheet: a
    transfer day and the reason for the whole trip got the same three
    pictures. The budget is finite, so weighting is redistribution rather
    than growth - and a day is never given more pictures than it has.
    """
    if not chapters:
        return {}
    wanted: dict[str, int] = {}
    for chapter in chapters:
        chapter_id = str(chapter.get("chapter_id") or "")
        if not chapter_id:
            continue
        available = len(chapter.get("media") or [])
        weight = PHOTO_WEIGHTS.get(chapter.get("importance"), PHOTO_WEIGHTS[IMPORTANCE_NORMAL])
        wanted[chapter_id] = max(0, min(available, weight, per_chapter_cap))

    total = sum(wanted.values())
    if total <= total_budget:
        return wanted

    # Over budget: take from the least important days first, and never
    # take a day's last picture while a richer day still has spares.
    order = sorted(
        wanted,
        key=lambda chapter_id: (
            _IMPORTANCE_ORDER.index(
                _importance_of(chapters, chapter_id)
            ),
            chapter_id,
        ),
    )
    # What a day may never be cut below.
    #
    # Reported from the abnahme: an important place - the Wolfsschanze -
    # was "visuell zu schwach vertreten" although matching photographs
    # existed. The reduction below takes from the least important days
    # first, which is right, but with no floor it kept going: a highlight
    # can be worn down to a single picture by a long trip, and one
    # picture of a place that mattered is the same as none.
    #
    # So an important day keeps a floor as long as it HAS the material -
    # and the floor is what makes "important + material exists" visible
    # rather than merely intended. A day with two photographs still gets
    # two; the floor never invents pictures.
    floors = {
        chapter_id: min(
            wanted[chapter_id],
            _COVERAGE_FLOOR.get(_importance_of(chapters, chapter_id), 1),
        )
        for chapter_id in wanted
    }
    while total > total_budget:
        moved = False
        for chapter_id in order:
            if total <= total_budget:
                break
            if wanted[chapter_id] > max(1, floors[chapter_id]):
                wanted[chapter_id] -= 1
                total -= 1
                moved = True
        if not moved:
            # Every day is at its floor and it is still too many. The
            # floors themselves now give way, still least-important
            # first, so the budget is met without ever pretending a day
            # had fewer photographs than it did.
            for chapter_id in order:
                if total <= total_budget:
                    break
                if wanted[chapter_id] > 1:
                    wanted[chapter_id] -= 1
                    total -= 1
                    moved = True
        if not moved:
            # Everybody is down to one picture and it is still too many.
            for chapter_id in order:
                if total <= total_budget:
                    break
                if wanted[chapter_id] > 0:
                    wanted[chapter_id] -= 1
                    total -= 1
            break
    return wanted


def _importance_of(chapters: list[dict[str, Any]], chapter_id: str) -> str:
    for chapter in chapters:
        if str(chapter.get("chapter_id") or "") == chapter_id:
            value = chapter.get("importance")
            return value if value in _IMPORTANCE_ORDER else IMPORTANCE_NORMAL
    return IMPORTANCE_NORMAL


# --- the plan ------------------------------------------------------------


def _importance(chapter: dict[str, Any]) -> str:
    value = chapter.get("importance")
    return value if value in _IMPORTANCE_ORDER else IMPORTANCE_NORMAL


def _enter(chapter: dict[str, Any]) -> str:
    return _ENTER_BY_ROLE.get(str(chapter.get("story_role") or ""), DEFAULT_ENTER)


def _caption(chapter: dict[str, Any]) -> str:
    """The film's text. The caption first, the story only as a fallback.

    A card is on screen for three seconds. The long text is right for an
    album and unreadable here, so it is used only when the editor never
    wrote a short one.
    """
    value = chapter.get("story")
    if isinstance(value, str):
        # A package chapter: the choice between caption and story was
        # already made when the package was built.
        return value.strip()
    caption = str(chapter.get("video_caption") or "").strip()
    if caption:
        return caption
    return str((value or {}).get("text") or "").strip()


def _chapter_scenes(
    chapter: dict[str, Any],
    *,
    photo_count: int,
    index: int,
    has_map: bool = False,
    clips: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """The shots for one day, priced from one budget rather than summed up.

    The day is worth `_CHAPTER_FRAMES[importance]` seconds of film because
    of what it *was*. Everything it contains - the card, the map, the
    pictures - is paid for out of that, in that order of precedence. So
    adding pictures makes each one shorter or groups more of them; it does
    not make the day longer.

    This is the separation the length control will need: the media budget
    decides how many memories a day may show, this decides how long the
    day runs, and the two are no longer the same number. Before, each
    picture bought its own seconds, so "show more" and "run longer" were
    the same instruction and there was no way to ask for one without the
    other.

    A day can still overrun, when the floors bind: ten pictures do not fit
    into nine seconds without becoming flickers, and a flicker is not a
    memory. A genuinely full day being longer than a thin one is right;
    every day growing with its library is not.
    """
    importance = _importance(chapter)
    style = str(chapter.get("visual_style") or "normal")
    enter = _enter(chapter)
    chapter_id = str(chapter.get("chapter_id") or "")
    caption = _caption(chapter)

    def scene(kind: str, indices: list[int], frames: int) -> dict[str, Any]:
        return {
            "type": kind,
            "chapter_id": chapter_id,
            "chapter_index": index,
            "frames": frames,
            "enter": enter,
            "photos": indices,
        }

    total = _CHAPTER_FRAMES[importance]

    # A transfer day and a map-focus day get no card of their own. The map
    # scene already carries the day number, the date, the country and the
    # title along its lower edge; the same words on a black screen first
    # are a second title for the same thing, and twenty-three of those in
    # a row are most of why the film read as "announcement, one photo,
    # announcement, one photo".
    folded = has_map and (importance == IMPORTANCE_TRANSITION or style == "map_focus")
    # A card carries a title and often a line of facts. Whichever takes
    # longer to read sets its length, never less than the importance
    # floor - a card that changes mid-title is the reported complaint in
    # its purest form.
    card_frames = (
        0
        if folded
        else max(
            _CARD_FRAMES[importance],
            reading_frames(
                str(chapter.get("title") or ""),
                minimum=_CARD_FRAMES[importance],
                maximum=200,
            ),
        )
    )

    map_frames = 0
    if has_map:
        share = MAP_FOCUS_SHARE if style == "map_focus" else _MAP_SHARE[importance]
        map_frames = max(MAP_FRAMES_MIN, min(MAP_FRAMES_MAX, round(total * share)))
        if folded:
            # The card's time is not saved, it is handed to the map, which
            # is now where the caption has to be read.
            map_frames = min(MAP_FRAMES_MAX, map_frames + _CARD_FRAMES[importance])
        # The map may not eat the room the day's content actually needs -
        # and "actually" means asking the shots rather than guessing. An
        # earlier version reserved a single picture's floor and then found
        # the day opening on a hero, whose floor is nearly twice that, so
        # the day ran over anyway. Without any reserve, a day with nothing
        # to show was inflated by its own map, which is the exact opposite
        # of what a share was for.
        reserved = (
            MIN_GROUP_FRAMES
            if photo_count <= 0
            else sum(_minimum_for(kind) for kind, _, _ in _shot_list(style, photo_count))
        )
        map_frames = max(
            MAP_FRAMES_MIN, min(map_frames, total - card_frames - reserved)
        )

    scenes: list[dict[str, Any]] = []
    if card_frames:
        scenes.append(scene(SCENE_CHAPTER_CARD, [], card_frames))
    if map_frames:
        scenes.append(scene(SCENE_MAP_LEG, [], map_frames))

    if photo_count <= 0:
        # Not "keine Fotos vorhanden" on screen - that is a diagnostic and
        # belongs in a log. The day gets a written page instead, which is
        # also what makes a photo-less last day flow into the outro.
        # A written page IS the scene, so it is paid by what it says
        # rather than by what is left over. The leftover was how a long
        # day-without-photos text got the same seconds as a short one.
        text_frames = max(
            MIN_GROUP_FRAMES,
            reading_frames(caption),
            total - card_frames - map_frames,
        )
        scenes.append(scene(SCENE_TEXT, [], text_frames))
        return scenes

    # A caption longer than a caption is a page. Giving it to the photo
    # scene means a scrim over half the frame, a clamped sentence, and
    # the reported "Text läuft unten aus dem Bild" - so it gets a scene
    # of its own, before the pictures, and the pictures keep their room.
    long_caption = len(caption) > CAPTION_OVER_PHOTO_CHARS
    caption_frames = reading_frames(caption) if long_caption else 0

    chosen_clips = list(clips or [])
    clip_shots = _clip_shots(chosen_clips, importance)
    # A clip's length is not negotiable the way a photograph's is - it was
    # cut to a moment, and trimming it is cutting it again. So its
    # seconds come off the top and the photographs share what is left.
    clip_frames = sum(
        max(MIN_PHOTO_FRAMES, round(float(chosen_clips[indices[0]].get("frames") or 0)))
        for _, indices, _ in clip_shots
    )
    shots = _shot_list(style, photo_count)
    # The smaller of what the day is worth and what it can honestly fill.
    natural = sum(_NATURAL_FRAMES.get(kind, MIN_PHOTO_FRAMES) for kind, _, _ in shots)
    picture_budget = max(
        MIN_PHOTO_FRAMES,
        min(total - card_frames - map_frames - clip_frames - caption_frames, natural),
    )
    # Movement first, then the stills. A clip after the photographs reads
    # as an afterthought; a clip before them reads as arriving somewhere
    # and then looking around.
    if caption_frames:
        scenes.append(scene(SCENE_TEXT, [], caption_frames))
    for _, indices, _ in clip_shots:
        clip = chosen_clips[indices[0]]
        scenes.append(
            {
                "type": SCENE_CLIP,
                "chapter_id": chapter_id,
                "chapter_index": index,
                "frames": max(MIN_PHOTO_FRAMES, round(float(clip.get("frames") or 0))),
                "enter": enter,
                "photos": [],
                "clip": indices[0],
            }
        )
    for kind, indices, frames in _fit_to_budget(shots, picture_budget):
        scenes.append(scene(kind, indices, frames))

    if not caption and scenes:
        # Nothing to read anywhere in this chapter: the first scene keeps
        # it on screen a moment longer.
        scenes[0]["frames"] += 15
    return scenes


def _clip_shots(clips: list[dict[str, Any]], importance: str) -> list[tuple[str, list[int], float]]:
    """The video clips a day may show, as shots.

    How many depends on importance for the same reason photographs do: a
    transfer day is not the place for three films, and a major highlight
    with strong material should not be held to one.

    Clips are placed **before** the photographs of the day rather than
    after. Movement after stills reads as an afterthought; movement first
    and stills after reads as arriving somewhere and then looking around.

    The weight is high because a clip has to play at its own length -
    unlike a photograph, shortening it is cutting it, and a two-second
    fragment of a five-second moment is not the moment.
    """
    allowance = _CLIPS_BY_IMPORTANCE.get(importance, 1)
    return [
        (SCENE_CLIP, [index], 2.2)
        for index in range(min(len(clips), allowance))
    ]


def _shot_list(style: str, photo_count: int) -> list[tuple[str, list[int], float]]:
    """Which shots a day is made of, and their relative weights.

    Shape only - no durations. That separation is the point of this
    rewrite: how a day is *composed* depends on its style and how much
    material it has, while how long it *runs* is a property of the day
    itself. Deciding both in one pass is what made a picture buy its own
    seconds, and therefore what made a richer day only a longer one.

    The weights say how the day's time is shared, not how much it is. A
    hero is worth more than a plain still because it is meant to be
    looked at; a group is worth more than a single because there is more
    in it - but four pictures in one group still cost far less than four
    pictures in a row, which is what lets a day show more memories inside
    the same minute.
    """
    indices = list(range(photo_count))
    shots: list[tuple[str, list[int], float]] = []
    if not indices:
        return shots

    if style == "collage":
        # Everything grouped, in chunks a viewer can actually read.
        for position in range(0, len(indices), GROUP_SIZE):
            shots.append((SCENE_COLLAGE, indices[position : position + GROUP_SIZE], 1.5))
        return shots

    if style == "map_focus":
        # The map is the act. One picture closes the day, and anything
        # else the day has is grouped behind it rather than thrown away -
        # which is what the previous version claimed and did not do: it
        # took the first four of the rest and dropped the remainder, so a
        # map-focus day with seven pictures quietly showed five.
        shots.append((SCENE_HERO, indices[:1], 1.6))
        rest = indices[1:]
        if len(rest) == 1:
            shots.append((SCENE_PHOTO, rest, 1.0))
        else:
            for position in range(0, len(rest), GROUP_SIZE):
                shots.append((SCENE_COLLAGE, rest[position : position + GROUP_SIZE], 1.3))
        return shots

    rest = indices
    if style in ("hero", "compact") or len(indices) >= 3:
        # A day with real material opens on one picture rather than
        # starting straight into a sequence.
        shots.append((SCENE_HERO, rest[:1], 1.7))
        rest = rest[1:]

    singles = rest[:MAX_SINGLES_PER_CHAPTER]
    grouped = rest[MAX_SINGLES_PER_CHAPTER:]
    for position in singles:
        shots.append((SCENE_PHOTO, [position], 1.0))
    # Beyond a few stills in a row the day stops being a sequence and
    # becomes a slideshow, so the remainder is grouped.
    for position in range(0, len(grouped), GROUP_SIZE):
        shots.append((SCENE_COLLAGE, grouped[position : position + GROUP_SIZE], 1.4))
    return shots


def _minimum_for(kind: str) -> int:
    """The shortest a shot of this kind may be held."""
    return MIN_GROUP_FRAMES if kind in (SCENE_COLLAGE, SCENE_HERO) else MIN_PHOTO_FRAMES


def _compress(
    shots: list[tuple[str, list[int], float]], budget: int
) -> list[tuple[str, list[int], float]]:
    """Group singles until the day fits, instead of letting it run over.

    This is where "more pictures, not more minutes" actually happens. When
    the floors add up to more than the day is worth, the choice is between
    a longer day, shorter-than-readable stills, or showing the same
    memories together instead of one after another. The third is the only
    one that costs nothing: four pictures in a group take roughly the time
    of one and a half stills, and the viewer sees all four.

    Singles are merged from the back, so the day keeps its opening shot
    and loses its repetition rather than its shape.
    """
    working = list(shots)
    while sum(_minimum_for(kind) for kind, _, _ in working) > budget:
        singles = [i for i, (kind, _, _) in enumerate(working) if kind == SCENE_PHOTO]
        if len(singles) >= 2:
            last, before = singles[-1], singles[-2]
            merged = working[before][1] + working[last][1]
            working[before] = (SCENE_COLLAGE, merged, 1.4)
            del working[last]
            continue
        # No two singles left. Merge the last two shots of any kind
        # instead - a bigger group is still every picture on screen,
        # while overrunning by half a minute is a day pretending to be
        # more important than it is. Merging stops at twice the normal
        # group, beyond which nobody can read it.
        if len(working) >= 2 and len(working[-2][1]) + len(working[-1][1]) <= GROUP_SIZE * 2:
            merged = working[-2][1] + working[-1][1]
            working[-2] = (SCENE_COLLAGE, merged, 1.5)
            working.pop()
            continue
        # Nothing further can be merged without throwing a picture away,
        # and a day that is genuinely full is allowed to be longer than a
        # day that is not.
        break
    return working


def _fit_to_budget(
    shots: list[tuple[str, list[int], float]], budget: int
) -> list[tuple[str, list[int], int]]:
    """Divide a day's time among its shots, by weight and never below a floor.

    The budget may honestly not be enough: ten pictures cannot all be
    shown for two seconds inside nine seconds of film. When that happens
    the day runs over rather than showing flickers - the floors win. A
    day that is genuinely full is allowed to be longer than a day that is
    not, which is different from every day growing with its library.
    """
    if not shots:
        return []
    shots = _compress(shots, budget)
    total_weight = sum(weight for _, _, weight in shots) or 1.0
    fitted: list[tuple[str, list[int], int]] = []
    for kind, indices, weight in shots:
        share = round(budget * (weight / total_weight))
        fitted.append((kind, indices, max(_minimum_for(kind), share)))
    return fitted


def build_scene_plan(
    *,
    trip: dict[str, Any],
    chapters: list[dict[str, Any]],
    narrative: dict[str, Any] | None = None,
    outro_photos: list[dict[str, Any]] | None = None,
    map_context: dict[str, Any] | None = None,
    crew: dict[str, Any] | None = None,
    clips_by_chapter: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """The whole film as an ordered list of scenes with frame counts.

    ``chapters`` are the package's chapters - each already carrying the
    pictures that travelled with it - so the plan can address photos by
    position and the renderer never has to decide anything.
    """
    if not chapters:
        raise FilmPlanError("Ein Film ohne Kapitel kann nicht geplant werden")

    arc = narrative if isinstance(narrative, dict) else {}
    mapped = {
        str(entry.get("chapter_id") or "")
        for entry in ((map_context or {}).get("chapters") or [])
    }
    mapped.discard("")

    scenes: list[dict[str, Any]] = [
        {
            "type": SCENE_INTRO,
            "chapter_id": "",
            "chapter_index": -1,
            # An opening sentence needs longer than a title.
            "frames": INTRO_FRAMES + (30 if str(arc.get("opening") or "").strip() else 0),
            "enter": "rise",
            "photos": [],
        }
    ]
    members = list((crew or {}).get("members") or [])
    if members:
        # Who is travelling, before where. The journey reads as this
        # crew's story rather than as an export of trip data - and the
        # scene simply does not exist when there are no portraits, which
        # is better than a row of empty circles.
        scenes.append(
            {
                "type": SCENE_CREW,
                "chapter_id": "",
                "chapter_index": -1,
                "frames": CREW_FRAMES,
                "enter": "rise",
                "photos": [],
            }
        )
    if mapped:
        # "Here is where it begins." The whole travel area, so the trip
        # has a shape before it has a first day, with the camper standing
        # on the start - and deliberately NOT the route ahead. A film that
        # shows the whole journey in its first ten seconds has spent the
        # one thing a map can give it.
        scenes.append(
            {
                "type": SCENE_MAP_START,
                "chapter_id": "",
                "chapter_index": -1,
                "frames": MAP_START_FRAMES,
                "enter": "rise",
                "photos": [],
            }
        )
    for index, chapter in enumerate(chapters):
        scenes.extend(
            _chapter_scenes(
                chapter,
                photo_count=len(chapter.get("images") or []),
                index=index,
                has_map=str(chapter.get("chapter_id") or "") in mapped,
                clips=(clips_by_chapter or {}).get(str(chapter.get("chapter_id") or "")),
            )
        )
    if mapped:
        # The whole route at last. Everything before this showed only how
        # far the journey had got by that day.
        scenes.append(
            {
                "type": SCENE_MAP_FULL,
                "chapter_id": "",
                "chapter_index": -1,
                "frames": MAP_FULL_FRAMES,
                "enter": "settle",
                "photos": [],
            }
        )

    scenes.append(
        {
            "type": SCENE_OUTRO,
            "chapter_id": "",
            "chapter_index": -1,
            "frames": OUTRO_FRAMES + (30 if str(arc.get("closing") or "").strip() else 0),
            "enter": "settle",
            "photos": [],
        }
    )
    final = list(outro_photos or [])[:OUTRO_COLLAGE_PHOTOS]
    if len(final) >= 2:
        # A film that stops after the last day has not ended, it has run
        # out. The closing collage is the difference.
        scenes.append(
            {
                "type": SCENE_OUTRO_COLLAGE,
                "chapter_id": "",
                "chapter_index": -1,
                "frames": OUTRO_COLLAGE_FRAMES,
                "enter": "fade",
                "photos": [],
                "paths": [str(item.get("path") or "") for item in final if item.get("path")],
            }
        )

    return {
        "plan_version": PLAN_VERSION,
        "fps": FILM_FPS,
        "total_frames": sum(int(scene["frames"]) for scene in scenes),
        "scenes": scenes,
    }


def plan_seconds(plan: dict[str, Any]) -> float:
    return round(int(plan.get("total_frames") or 0) / FILM_FPS, 2)


def validate_scene_plan(plan: Any) -> dict[str, Any]:
    """Read a plan with the rules that produced it."""
    if not isinstance(plan, dict):
        raise FilmPlanError("Szenenplan ist kein Objekt")
    if plan.get("plan_version") != PLAN_VERSION:
        raise FilmPlanError(f"Unbekannte Planversion: {plan.get('plan_version')!r}")
    if plan.get("fps") != FILM_FPS:
        raise FilmPlanError("Der Szenenplan hat eine fremde Bildrate")
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise FilmPlanError("Szenenplan ohne Szenen")
    total = 0
    for scene in scenes:
        if not isinstance(scene, dict):
            raise FilmPlanError("Szene ist kein Objekt")
        if scene.get("type") not in SCENE_TYPES:
            raise FilmPlanError(f"Unbekannter Szenentyp: {scene.get('type')!r}")
        frames = scene.get("frames")
        if not isinstance(frames, int) or isinstance(frames, bool) or frames <= 0:
            raise FilmPlanError("Szene ohne gültige Länge")
        total += frames
    if total != plan.get("total_frames"):
        raise FilmPlanError("Die Gesamtlänge passt nicht zu den Szenen")
    return plan


__all__ = [
    "FILM_FPS",
    "OUTRO_COLLAGE_PHOTOS",
    "PLAN_VERSION",
    "PHOTO_WEIGHTS",
    "SCENE_TYPES",
    "FilmPlanError",
    "allocate_photos",
    "build_scene_plan",
    "plan_seconds",
    "readable_place",
    "readable_places",
    "validate_scene_plan",
]
