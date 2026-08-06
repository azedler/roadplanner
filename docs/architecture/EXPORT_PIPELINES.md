# Export pipelines: PDF trip summary and MP4 trip video

Written for an external architecture review. It describes how the two
export paths actually work today, why each non-obvious decision was made,
and where the known weak points are. Nothing here is aspirational — every
statement below reflects code in `custom_components/roadplanner_mcp/`.

**Context that shapes everything:** this is a Home Assistant custom
integration. It runs inside the HA event loop, on hardware that ranges from
a Raspberry Pi to a NUC, and it is operated almost exclusively from a phone
on mobile data, frequently while the user is actually travelling. Those
three facts explain most of the design.

---

## 1. Shared foundation

### 1.1 Data source

Both exports read one snapshot:

```
manager.async_get_assistant_payload(trip_id)
  → {"summary": {"trip": {...}, "revision": N, "total_distance_km": …},
     "days": {"days": [ {..., "stops": [...], "details": {...}}, … ]}}
```

The trip itself lives as JSON documents on disk (one per day plus a trip
document) behind a revision counter; every mutation is optimistic-locked on
`expected_revision`. The exports are pure readers — they never mutate the
trip. The one exception is the summary generator (§4), which is a separate
service precisely so that the exports stay read-only.

### 1.2 Photo selection — one curator, three consumers

`trip_export_photos.async_fetch_day_photos` is shared by the PDF, the video
and the summary generator. It calls
`media_intelligence.select_media_highlights`, the same function the panel
uses for its "5 Highlights aus 20 Fotos" display: duplicate collapse,
burst suppression (4 s / 30 min windows), and a diverse selection across
10-minute buckets.

*Why:* the exports previously sorted by a quality score while the panel
curated. Same trip, two different selections, and the user noticed. Sharing
the curator is the only structural guarantee that they cannot drift again.

### 1.3 Photo fetching — thumbnails first, deliberately

`async_fetch_media_photo` tries, in order:

1. local cache (`media_cache`, keyed on media id + provider item id + size + kind)
2. Microsoft Graph thumbnail `c1920x1440`
3. Graph thumbnail `large`
4. the original

**The original is last on purpose.** iPhone photos are HEIC; Pillow cannot
open HEIC, so a successfully downloaded original was silently discarded and
both exports came out empty. Graph renders every *thumbnail* size as JPEG
whatever the source is, so a rendered preview is the reliable path and the
original is the fallback, not the other way round.

Two hard-won details in the same path:

- Graph answers a thumbnail request with a **302**, and the redirect
  response body is empty. Reading it as JSON raised `ValueError` and looked
  like "unreadable response".
- aiohttp's `StreamReader.read(n)` returns only what is currently buffered,
  not `n` bytes. Every download was silently truncated. All bounded reads
  now go through `http_read.async_read_bounded/async_read_capped`, which
  iterate `iter_chunked`.

Every downloaded image is validated with `is_decodable_image` before it
counts as a photo — including stock/gallery imagery, which arrives as
HEIC/AVIF or as an HTML error page just as easily.

### 1.4 Map snapshots

`map_snapshot.async_fetch_snapshot(session, provider, api_key, *, center,
markers, path, zoom, size)` with two backends:

- **OpenStreetMap** (default): tiles stitched with Pillow, mandatory
  attribution burned in, identifying User-Agent, bounded tile count.
- **Google Static Maps**: reuses the configured Places API key as a query
  parameter.

If Google is configured but rejects the key, the call **falls back to
OpenStreetMap** rather than returning nothing — an unactivated Static Maps
API is a Google Cloud setting nobody can fix from inside HA, and a working
free tile server is right there. The rejection reason is preserved for the
system check; the fallback makes the export work, it does not make the
misconfiguration invisible.

---

## 2. PDF pipeline

```
panel action "export_trip_pdf"  (inline, websocket)
  └─ TripPdfExporter.async_generate(trip_id)          # async: gather
       ├─ manager.async_get_assistant_payload
       ├─ experience.async_panel_payload              # media index
       ├─ per day: async_fetch_day_photos             # network
       ├─ crew portraits (local files, §5)
       ├─ _async_route_map → async_fetch_snapshot     # network
       └─ hass.async_add_executor_job(build_trip_pdf, data)   # CPU
  └─ async_create_ticket(pdf_bytes)  →  5-minute, 3-use download ticket
  └─ async_store_in_library(pdf_bytes) → durable copy ("Letztes PDF")
```

