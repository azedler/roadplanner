"""Which stops a film is *about*, and which ones merely happened.

From the live acceptance of `reise(8)`: a fast-food stop was treated as a
day's headline. That is not a naming bug and it does not get a name-based
fix - filtering the string "MAX Burgers" would leave the next petrol
station, pharmacy and car park to be discovered one live film at a time.

The real distinction is between two kinds of stop that a roadbook stores
identically:

- **Narrative stops.** The moose park, the Wolfsschanze, the Arctic
  Circle, the ferry, a pitch somebody chose for the view. These are what
  the journey was; a day is named after them and the map labels them.
- **Functional stops.** Fuel, groceries, a service point, a car park, a
  meal on the way. They are real, they belong in the roadbook, and they
  are why a day worked - but they are not what anybody remembers.

Both stay in the data. Only prominence differs.

Why not "type" alone
--------------------

A roadbook's `type` is filled in by whoever entered the stop and by the
assistant, so it is right often and absent sometimes. So the type decides
when it is there and useful, and otherwise a **conservative** reading of
the name fills in - conservative meaning: an unrecognised stop stays
`unknown` and keeps ordinary prominence. Wrongly demoting a real memory
is much worse than leaving a supermarket at normal weight, because the
first loses something from the film and the second only fails to improve
it.

Why prominence is a number and not a flag
-----------------------------------------

Because the answer depends on more than the category. A functional stop
where somebody wrote three sentences and took twelve photographs was
evidently not merely functional - the day says so - and the rule has to
be able to hear that. So a category sets the starting point and the
evidence moves it: what the person wrote, what the day is worth, how
many pictures there are, and an explicit override, which always wins.
"""

from __future__ import annotations

from typing import Any

# The categories, ordered from "this is the journey" to "this is
# logistics". Kept as data rather than as branching so that a new kind of
# stop is a new entry, not a new code path.
CATEGORY_NARRATIVE = "narrative"
CATEGORY_DESTINATION = "destination"
CATEGORY_ACTIVITY = "activity"
CATEGORY_SCENIC = "scenic"
CATEGORY_OVERNIGHT = "overnight"
CATEGORY_TRANSPORT = "transport"
CATEGORY_FOOD = "food"
CATEGORY_SHOPPING = "shopping"
CATEGORY_SERVICE = "service"
CATEGORY_FUNCTIONAL = "functional"
CATEGORY_UNKNOWN = "unknown"

CATEGORIES = (
    CATEGORY_NARRATIVE,
    CATEGORY_DESTINATION,
    CATEGORY_ACTIVITY,
    CATEGORY_SCENIC,
    CATEGORY_OVERNIGHT,
    CATEGORY_TRANSPORT,
    CATEGORY_FOOD,
    CATEGORY_SHOPPING,
    CATEGORY_SERVICE,
    CATEGORY_FUNCTIONAL,
    CATEGORY_UNKNOWN,
)

# The categories a day may be named after and the map may label large.
# Everything else can still appear - it simply does not lead.
STORY_CATEGORIES = frozenset(
    {
        CATEGORY_NARRATIVE,
        CATEGORY_DESTINATION,
        CATEGORY_ACTIVITY,
        CATEGORY_SCENIC,
        CATEGORY_OVERNIGHT,
    }
)

# Stops that exist so the journey could continue.
FUNCTIONAL_CATEGORIES = frozenset(
    {
        CATEGORY_FOOD,
        CATEGORY_SHOPPING,
        CATEGORY_SERVICE,
        CATEGORY_FUNCTIONAL,
    }
)

