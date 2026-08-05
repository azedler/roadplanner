# Background music for the trip video export

This folder ships empty ON PURPOSE, and that no longer means a silent
video: without a file here, the export synthesises a calm chord bed itself
(`trip_music.py`). Nobody else holds rights in a pad this code describes,
so there is no licence claim to verify and no attribution to carry - which
is exactly why no third-party track is bundled.

Anything you put here wins over the generated bed. The export
(`trip_video_export.py`'s `pick_music_track()`) treats a missing or empty
folder as "use the generated bed" - never a crash or error.

To enable background music, add a small number of instrumental `.mp3`
files here (roughly 2-4 minutes each, calm/ambient enough to sit under the
on-screen narrative text without needing separate ducking logic). They must
be genuinely royalty-free or CC0/CC-BY licensed for redistribution as part
of this open-source integration - for example from:

- [Free Music Archive](https://freemusicarchive.org/) (filter by CC0/CC-BY)
- [Pixabay Music](https://pixabay.com/music/) (Pixabay Content License)
- an artist's own explicit royalty-free release

If a track requires attribution (CC-BY), note the required credit line in
this file next to the filename.

A track is picked deterministically per trip (a hash of the trip ID), so
the same trip always renders with the same track across repeated exports.

## Tracks

_(none yet)_
