"""Three ways a film could be scored, held apart so one can be chosen.

The open product question is not "what should the music sound like" -
the style lock settles that - but *how many musical layers a travel film
actually wants*:

**single_score** - one coherent piece runs under the stretch of film.
The simplest thing that could work, and therefore the baseline that
every other option has to beat.

**layered_bed_accent** - a very restrained continuous atmosphere carries
the whole stretch, and a more characterful piece sits on top of it. The
hope is that the atmosphere holds quiet passages and transitions
together without turning into a second song.

**atmosphere_only** - the atmosphere alone. The control: it answers
whether a subtle layer already carries the film, or whether without a
musical statement the film simply sounds empty.

Nothing here decides which of the three is right. That is what the
listening test is for, and this module exists precisely so the answer is
not written into the code before it is known. A schema that assumed
every film has a continuous bed would have made the experiment
unfalsifiable - the architecture would have been the default and the
question would have quietly become "how loud should the bed be".

"Atmosphere" rather than "drone" throughout, in code and in the UI. A
drone is one technique for producing a continuous restrained layer; what
the product wants is the layer, and naming it after one way of making it
would tie the vocabulary to an implementation that may well change.
"""

from __future__ import annotations

from typing import Any

ARCHITECTURE_VERSION = 1

# The architectures under test. Neutral names: none of them is "the"
# architecture yet.
ARCH_SINGLE_SCORE = "single_score"
ARCH_LAYERED_BED_ACCENT = "layered_bed_accent"
ARCH_ATMOSPHERE_ONLY = "atmosphere_only"

ARCHITECTURES = (ARCH_SINGLE_SCORE, ARCH_LAYERED_BED_ACCENT, ARCH_ATMOSPHERE_ONLY)

# What a generated piece is FOR. A role is what gets bought; a variant
# is a way of combining roles. Keeping them apart is what lets two
# variants share one purchase.
ROLE_SCORE = "score"
ROLE_BED = "bed"
ROLE_ACCENT = "accent"

ROLES = (ROLE_SCORE, ROLE_BED, ROLE_ACCENT)

# How each role is asked for. English, alongside the style lock, and
# deliberately about FUNCTION rather than about notes: the provider is
# being told what job the piece has, and the style lock already says
# what it should sound like.
ROLE_DIRECTION: dict[str, str] = {
    ROLE_SCORE: (
        "A single continuous instrumental piece that can carry a whole "
        "sequence on its own. It has a clear musical identity: a gentle "
        "melodic line, quiet forward motion, room to breathe."
    ),
    ROLE_BED: (
        "A soft continuous atmospheric bed. Sustained harmonic texture, "
        "no lead melody, no drums, no percussion, no rhythmic figure, "
        "very restrained harmonic movement. It should sit unobtrusively "
        "underneath and never draw attention to itself, and it should "
        "stay even enough that any moment of it sounds like any other."
    ),
    ROLE_ACCENT: (
        "A characterful instrumental piece meant to sit ON TOP of a soft "
        "atmospheric bed. It carries the musical statement: a gentle "
        "melodic identity, acoustic guitar and soft cello in front. "
        "Leave the low end open and do not fill the whole frequency "
        "range - something quiet is already playing underneath."
    ),
}

# The mix, as gains relative to the loudest layer of the same variant.
#
# Two rules, both from listening rather than from arithmetic: in the
# layered variant the bed is background and frequency fill while the
# accent is the music, so the bed sits far below it; in the atmosphere
# variant the bed carries alone and may come up - but not so far that
# the background layer quietly becomes a lead track, which would be a
# fourth architecture nobody asked to test.
#
# These are the balance INSIDE a variant. Across variants the loudness
# match below is what makes the comparison fair.
_LAYERS: dict[str, tuple[tuple[str, float], ...]] = {
    ARCH_SINGLE_SCORE: ((ROLE_SCORE, 1.0),),
    ARCH_LAYERED_BED_ACCENT: ((ROLE_BED, 0.38), (ROLE_ACCENT, 1.0)),
    ARCH_ATMOSPHERE_ONLY: ((ROLE_BED, 0.72),),
}

# The variants of the comparison, in the order they are listened to.
VARIANT_A = "A"
VARIANT_B = "B"
VARIANT_C = "C"

_VARIANTS: dict[str, dict[str, Any]] = {
    VARIANT_A: {
        "architecture": ARCH_SINGLE_SCORE,
        "label": "A · nur Lyria",
        "question": "Reicht ein einzelnes gutes Musikstück schon?",
    },
    VARIANT_B: {
        "architecture": ARCH_LAYERED_BED_ACCENT,
        "label": "B · Atmosphäre + Akzent",
        "question": "Verbindet ein leises Klangbett Übergänge und ruhige Stellen?",
    },
    VARIANT_C: {
        "architecture": ARCH_ATMOSPHERE_ONLY,
        "label": "C · nur Atmosphäre",
        "question": "Trägt eine zurückhaltende Ebene allein - oder fehlt Richtung?",
    },
}