# What a roadbook's own `type` means here. The left side is what the
# assistant and the forms actually write.
_TYPE_CATEGORY = {
    "start": CATEGORY_TRANSPORT,
    "origin": CATEGORY_TRANSPORT,
    "waypoint": CATEGORY_TRANSPORT,
    "break": CATEGORY_FUNCTIONAL,
    "border": CATEGORY_TRANSPORT,
    "ferry": CATEGORY_NARRATIVE,
    "destination": CATEGORY_DESTINATION,
    "sightseeing": CATEGORY_NARRATIVE,
    "attraction": CATEGORY_NARRATIVE,
    "viewpoint": CATEGORY_SCENIC,
    "nature": CATEGORY_SCENIC,
    "beach": CATEGORY_SCENIC,
    "hike": CATEGORY_ACTIVITY,
    "activity": CATEGORY_ACTIVITY,
    "museum": CATEGORY_NARRATIVE,
    "overnight": CATEGORY_OVERNIGHT,
    "campsite": CATEGORY_OVERNIGHT,
    "camping": CATEGORY_OVERNIGHT,
    "stellplatz": CATEGORY_OVERNIGHT,
    "wildcamp": CATEGORY_OVERNIGHT,
    "accommodation": CATEGORY_OVERNIGHT,
    "restaurant": CATEGORY_FOOD,
    "food": CATEGORY_FOOD,
    "cafe": CATEGORY_FOOD,
    "shopping": CATEGORY_SHOPPING,
    "supermarket": CATEGORY_SHOPPING,
    "pharmacy": CATEGORY_SERVICE,
    "fuel": CATEGORY_SERVICE,
    "charging": CATEGORY_SERVICE,
    "service": CATEGORY_SERVICE,
    "water": CATEGORY_SERVICE,
    "waste": CATEGORY_SERVICE,
    "laundry": CATEGORY_SERVICE,
    "repair": CATEGORY_SERVICE,
    "parking": CATEGORY_FUNCTIONAL,
    "rest": CATEGORY_FUNCTIONAL,
}

# Generic words that identify a functional stop when the type is missing.
# Deliberately **no brand names**: a list of chains is the string filter
# this module exists instead of, and it would be wrong the first time
# somebody stops at a chain nobody here has heard of. These are the words
# that describe the *kind* of place in the languages this trip travels
# through.
#
# Also deliberately narrow. Anything not listed stays `unknown`, and
# unknown keeps ordinary prominence - a missed supermarket costs the film
# nothing, a demoted memory costs it something.
_FUNCTIONAL_WORDS = {
    CATEGORY_SHOPPING: (
        "supermarkt",
        "supermarket",
        "einkaufszentrum",
        "lebensmittel",
        "grocery",
        "shopping centre",
        "shopping center",
    ),
    CATEGORY_SERVICE: (
        "tankstelle",
        "tankstation",
        "petrol station",
        "gas station",
        "bensinstation",
        "apotheke",
        "pharmacy",
        "waschsalon",
        "laundromat",
        "entsorgung",
        "dumpstation",
        "frischwasser",
        "servicestation",
        "werkstatt",
    ),
    CATEGORY_FOOD: (
        "schnellrestaurant",
        "fast food",
        "imbiss",
        "drive-in",
        "drive-thru",
    ),
    CATEGORY_FUNCTIONAL: (
        "parkplatz",
        "rastplatz",
        "raststaette",
        "raststätte",
        "rasthof",
        "autohof",
        "picknickplatz",
        "lay-by",
        "car park",
    ),
}

# How long a word has to be before a plain substring match is safe. Below
# this it must be a whole word: "parking" inside "Parkinghallen" is fine,
# but a six-letter fragment inside a longer German name is how a place
# called something innocent gets filed as a car park.
_COMPOUND_LENGTH = 8

# Names that are not names: a router's answer for a road it does not know,
# and address fragments. A stop whose only name is one of these is better
# with no label at all than with a database row on screen.
# NOTE: written already folded. `str.casefold()` turns "ß" into "ss", so a
# pattern spelled with "ß" would never match a folded name - which is
# exactly how "Unbenannte Straße" slipped through the first version.
_NON_NAMES = (
    "unnamed rd",
    "unnamed road",
    "unbenannte strasse",
    "unnamed",
    "unknown road",
    "no name",
    "ohne namen",
)

