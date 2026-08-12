"""How many pixels a photograph actually needs, slot by slot.

The film is authored on a logical surface of 1280x720 and a render
profile scales that surface as one image. That keeps the film identical
at every size - and it means a photograph prepared for the old fixed
ceiling is drawn at whatever the profile asks for, however far that is
above what the file contains.

Measured on the first 1440p check, against the composition's own layout
arithmetic:

    Hero, landscape     needs 2790 px   had 900   3.10x upscale
    Full frame, upright needs 1526 px   had 900   1.70x
    Collage, 2-up       needs 1120 px   had 900   1.24x
    Collage, 4-up       needs  648 px   had 900   0.72x
    Collage, 9-up       needs  416 px   had 900   0.46x

Which is exactly what was visible: the large single pictures looked
soft and the small tiles did not. The four-, six- and nine-up tiles are
already given more pixels than they draw.

Three things decide the number, and all three are read from the
rendering rather than assumed:

**The box.** The share of the frame the picture occupies, from the same
grid arithmetic `collageLayout` uses in the composition.

**The fit.** Only a landscape picture in a full frame is `cover`;
everything else is `contain` - a wall of memories may not be cropped,
and an upright photograph in a 16:9 frame would lose its sky and its
subject. `contain` binds on the other axis, so an upright hero needs
little more than half what a landscape one does.

**The movement.** The Ken Burns span in the composition is 1.09 for a
hero and 1.06 for any other photograph. That is the whole reserve
needed: nothing else moves a picture beyond its frame. A guessed
"1.15 to 1.30" would have been either wasteful or short, and neither
could be checked against anything.

What this module deliberately does NOT do is change what is in the film.
A render profile decides pixels. It does not touch the picture chosen,
the order, the crop, the timing or the story - the scene plan is read
here and never written.
"""

from __future__ import annotations

from typing import Any

from .render_profiles import DESIGN_HEIGHT, DESIGN_WIDTH, render_profile
from .trip_film_plan import (
    SCENE_CHAPTER_CARD,
    SCENE_COLLAGE,
    SCENE_HERO,
    SCENE_OUTRO_COLLAGE,
    SCENE_PHOTO,
    SCENE_TEXT,
)

# The collage grid, from the composition. A test reads both files and
# compares, because a tile that is a quarter of the frame on one side and
# a ninth on the other is this project's oldest bug in a new place.
COLLAGE_MARGIN = 5.0
COLLAGE_GUTTER = 2.5
COLLAGE_CAPTION_ROOM = 20.0

# The Ken Burns span, measured rather than chosen. Anything a picture is
# scaled by during its scene has to exist in the file beforehand.
ZOOM_HERO = 1.09
ZOOM_PHOTO = 1.06

# Never below this, whatever the arithmetic says. A picture that ends up
# tiny in one film is reused in another at a different size, and the
# cache is keyed by the target - so a floor keeps a thumbnail from
# becoming the only variant anybody ever has.
MIN_TARGET_EDGE = 480

# And never above: past this a photograph costs decode time and package
# bytes for detail no profile draws. 4K full frame with the hero zoom is
# 4186, so this clears the largest real case with room.
MAX_TARGET_EDGE = 4400


def collage_columns(count: int) -> int:
    """Mirrors `collageColumns` in the composition."""
    if count <= 1:
        return 1
    if count <= 4:
        return 2
    if count <= 6:
        return 3
    return 4