### 2.1 The split that matters

`trip_pdf.py` is **pure**: dataclasses in, `bytes` out, no HA, no network,
no I/O beyond an in-memory buffer. `trip_pdf_export.py` does all the async
gathering and then hands a fully-resolved structure to the renderer via
`async_add_executor_job`, because reportlab is CPU-bound and would block
the event loop.

This is the single most valuable structural decision in the codebase: the
entire layout is testable by calling one function with plain data.

### 2.2 Layout decisions worth reviewing

- **Days flow, they do not own a page.** 28 short days used to produce 33
  pages of whitespace. `_day_block_height` measures a day, `_day_pages`
  packs them.
- **Photos are laid out in justified rows**, not a fixed grid: every photo
  in a row shares the row height and keeps its own aspect ratio, so a
  portrait shot stays portrait. Row height is clamped
  (`_DAY_PHOTO_MIN_ROW_H`…`_MAX_ROW_H`), max 4 per row, up to 9 per day.
  A day that just misses the page shrinks its photos within those limits
  rather than starting a new page and leaving half of this one blank.
- **No filler.** A day with no usable photo gets no placeholder; if it *had*
  photos that could not be loaded, `photo_note` says which error occurred.
  This is a deliberate rule: the PDF must never imply data it does not have.
- **A bundled DejaVu font** is registered lazily, because Helvetica is
  WinAnsi-only and Polish place names rendered as black boxes.

### 2.3 Delivery

The download is a **ticket**, not a session-authenticated URL: the mobile
companion app performs a plain link download without attaching an auth
token, which turned every export into a 17-byte "401: Unauthorized" file.
The capability is the token — 128-bit, server-generated, 5-minute TTL,
3 uses, never listed. The durable library copy uses the same reasoning with
a uuid4-hex filename.

**Review question worth asking:** the library's "last PDF" lookup is
global, not per trip. With multiple trips, "Letztes PDF" can return another
trip's file. Known, not yet fixed.

---

## 3. Video pipeline

```
panel action "export_trip_video"  (returns immediately)
  └─ TripVideoExporter.async_start(trip_id, style)   → status dict
       └─ hass.async_create_task(_async_run)         # background
            ├─ ffmpeg_available()                    # fail fast
            ├─ payload + media index
            ├─ per chapter: photos + map snapshot + Gemini narrative
            ├─ prepare_chapter_assets  (executor: Pillow → numbered JPEGs)
            ├─ build_ffmpeg_filter_graph (pure string building)
            ├─ build_music_graph        (synthesised audio bed)
            └─ async_run_ffmpeg(args, timeout)        # subprocess
  └─ library file + HA persistent notification
panel action "trip_video_status"  ← polled every 5 s by the panel
```

### 3.1 Why a background job

A render takes minutes. A websocket request held open that long dies on the
first mobile connection change, and the work dies with it. `asyncio.shield`
fixes *completion* but not *delivery* — the client is gone either way. So
the rule in this codebase is: **≤ ~1 minute and the result matters →
inline; minutes-long → start/status polling plus a durable artefact**.

The finished video is written to disk and announced via a HA persistent
notification, because the user who started it has very likely closed the
app by the time it is ready.

### 3.2 Why ffmpeg is a subprocess, not an executor job

Video encoding occupies a thread for minutes; HA's executor pool is small
and shared, and meant for short CPU bursts (the reportlab render above).
ffmpeg is an external binary, so it goes through
`asyncio.create_subprocess_exec` and never touches the pool. It also cannot
be declared in `manifest.json` requirements (pip packages only), so
`ffmpeg_available()` is checked *before* any Gemini or photo work happens.

### 3.3 The render budget

`_render_timeout_seconds(frame_count)` scales with the work: base 240 s,
+20 s per frame, capped at 1800 s. A flat 240 s was wrong — measured on
four cores the chained `xfade` costs ~2.3 s per frame and scales linearly
(12 frames 28 s, 24 frames 53 s, 48 frames 111 s), and a small HA box is
several times slower, so a photo-rich trip could not finish at all.

`-preset veryfast` is set explicitly; x264's default "medium" buys detail
retention a slideshow of stills does not need (111 s → 96 s on the same
48-frame render).