# How prominent a stop of each category starts out. 1.0 leads a day; below
# `LEAD_THRESHOLD` it may appear but not headline.
_BASE_PROMINENCE = {
    CATEGORY_NARRATIVE: 1.0,
    CATEGORY_DESTINATION: 0.95,
    CATEGORY_ACTIVITY: 0.85,
    CATEGORY_SCENIC: 0.8,
    CATEGORY_OVERNIGHT: 0.7,
    CATEGORY_UNKNOWN: 0.6,
    CATEGORY_TRANSPORT: 0.35,
    CATEGORY_FOOD: 0.2,
    CATEGORY_SHOPPING: 0.15,
    CATEGORY_SERVICE: 0.15,
    CATEGORY_FUNCTIONAL: 0.15,
}

LEAD_THRESHOLD = 0.55
LABEL_THRESHOLD = 0.3


def _fold(text: Any) -> str:
    return " ".join(str(text or "").casefold().split())


def classify(stop: dict[str, Any]) -> str:
    """What kind of stop this is. Never raises, never guesses wildly.

    The declared type wins when it says something. Otherwise the name is
    read, and only for the handful of things that are unmistakably
    logistics. Everything else is `unknown`, which is treated as an
    ordinary stop rather than as a demoted one.
    """
    if not isinstance(stop, dict):
        return CATEGORY_UNKNOWN
    # An explicit decision by a person outranks everything, including the
    # type: somebody who marked a car park as the point of the day knows
    # something the categories do not.
    override = _fold(stop.get("story_category") or stop.get("category"))
    if override in CATEGORIES:
        return override

    # "kind" is what the curation brief calls it; "type" is what the
    # roadbook calls it. Same field, two spellings, and a classifier
    # that knew only one of them would silently see nothing.
    declared = _fold(stop.get("type") or stop.get("kind"))
    if declared in _TYPE_CATEGORY:
        return _TYPE_CATEGORY[declared]

    name = _fold(stop.get("story_name") or stop.get("name"))
    for category, words in _FUNCTIONAL_WORDS.items():
        for word in words:
            if _names_a(name, word):
                return category
    return CATEGORY_UNKNOWN


def _names_a(name: str, word: str) -> bool:
    """Whether a name says it is this kind of place.

    German writes "Tankstelle" and "Autohof" as one word, so a long word
    may match inside a longer one. A short word has to stand on its own -
    otherwise a fragment inside an ordinary place name files a memory as
    logistics, which is the expensive direction.
    """
    if " " in word or len(word) >= _COMPOUND_LENGTH:
        return word in name
    return word in name.replace("-", " ").replace("/", " ").split()


def is_functional(stop: dict[str, Any]) -> bool:
    """Whether this stop exists so the journey could continue."""
    return classify(stop) in FUNCTIONAL_CATEGORIES


def has_no_real_name(stop: dict[str, Any]) -> bool:
    """Whether the only name available is a router's placeholder.

    ``Unnamed Rd, 12345 Municipality`` is what a geocoder says when it
    does not know. On screen it reads as a fault in the film rather than
    as a fact about the journey, so nothing is better.
    """
    name = _fold(stop.get("story_name") or stop.get("name"))
    if not name:
        return True
    return any(marker in name for marker in _NON_NAMES)