VARIANTS = (VARIANT_A, VARIANT_B, VARIANT_C)

# Where all three variants are brought to, so none of them wins the
# listening test merely by being louder. EBU R128 integrated loudness,
# with a true-peak ceiling well under full scale.
#
# This is deliberately applied to the FINISHED mix of each variant
# rather than to each asset: what a listener compares is the variant,
# and normalising the parts would have flattened exactly the internal
# balance the layered variant is being judged on.
#
# It has one honest consequence worth stating rather than hiding: the
# atmosphere-only variant is quiet material, so matching it to the same
# loudness raises it considerably. That is the fair comparison - "would
# this carry the film if it were at listening level" - and not the same
# question as "is a soft layer enough at ITS natural level".
TARGET_LUFS = -20.0
TRUE_PEAK_CEILING_DBTP = -1.5
LOUDNESS_RANGE = 11.0

# Preview only. A sixty-second cut out of the middle of a film has no
# reason to start or stop abruptly, and these fades exist for that and
# nothing else - they are NOT a decision about how the finished film's
# music begins and ends.
PREVIEW_FADE_IN_SECONDS = 1.5
PREVIEW_FADE_OUT_SECONDS = 2.5


class ArchitectureError(ValueError):
    """A variant or architecture that does not exist. Named, not guessed."""


def variant_layers(variant: str) -> list[dict[str, Any]]:
    """Which roles play in this variant, and at what relative gain."""
    entry = _VARIANTS.get(str(variant))
    if not entry:
        raise ArchitectureError(f"Unbekannte Variante: {variant!r}")
    architecture = entry["architecture"]
    return [
        {"role": role, "gain": gain, "architecture": architecture}
        for role, gain in _LAYERS[architecture]
    ]


def architecture_of(variant: str) -> str:
    entry = _VARIANTS.get(str(variant))
    if not entry:
        raise ArchitectureError(f"Unbekannte Variante: {variant!r}")
    return str(entry["architecture"])


def describe_variant(variant: str) -> dict[str, Any]:
    entry = _VARIANTS.get(str(variant))
    if not entry:
        raise ArchitectureError(f"Unbekannte Variante: {variant!r}")
    return {
        "variant": variant,
        "architecture": entry["architecture"],
        "label": entry["label"],
        "question": entry["question"],
        "layers": variant_layers(variant),
    }


def required_roles(variants: list[str] | tuple[str, ...]) -> list[str]:
    """Every role the chosen variants need, each named once.

    This is the whole reason roles and variants are separate things.
    Both the layered variant and the atmosphere variant want a bed, and
    they want *the same* bed - buying a second one so that the control
    variant could have its own would make the two variants differ by
    their material as well as by their architecture, which is precisely
    the comparison being avoided.
    """
    wanted: list[str] = []
    for variant in variants:
        for layer in variant_layers(variant):
            if layer["role"] not in wanted:
                wanted.append(layer["role"])
    return [role for role in ROLES if role in wanted]


def role_prompt(role: str, *, style_sentence: str, arc: str = "") -> str:
    """One request, built from the role's job and the shared style.

    The style sentence is identical across roles by construction. That
    is what makes the test a test of architecture: if the bed and the
    accent came from different style descriptions, a listener comparing
    them would be hearing two tastes rather than two layers.
    """
    direction = ROLE_DIRECTION.get(str(role))
    if not direction:
        raise ArchitectureError(f"Unbekannte Rolle: {role!r}")
    parts = [direction, style_sentence]
    if arc and role != ROLE_BED:
        # The bed deliberately gets no energy arc. A bed that follows
        # the film's shape is not a bed any more - it is a second score,
        # and then the layered variant would be testing two scores.
        parts.append(arc)
    return " ".join(part.strip() for part in parts if part and part.strip())


__all__ = [
    "ARCHITECTURES",
    "ARCHITECTURE_VERSION",
    "ARCH_ATMOSPHERE_ONLY",
    "ARCH_LAYERED_BED_ACCENT",
    "ARCH_SINGLE_SCORE",
    "LOUDNESS_RANGE",
    "PREVIEW_FADE_IN_SECONDS",
    "PREVIEW_FADE_OUT_SECONDS",
    "ROLES",
    "ROLE_ACCENT",
    "ROLE_BED",
    "ROLE_DIRECTION",
    "ROLE_SCORE",
    "TARGET_LUFS",
    "TRUE_PEAK_CEILING_DBTP",
    "VARIANTS",
    "VARIANT_A",
    "VARIANT_B",
    "VARIANT_C",
    "ArchitectureError",
    "architecture_of",
    "describe_variant",
    "required_roles",
    "role_prompt",
    "variant_layers",
]
