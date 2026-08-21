"""RP-415: "Bilder je Kapitel" exists once, and every reader agrees.

The film could not be started at all for three days on a live system.
The selection handed a chapter fifteen pictures - its own ceiling is the
maximum of ``PHOTO_CAPS_BY_IMPORTANCE``, eighteen - and the package
builder refused the fifteenth, because it carried a hand-written
``MAX_PHOTOS_PER_CHAPTER = 14`` beside it. The message named neither
number, neither the chapter, and pointed at the renderer app, which was
running perfectly.

That is this project's most repeated fault (one number, two places, one
side raised) in its worst form so far: FOUR readers of the same rule, in
two deployables.

    film_photo_allocation.PHOTO_CAPS_BY_IMPORTANCE  the decision
    story_context_builder.MEDIA_PER_CHAPTER         what a chapter carries
    travel_story_manifest.MAX_MEDIA_PER_CHAPTER     what the manifest keeps
    trip_film_package.MAX_PHOTOS_PER_CHAPTER        what the package accepts
    protocol.mjs MAX_FILM_PHOTOS_PER_CHAPTER        what the renderer accepts

So this file reads all five - the real values, by importing the modules
and by parsing the renderer's constant - and requires them to be the same
number. Not "compatible": the same. A ceiling that a later reader lowers
is a film that cannot be rendered; one that a later reader raises is a
picture silently dropped.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import re
import sys
import types

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "roadplanner_mcp"
PROTOCOL = ROOT / "apps" / "roadplanner_renderer" / "src" / "protocol.mjs"
PACKAGE_NAME = "rp_film_cap"

if PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(PACKAGE_ROOT)]
    sys.modules[PACKAGE_NAME] = package


def load(name: str):
    full = f"{PACKAGE_NAME}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, PACKAGE_ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


def verify_every_reader_of_the_cap_says_the_same_number() -> None:
    allocation = load("film_photo_allocation")
    decided = max(allocation.PHOTO_CAPS_BY_IMPORTANCE.values())

    package = load("trip_film_package")
    assert package.MAX_PHOTOS_PER_CHAPTER == decided, (
        f"Das Renderpaket akzeptiert {package.MAX_PHOTOS_PER_CHAPTER} Bilder "
        f"je Kapitel, die Auswahl vergibt bis zu {decided}. Genau diese "
        "Differenz machte jeden Film unstartbar."
    )

    # The two story modules import the same table and derive from it; read
    # their real values rather than trusting that they still do.
    manifest = load("travel_story_manifest")
    assert manifest.MAX_MEDIA_PER_CHAPTER == decided, manifest.MAX_MEDIA_PER_CHAPTER

    # story_context_builder pulls in the assistant stack, so its constant
    # is read from the source instead of by importing the module - the
    # DERIVATION is what matters here, not a literal.
    source = (PACKAGE_ROOT / "story_context_builder.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    found = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "MEDIA_PER_CHAPTER"
            for target in node.targets
        ):
            found = ast.unparse(node.value)
    assert found == "max(PHOTO_CAPS_BY_IMPORTANCE.values())", (
        "story_context_builder leitet die Obergrenze nicht mehr aus der "
        f"Cap-Tabelle ab, sondern aus: {found}"
    )


def verify_the_renderer_accepts_what_the_integration_sends() -> None:
    """The other deployable. It refuses a chapter it considers too full.

    Raising the integration alone would only have MOVED the outage: the
    package would build, and the renderer would answer "Kapitel mit zu
    vielen Bildern" instead.
    """
    decided = max(load("film_photo_allocation").PHOTO_CAPS_BY_IMPORTANCE.values())
    protocol = PROTOCOL.read_text(encoding="utf-8")
    match = re.search(
        r"export const MAX_FILM_PHOTOS_PER_CHAPTER = (\d+);", protocol
    )
    assert match, "MAX_FILM_PHOTOS_PER_CHAPTER fehlt im Renderer"
    assert int(match.group(1)) == decided, (
        f"Renderer akzeptiert {match.group(1)} Bilder je Kapitel, die "
        f"Integration vergibt bis zu {decided}"
    )

    # The filename pattern encodes the same ceiling and has eaten
    # pictures twice by lagging behind it.
    pattern = re.search(r"FILM_PHOTO_RE = /(.+?)/;", protocol)
    assert pattern, "FILM_PHOTO_RE fehlt im Renderer"
    photo_re = pattern.group(1)
    compiled = re.compile(photo_re.replace("\\/", "/"))
    assert compiled.match(f"photos/c00-{decided}.jpg"), (
        f"Der Renderer erkennt den {decided}. Bildplatz nicht: {photo_re}"
    )


def verify_the_addon_version_moved_with_the_protocol() -> None:
    """The add-on version IS the contract between the two deployables.

    A protocol change without a version bump publishes no image, so the
    integration would send eighteen pictures to a renderer still refusing
    fifteen - the same outage, now invisible because both repositories
    look correct.
    """
    config = (ROOT / "apps" / "roadplanner_renderer" / "config.yaml").read_text(
        encoding="utf-8"
    )
    package_json = (ROOT / "apps" / "roadplanner_renderer" / "package.json").read_text(
        encoding="utf-8"
    )
    in_config = re.search(r'^version:\s*"([^"]+)"', config, re.MULTILINE)
    in_package = re.search(r'"version":\s*"([^"]+)"', package_json)
    assert in_config and in_package, (in_config, in_package)
    assert in_config.group(1) == in_package.group(1), (
        f"Add-on-Version: config.yaml {in_config.group(1)}, "
        f"package.json {in_package.group(1)} - eine Seite wurde vergessen"
    )


def verify_the_package_refuses_exactly_one_past_the_ceiling() -> None:
    """The behaviour the whole outage was made of, pinned at the boundary."""
    package = load("trip_film_package")
    cap = package.MAX_PHOTOS_PER_CHAPTER
    assert package.photo_filename(0, cap).endswith(f"c00-{cap}.jpg")
    try:
        package.photo_filename(0, cap + 1)
    except Exception as err:  # RenderPackageError
        assert "Bildposition" in str(err), err
    else:
        raise AssertionError("das Paket nimmt jetzt mehr Bilder als die Obergrenze")


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Film photo cap tests passed.")


if __name__ == "__main__":
    main()