def prominence(
    stop: dict[str, Any],
    *,
    day_importance: str = "normal",
    story_text: str = "",
    media_count: int = 0,
) -> float:
    """How much room this stop has earned, from 0 to 1.

    The category is the starting point; the evidence moves it. A stop
    somebody wrote about, or photographed a dozen times, was evidently
    not merely functional however it is filed - and on an unimportant day
    even a real sight is not the headline of the film.
    """
    if not isinstance(stop, dict):
        return 0.0
    # An explicit pin is an answer, not an input to a formula.
    pinned = _fold(stop.get("story_prominence"))
    if pinned == "high":
        return 1.0
    if pinned == "low":
        return 0.05

    category = classify(stop)
    score = _BASE_PROMINENCE.get(category, _BASE_PROMINENCE[CATEGORY_UNKNOWN])

    # Being named in the day's own story is the strongest signal there
    # is: somebody wrote it down. It lifts a functional stop out of the
    # basement without turning it into a headline on its own.
    name = _fold(stop.get("story_name") or stop.get("name"))
    if name and _mentioned(name, story_text):
        score += 0.3

    # Somebody's own words about this stop.
    if _fold(stop.get("story_note") or stop.get("notes")):
        score += 0.15

    # Photographs are evidence of attention. Diminishing, because twelve
    # pictures of a car park is still a car park.
    if media_count >= 3:
        score += 0.1
    if media_count >= 8:
        score += 0.1

    if day_importance == "major_highlight":
        score += 0.1
    elif day_importance == "transition":
        score -= 0.1

    # A stop with no usable name cannot lead anything, whatever it scores.
    if has_no_real_name(stop):
        score = min(score, LABEL_THRESHOLD - 0.01)

    return max(0.0, min(1.0, round(score, 3)))


def _mentioned(name: str, story_text: str) -> bool:
    """Whether the day's own text talks about this place.

    Matched on the distinctive words of the name rather than the whole
    string, because a story says "an der Wolfsschanze" and the stop is
    called "Wolfsschanze (Gierloz)".
    """
    text = _fold(story_text)
    if not text or not name:
        return False
    words = [word for word in name.replace("-", " ").split() if len(word) >= 5]
    return any(word in text for word in words)


def may_lead_day(
    stop: dict[str, Any],
    *,
    day_importance: str = "normal",
    story_text: str = "",
    media_count: int = 0,
) -> bool:
    """Whether this stop may name a day or headline its map."""
    return (
        prominence(
            stop,
            day_importance=day_importance,
            story_text=story_text,
            media_count=media_count,
        )
        >= LEAD_THRESHOLD
    )


def rank_stops(
    stops: list[dict[str, Any]],
    *,
    day_importance: str = "normal",
    story_text: str = "",
    media_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """The day's stops, most film-worthy first.

    Order is by earned prominence and then by the order they happened -
    never by name and never by position on a screen, because both of
    those change while the same day stays the same day.
    """
    counts = media_counts or {}
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for index, stop in enumerate(stops or []):
        if not isinstance(stop, dict):
            continue
        score = prominence(
            stop,
            day_importance=day_importance,
            story_text=story_text,
            media_count=int(counts.get(str(stop.get("id") or ""), 0)),
        )
        scored.append((score, index, stop))
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [
        {
            **stop,
            "story_category": classify(stop),
            "story_prominence_score": score,
            "may_lead": score >= LEAD_THRESHOLD,
            "may_label": score >= LABEL_THRESHOLD and not has_no_real_name(stop),
        }
        for score, _index, stop in scored
    ]


__all__ = [
    "CATEGORIES",
    "CATEGORY_ACTIVITY",
    "CATEGORY_DESTINATION",
    "CATEGORY_FOOD",
    "CATEGORY_FUNCTIONAL",
    "CATEGORY_NARRATIVE",
    "CATEGORY_OVERNIGHT",
    "CATEGORY_SCENIC",
    "CATEGORY_SERVICE",
    "CATEGORY_SHOPPING",
    "CATEGORY_TRANSPORT",
    "CATEGORY_UNKNOWN",
    "FUNCTIONAL_CATEGORIES",
    "LABEL_THRESHOLD",
    "LEAD_THRESHOLD",
    "STORY_CATEGORIES",
    "classify",
    "has_no_real_name",
    "is_functional",
    "may_lead_day",
    "prominence",
    "rank_stops",
]
