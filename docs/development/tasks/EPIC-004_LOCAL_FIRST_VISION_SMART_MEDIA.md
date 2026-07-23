# EPIC-004 — Local-first Vision Smart Media

## Goal

Enhance the deterministic Roadplanner media selection with an optional semantic Vision stage while preserving local filtering, privacy controls, manual user choices and a complete fallback path.

## User outcome

- Before a visit, representative planning images are locally ranked and can optionally be semantically curated.
- After a visit, personal OneDrive photos are locally deduplicated and burst-reduced before the best cover and varied highlights are selected.
- The user can always inspect the complete album and override the cover.
- Local-only mode remains fully functional and is the default for existing installations.

## Processing contract

```text
Provider images / OneDrive metadata
→ deterministic local filter
→ bounded thumbnail candidates
→ optional Gemini Vision selection
→ strict ID validation
→ cached cover/highlight IDs
→ UI presentation
```

## Safety boundaries

- No more than 15 candidates per request.
- No original OneDrive file is modified or deleted.
- The model may reorder only supplied opaque image IDs.
- Manual cover choices always win.
- No person identification or sensitive-trait inference.
- Per-trip daily limits and candidate fingerprints protect quota and cost.
- Every failure falls back to deterministic local selection.

## Acceptance criteria

1. Local duplicate, burst, screenshot and metadata filtering always runs first.
2. Hybrid mode is explicit and local-only mode is the default.
3. Planning and travel images can both receive semantic cover/highlight selection.
4. The provider receives thumbnails, not a complete unfiltered album.
5. Unknown or invented image IDs are discarded.
6. Unchanged candidate sets are not re-analysed.
7. Daily Vision calls are bounded per trip.
8. Manual cover choices override Vision.
9. Provider, timeout, quota and parsing failures leave a usable local album.
10. The UI identifies local versus hybrid selection and offers a manual re-evaluation action for personal photos.
