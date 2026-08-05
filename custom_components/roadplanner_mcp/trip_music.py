"""Generate a calm instrumental bed for the trip video - no licence needed.

Live question: "Du kannst keine lizenzfreie Musik besorgen?" Downloading
one is not possible from the build environment, and bundling a file found
somewhere would mean trusting a licence claim nobody here can verify.

So the music is not fetched, it is SYNTHESISED: a slow chord pad built from
plain sine tones by ffmpeg itself. Nobody else holds rights in it, there is
no attribution to carry and no licence file to keep in sync - it is simply
output of this module. Musically it is deliberately modest: a soft
progression that sits under photos without competing with them.

A real track still wins: anything the user drops into ``assets/music`` is
used instead (see trip_video_export.pick_music_track).

Pure string building, exactly like trip_video.build_ffmpeg_filter_graph -
no subprocess, no I/O, fully testable without running ffmpeg.
"""

from __future__ import annotations

import hashlib

SAMPLE_RATE = 44_100
# Seconds per chord. Slow enough to feel calm, short enough that a
# two-minute reel still moves.
CHORD_SECONDS = 4.0
# One pass of the progression; it is looped to cover any video length.
_PROGRESSION = (0, 7, 9, 5)  # I - V - vi - IV in semitones from the root
_CHORD_INTERVALS = (0, 4, 7)  # major triad
_VOICE_GAINS = (0.30, 0.20, 0.16)
_BASS_GAIN = 0.22
# Comfortable roots (A3 to F4): low enough to stay in the background, high
# enough not to rumble on a phone speaker.
_ROOT_CHOICES = (220.00, 233.08, 246.94, 261.63, 277.18, 293.66, 311.13, 329.63)


def _semitone(frequency: float, steps: int) -> float:
    return frequency * (2 ** (steps / 12))


def root_frequency(trip_id: str) -> float:
    """Pick this trip's key deterministically - the same trip, the same bed."""
    digest = hashlib.sha256(str(trip_id or "").encode("utf-8")).hexdigest()
    return _ROOT_CHOICES[int(digest, 16) % len(_ROOT_CHOICES)]


def build_music_graph(
    trip_id: str, *, first_input_index: int, chord_seconds: float = CHORD_SECONDS
) -> tuple[list[str], str, str]:
    """Return (ffmpeg input args, filter graph, output label) for the bed.

    ``first_input_index`` is the index the generated inputs start at, so the
    caller can append them after its own image inputs.
    """
    root = root_frequency(trip_id)
    inputs: list[str] = []
    parts: list[str] = []
    chord_labels: list[str] = []
    index = first_input_index

    for chord_position, chord_step in enumerate(_PROGRESSION):
        chord_root = _semitone(root, chord_step)
        voice_labels: list[str] = []
        # A bass note an octave down gives the pad a floor.
        for voice, (interval, gain) in enumerate(
            [(-12, _BASS_GAIN)]
            + [
                (interval, gain)
                for interval, gain in zip(_CHORD_INTERVALS, _VOICE_GAINS)
            ]
        ):
            frequency = _semitone(chord_root, interval)
            inputs += [
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency:.3f}:duration={chord_seconds:.3f}"
                f":sample_rate={SAMPLE_RATE}",
            ]
            label = f"m{chord_position}v{voice}"
            parts.append(f"[{index}:a]volume={gain:.3f}[{label}]")
            voice_labels.append(f"[{label}]")
            index += 1
        # Long crossfading envelopes so chords breathe into each other
        # instead of clicking.
        fade = min(1.5, chord_seconds / 2)
        parts.append(
            "".join(voice_labels)
            + f"amix=inputs={len(voice_labels)}:normalize=0,"
            f"afade=t=in:st=0:d={fade:.3f},"
            f"afade=t=out:st={chord_seconds - fade:.3f}:d={fade:.3f}"
            f"[mc{chord_position}]"
        )
        chord_labels.append(f"[mc{chord_position}]")

    progression_samples = int(chord_seconds * len(_PROGRESSION) * SAMPLE_RATE)
    parts.append(
        "".join(chord_labels)
        + f"concat=n={len(chord_labels)}:v=0:a=1,"
        # Loop the whole progression; the caller's -shortest trims it to the
        # length of the slideshow.
        f"aloop=loop=-1:size={progression_samples},"
        "tremolo=f=0.12:d=0.18,"
        "aecho=0.8:0.85:700|1100:0.28|0.18,"
        # A predictable level: sine voices summed by hand land anywhere,
        # and background music that is barely audible is as useless as one
        # that drowns the pictures.
        "loudnorm=I=-24:TP=-3:LRA=7,"
        "aformat=sample_fmts=fltp:channel_layouts=stereo:"
        f"sample_rates={SAMPLE_RATE}"
        "[music]"
    )
    return inputs, ";".join(parts), "[music]"