### 3.4 Ken-Burns: built, measured, removed

`zoompan` re-renders every output frame; with `-loop 1` input it is
effectively quadratic. A three-chapter test took over six minutes. It was
removed rather than shipped — the liveliness comes from content (chapter
captions, shorter holds) which costs nothing at render time.

### 3.5 Captions are drawn with Pillow, not `drawtext`

The chapter title/date/narrative is burned onto the first frame of each
chapter while the frames are being prepared. Using ffmpeg's `drawtext`
would require escaping arbitrary model output on a command line. Drawing it
in Pillow removes that class of failure entirely.

### 3.6 Music is synthesised, not bundled

`trip_music.build_music_graph` generates a slow chord pad from sine tones
inside ffmpeg itself, keyed deterministically per trip, loudness-normalised
to `I=-24`. No audio file ships with the integration, so there is no licence
to verify and no attribution to carry. A real track dropped into
`assets/music` still wins.

### 3.7 Status robustness

`_async_run` catches `RoadplannerError`/`ValidationError`, then
**`asyncio.CancelledError` explicitly** (it is a `BaseException`, so
`except Exception` misses it and a restart mid-render left the status on
"running" forever), then bare `Exception`. The status always resolves.

---

## 4. Written summaries (feeds the PDF)

A separate service, `TripSummaryService`, generates and **stores**:

- per day: Gemini vision over the day's curated photos **plus** the planned
  facts (stops, date, distance). Photos alone invent place names; facts
  alone only describe what was planned.
- per person: vision with the crew member's reference portrait as image #1
  ("is this the same person, and what are they doing?"). No reference
  photo → captions only, never guesswork.
- per trip: text-only, written from the finished day summaries.

**Stored, not recomputed per export.** 23 days plus crew is ~30 vision
calls; doing that inside every PDF export would push it into minutes and
burn the daily quota on text that barely changes. Results live in
`day.details.ai_summary`, `trip.details.ai_summary` and on the crew record,
so the PDF only reads them — and a wrong sentence can be corrected by hand.

Writes carry the revision forward between days (each mutation returns the
revision it produced); a conflict skips that one day rather than aborting.
Every prompt forbids inventing places, names, times, weather or events.

---

## 5. Crew portraits

Portraits are stored as **local, already-cropped JPEGs** under
`crew_portraits/`, named `sha1(kind|entity_id|media_id|crop).jpg`.

Two consequences by construction:

- A face no longer depends on OneDrive being reachable or on the source
  photo still existing.
- The crop is applied **once, at storage time**, so no rendering path does
  crop maths. That class of bug (shown region ≠ picked region) cannot recur.

Because the crop is part of the filename, a re-crop is a different file —
nothing to invalidate, and a stale portrait can never be served.

---

## 6. Known weak points (for the review)

1. **"Letztes PDF"/"Letztes Video" are global, not per trip.** With more
   than one trip the newest file of *any* trip is returned.
2. **Only one background job of each kind at a time**, guarded by a task
   handle. Two trips cannot render simultaneously. Adequate today,
   arbitrary as a design.
3. **The vision daily limit guards the curation manager, not the PDF
   exporter or the summary service.** Budget enforcement is not uniform.
4. **Ferry crossing time depends on the user entering terminal times.**
   No schedule lookup exists, and estimating from distance was rejected as
   inventing a fact.
5. **`route_input_hash` is the cache key for routing**, and its field list
   is maintained by hand; a new route-relevant field that is not added
   there produces a silently stale route.
6. **No streaming for large photos.** Everything is held in memory as
   `bytes`; bounded by size caps (30 MB/photo), but a large day is a large
   allocation.

---

## 7. Test strategy

- Pure modules (`trip_pdf`, `trip_video`, `trip_summaries`, `map_snapshot`,
  `routing`) have directly executable behavioural tests: `python3
  tests/test_X.py`.
- Async orchestration is tested with fakes that mimic the real failure
  modes — notably a fake HTTP reader whose `read()` returns only the first
  chunk, because a naive fake hid the truncation bug described in §1.3.
- Contract tests assert cross-file wiring (action strings present in
  `panel.py` *and* dispatched in the frontend), because each half looked
  fine alone while a feature was unreachable.
- `python3 tools/release.py check` runs the whole suite plus repository
  validation, HACS preflight and version consistency; it gates every
  release.