def collage_tile_fraction(count: int) -> tuple[float, float]:
    """What share of the frame one tile of an n-up collage occupies."""
    columns = collage_columns(max(1, count))
    rows = -(-max(1, count) // columns)
    width = (100.0 - COLLAGE_MARGIN * 2 - COLLAGE_GUTTER * (columns - 1)) / columns
    height = (
        100.0 - COLLAGE_MARGIN * 2 - COLLAGE_CAPTION_ROOM - COLLAGE_GUTTER * (rows - 1)
    ) / rows
    return width / 100.0, height / 100.0


def slot_box(scene_type: str, photo_count: int, profile_id: str) -> tuple[int, int]:
    """The largest box this picture is ever drawn into, in real pixels."""
    profile = render_profile(profile_id)
    width, height = int(profile["width"]), int(profile["height"])
    if scene_type in (SCENE_COLLAGE, SCENE_OUTRO_COLLAGE):
        share_w, share_h = collage_tile_fraction(photo_count)
        return max(1, round(width * share_w)), max(1, round(height * share_h))
    # Everything else that carries a photograph fills the frame. A
    # chapter card and a text scene put words over it; the picture
    # underneath is still full size.
    return width, height


def slot_fit(scene_type: str, orientation: str) -> str:
    """`cover` or `contain`, exactly as the composition decides it."""
    if scene_type in (SCENE_COLLAGE, SCENE_OUTRO_COLLAGE):
        return "contain"
    return "contain" if str(orientation) == "portrait" else "cover"


def slot_zoom(scene_type: str) -> float:
    return ZOOM_HERO if scene_type == SCENE_HERO else ZOOM_PHOTO


def required_edge(
    *,
    scene_type: str,
    photo_count: int,
    profile_id: str,
    source_width: int,
    source_height: int,
) -> int:
    """How long this picture's longest edge has to be, for this slot.

    Derived from the picture's own shape rather than from the slot alone:
    with `contain` the binding axis is the other one, so an upright
    photograph in a full frame needs little more than half of what a
    landscape one needs. Reading the shape here is free - the file has
    been decoded by the time this is asked.
    """
    box_w, box_h = slot_box(scene_type, photo_count, profile_id)
    width = max(1, int(source_width))
    height = max(1, int(source_height))
    orientation = "portrait" if height > width else "landscape"
    fit = slot_fit(scene_type, orientation)
    scale = (
        max(box_w / width, box_h / height)
        if fit == "cover"
        else min(box_w / width, box_h / height)
    )
    longest = max(width, height) * scale * slot_zoom(scene_type)
    return int(min(MAX_TARGET_EDGE, max(MIN_TARGET_EDGE, round(longest))))


def photo_slots(scene_plan: dict[str, Any]) -> dict[tuple[str, int], tuple[str, int]]:
    """For every picture the film uses: which scene shows it, and with how many.

    Keyed by chapter and the picture's position in that chapter, which is
    what the export counts by. A picture that appears in two scenes -
    a hero that is also in the day's collage - keeps the larger claim,
    because it has to satisfy both.
    """
    found: dict[tuple[str, int], tuple[str, int]] = {}
    for scene in scene_plan.get("scenes") or []:
        indices = scene.get("photos") or []
        if not indices:
            continue
        scene_type = str(scene.get("type") or "")
        chapter_id = str(scene.get("chapter_id") or "")
        count = len(indices)
        for index in indices:
            if not isinstance(index, int) or isinstance(index, bool):
                continue
            key = (chapter_id, index)
            previous = found.get(key)
            # Fewer pictures in a scene means a bigger box for each, so
            # the smaller count wins. Not "the last scene wins": that
            # would depend on the order scenes happen to be listed in.
            if previous is None or count < previous[1]:
                found[key] = (scene_type, count)
    return found


# Which scene types carry a photograph at all. Named so a new one has to
# be added here deliberately rather than silently falling into the
# full-frame default.
PHOTO_SCENES = (
    SCENE_HERO,
    SCENE_PHOTO,
    SCENE_COLLAGE,
    SCENE_OUTRO_COLLAGE,
    SCENE_CHAPTER_CARD,
    SCENE_TEXT,
)

__all__ = [
    "COLLAGE_CAPTION_ROOM",
    "COLLAGE_GUTTER",
    "COLLAGE_MARGIN",
    "MAX_TARGET_EDGE",
    "MIN_TARGET_EDGE",
    "PHOTO_SCENES",
    "ZOOM_HERO",
    "ZOOM_PHOTO",
    "collage_columns",
    "collage_tile_fraction",
    "photo_slots",
    "required_edge",
    "slot_box",
    "slot_fit",
    "slot_zoom",
]
