# EPIC-006 — Decompose the four largest modules

## Goal

Split `assistant.py` (4671 lines), `frontend/roadplanner-panel.js` (6745 lines), `roadplanner.py` (3605 lines) and `experience_manager.py` (3143 lines) into smaller, single-responsibility modules — no behavior change, no schema/data change. This is a planning document only; nothing here has been implemented. Each numbered step below should become its own small RP-XXX task, validated with the full check suite before moving to the next step.

## Why these four, and why in this order

All four are god-object/god-file patterns: one huge class (or, for the JS file, one huge `HTMLElement` subclass) that owns several genuinely separable responsibilities. None of them have circular-import problems severe enough to block a start, except `roadplanner.py` ↔ `changeset.py`, which is called out explicitly below.

Recommended file order (independent of the in-file step order within each): **`assistant.py` → `experience_manager.py` → `roadplanner.py` → `roadplanner-panel.js`**. The first two decompose cleanly into a facade + collaborators with low blast radius. `roadplanner.py` has one real circular-dependency knot (see below) that's easier to untangle once the pattern has been proven twice. `roadplanner-panel.js` is last because it needs a `panel.py` static-file change and a test-harness change (see its section) before any file can even be split — that's infrastructure work the other three don't need.

Every step must pass, before moving on:
```
python tools/release.py check
```
(Python 3.12 required — see the SessionStart hook merged earlier; `navigation.py` doesn't parse on 3.11.)

---

## 1. `assistant.py` (4671 lines) — do first

One class, `RoadplannerAssistant`, plus module-level basket/session machinery. Proposed split:

| New module | Responsibility | Depends on |
|---|---|---|
| `assistant_shared.py` | Pure text/ID sanitization + constants used by both basket and compile code (`_clean_text`, date regexes, `_ALLOWED_ENTITY_TYPES`, `_BASKET_ENTITY_ALIASES`) | stdlib only |
| `assistant_basket.py` | The conversational "change basket": `AssistantSession`/`AssistantSessionStore`, basket-item repair/normalization | `assistant_shared`, `canonical_day`, `roadplanner.ValidationError` |
| `assistant_compile.py` | Normalize a provider's raw "compile" JSON (alias/casing, new-day temp-IDs) into the canonical operation dialect + bounded Roadbook context | `assistant_shared`, `canonical_day`, `structured_output` |
| `assistant_operation_sanitizer.py` | Turn one compiled operation into a strictly-validated ChangeSet operation (day/stop ID inference, position bookkeeping) — the trickiest function (`_sanitize_operation`) moves here **whole, unmodified** | `assistant_shared`, `assistant_compile` |
| `assistant.py` (slimmed) | Orchestration: chat turn handling, briefing, drafts, compile→sanitize→ChangeSet | all of the above |

**Don't split `_sanitize_operation` internally** — it interleaves envelope stripping, per-entity validation, ID-existence checks and stateful position bookkeeping tightly enough that decomposing it further risks behavior changes. Move it whole; revisit later with dedicated test coverage if wanted.

**Test impact** (both are raw source-string/`ast` checks, not Python imports — no import graph to rewire, just two path constants):
- `tests/test_assistant_day_reference_normalization.py` — `ast.parse`s `assistant.py` to extract `_normalize_compiled_day_reference` by name → repoint to `assistant_compile.py`.
- `tests/test_day_plan_integrity_contract.py` — greps `assistant.py` text for `"position_state"` / `'operation["position"] = insert_at + 1'` → repoint to `assistant_operation_sanitizer.py`.

**Extraction order**: `assistant_shared.py` → `assistant_compile.py` → `assistant_basket.py` → `assistant_operation_sanitizer.py` (highest risk, do last, verify `async_prepare_review` end-to-end afterward).

---

## 2. `experience_manager.py` (3143 lines) — do second

One class, `RoadplannerExperienceManager`, already a clean composition-root candidate — nine cohesive method clusters, each becomes a collaborator; the class itself becomes a thin facade.

| New module | Responsibility |
|---|---|
| `experience_helpers.py` | Pure day/stop/geo/folder-name parsing helpers |
| `media_token_service.py` | Sign/validate HMAC media redirect tokens |
| `media_library_manager.py` | OneDrive scan/delta sync engine + photo→day/stop auto-assignment + media CRUD |
| `media_vision_curation.py` | Shared "local filter → optional Gemini Vision" engine (used by both media curation and destination galleries) |
| `media_curation_manager.py` | Per-stop/trip photo album curation, on top of `media_vision_curation` |
| `destination_gallery_manager.py` | AI-assisted planning-photo galleries per stop, background auto-population |
| `place_enrichment_orchestrator.py` | Place-enrichment preview/submit → ChangeSet (delegates resolution to existing `place_enrichment.py`) |
| `decision_manager.py` | AI-suggested decision cards: generate, enrich, select/transfer/archive/delete |
| `panel_payload_builder.py` | Assemble the aggregated `"experience"` panel payload — extract **last**, highest fan-in |
| `experience_manager.py` (slimmed) | Facade: wires collaborators together, every current public method becomes a one-line delegation |

**Circular-dependency fix**: almost every collaborator ends by calling `self.async_panel_payload(...)`. Instead of each new module importing `panel_payload_builder.py` directly, inject a `get_panel_payload` callable (and, for the two background jobs that also trigger media curation, an `on_media_changed` callable) from the facade's `__init__`. This keeps all concrete cross-imports inside the facade and gives a clean one-directional dependency graph.

**Public surface that must not break**: `panel.py` calls ~20 methods directly on `runtime.experience` (`async_prepare_place_enrichment`, `async_submit_place_enrichment`, `async_curate_stop_media`, `async_panel_payload`, etc.) and reads OneDrive/vision settings as attributes. `__init__.py` and `experience_http.py` also touch it directly. All of these must remain reachable as facade attributes/methods — this is the point of keeping `experience_manager.py` as a facade rather than deleting it.

**Test impact** (five source-string contract tests, no Python-import rewiring needed):
- `test_decision_enrichment_contract.py` → repoint to `decision_manager.py`
- `test_destination_refresh_contract.py`, `test_background_visual_enrichment_contract.py` → repoint to `destination_gallery_manager.py`
- `test_day_plan_integrity_contract.py` → repoint to `place_enrichment_orchestrator.py`
- `test_gemini_vision_contract.py` → spans **two** new files (`media_curation_manager.py` + `media_vision_curation.py` for one half, `destination_gallery_manager.py` for the other) — update to read/assert against both.

**Extraction order**: `experience_helpers.py` → `media_token_service.py` → `decision_manager.py` → `place_enrichment_orchestrator.py` → `media_library_manager.py` → `media_vision_curation.py` → `media_curation_manager.py` → `destination_gallery_manager.py` → `panel_payload_builder.py` (last).

**Progress**: steps 1-6 done (`experience_helpers.py`, `media_token_service.py`, `decision_manager.py`, `place_enrichment_orchestrator.py`, `media_library_manager.py`, `media_vision_curation.py`). `experience_manager.py` is at 1282 lines (from 3143).

**Refined design for steps 6-9** (worked out before implementing step 6, to avoid rework in 7-9):

- `media_vision_curation.py` → `VisionCurationEngine`: owns the vision settings (`media_curation_mode`, `media_vision_max_candidates/highlights/daily_limit`, `vision_enabled` property) and one public method `async_curate(...)` (renamed from `_async_semantic_curation`). Constructed with `hass`/`store`/`onedrive`/`provider`. The facade gets read-only properties delegating to it (same pattern as `media_library_manager`'s settings in step 5), since `panel.py` reads these as plain attributes on `runtime.experience`.
- `_find_stop` (currently sitting in the destination-gallery code range) is used by **both** step 7 and step 8 — move it to `experience_helpers.py` when extracting step 7, rather than duplicating it.
- `media_curation_manager.py` → `MediaCurationManager`: owns `_vision_lock`/`_vision_status` (their lifecycle belongs to the batch-curation orchestration, not the engine), calls into `VisionCurationEngine.async_curate`, uses the existing `get_panel_payload` callback pattern.
- `destination_gallery_manager.py` → `DestinationGalleryManager`: mirrors step 5 — owns its own `async_initialize`/`async_shutdown` (moves the destination-enrichment scheduling half out of the facade entirely), its own lock/status/unsub handles, and also calls into `VisionCurationEngine.async_curate`. Its periodic background job currently calls `self.async_auto_curate_media(...)` (step 7) directly — replace with an injected `trigger_vision_curation(trip_id)` callback, same shape as step 5's `on_media_changed`.
- `panel_payload_builder.py` (last): has the highest fan-in (reads settings/status from every other collaborator plus `geocoder`/`provider`/`store`/`hass`/`manager`). Since nothing calls back into it, give it these as **constructor parameters**, not a callback — the facade's `async_panel_payload` becomes a one-line delegation, so steps 3-8's callback wiring (`get_panel_payload=self.async_panel_payload`) needs zero changes.

---

## 3. `roadplanner.py` (3605 lines) — do third

Bottom layer: exception hierarchy, JSON/ID/validation primitives, trip/day/stop normalization, routing-metric helpers, projections, `TripState`, and the ~1940-line `RoadplannerStore` god class (init/migration, queries, CRUD mutations, transaction/snapshot/backup, changeset apply, context export).

| New module | Responsibility |
|---|---|
| `json_io.py` | Atomic, size-bounded JSON/text file I/O (no domain knowledge) |
| `identifiers.py` | ID generation + field validators (`ValidationError` and friends live here or in a small exceptions module) |
| `json_tree_validation.py` | Recursive bounded "details" JSON validation |
| `trip_documents.py` | `normalize_stop`/`normalize_day_document`/`normalize_trip_document` + schema-version constants |
| `routing_helpers.py` | Derive/invalidate/reconcile routing metrics, ferry/transport metadata |
| `trip_projections.py` | Bounded read-only projections (`_compact_trip/_day/_stop`, summaries) |
| `trip_state.py` | `TripState` dataclass |
| `trip_repository.py` | On-disk layout, schema migration on load, transaction/snapshot/backup machinery |
| `trip_queries.py` | Read-only query surface (`load_trip`, `get_trip_summary`, `search_stops`, ...) |
| `trip_mutations.py` | CRUD/revision-checked writes (`add_day`, `update_stop`, ...) |
| `changeset_operations.py` | Store-side changeset glue (`preview_changeset`, `apply_changeset`, ...) |
| `context_export.py` | Handoff-context JSON/Markdown generation (check for overlap with existing `handoff.py` before finalizing — may belong there instead) |
| `roadplanner.py` (slimmed) | `RoadplannerStore` as a composition/façade class; same public API, so `manager.py` needs no changes beyond import paths |

**The one real circular-dependency risk in this whole EPIC**: `changeset.py` already imports `TripState`, `normalize_day_document`, `_stable_id`, etc. from `roadplanner.py` at module load time, while `roadplanner.py`'s own changeset-calling methods (`preview_changeset`, `apply_changeset`, ...) locally re-import `changeset.py` **inside the method body** — a clear sign the original author already hit this cycle. Fix: `changeset.py` should import from `trip_documents.py`/`trip_state.py`/`identifiers.py` directly (not from `roadplanner.py`), so only `roadplanner.py`/`changeset_operations.py` imports `changeset.py`, one-directionally. Do this as part of extracting `changeset_operations.py` (second-to-last step).

**Widely-imported symbols** (`ValidationError`, `RoadplannerError`, `validate_identifier`, `TripState`) are used by 15-28 sibling modules — keep them importable at stable paths (re-export from `roadplanner.py` if needed for a transition period) so this doesn't turn into a repo-wide import-path rewrite in one commit.

**Test impact**: `test_day_plan_integrity_contract.py` (reads `roadplanner.py` for `reindex_explicit_positions` — repoint to `trip_mutations.py`), `test_place_enrichment_changeset.py` and `test_roadbook_sequence_normalization.py` (both dynamically load `roadplanner`/`changeset`/etc. by file path into a synthetic package — need the new module names added to their loader lists).

**Extraction order**: `json_io.py` → `identifiers.py` → `json_tree_validation.py` → `routing_helpers.py` → `trip_projections.py` → `trip_documents.py` → `trip_state.py` → `trip_repository.py` → `trip_queries.py` → `trip_mutations.py` (update `test_day_plan_integrity_contract.py` alongside this step) → `changeset_operations.py` (resolve the `changeset.py` cycle here; update the two dynamic-loader tests) → `context_export.py`.

---

## 4. `frontend/roadplanner-panel.js` (6745 lines) — do last, needs infra first

One class, `RoadplannerPanel extends HTMLElement`, no existing mixin/module structure.

### Blocking infrastructure (must land before any file split)

1. **`panel.py` currently serves exactly one static file** (`async_setup_panel_support` registers a single `StaticPathConfig` mapping one URL to `roadplanner-panel.js`). Splitting into multiple files requires either serving the whole `frontend/` directory or registering one `StaticPathConfig` per new file.
2. **8 of 12 `tests/*.mjs` files load the file via `vm.runInThisContext(source)`** — a classic-script context that cannot execute `import`/`export`. These must switch to real dynamic `import()` of a `file://` URL (Node supports this natively) before ES modules can be introduced. The other 4 test files assert on raw substrings in the file text and need to be repointed to whichever new file now contains the string.

Recommendation: accept both infrastructure changes and use real ES modules (cleanest long-term), rather than working around them with runtime `<script>` injection or `new Function()` tricks.

### Proposed groups (mixins applied via `Object.assign(RoadplannerPanel.prototype, ...)`, or later true action-map dispatch — see below)

| Group | File | Contents |
|---|---|---|
| Core | `lib/constants.js`, `lib/styles.js`, `lib/core-helpers.js` | WS constants, label dictionaries, `escapeHtml` etc.; the ~900-line CSS template string; shared formatters/permission checks (`_canEdit`, `_formatDate`, `_findDay`, ...) and the pervasive `_runAction`/`_render`/`_showToast`/`_closeDialog` |
| Assistant chat | `features/assistant.js` | Chat send/briefing/diagnostics, message rendering, link/markdown rendering |
| Universal import | `features/universal-import.js` | Upload/analyze/transfer/discuss/discard + rendering |
| Archive/documents | `features/archive.js` | Upload/clipboard/paste machinery, document/expense/todo CRUD + rendering |
| Media/gallery | `features/media.js` | OneDrive media, destination galleries, image search |
| Trip/day/stop + route/map | `features/trip-day-stop.js`, `features/route-map.js` | Stop/day CRUD, route calculation, map rendering — largest, most test coverage, extract last among features |
| Place enrichment | `features/place-enrichment.js` | Review-card rendering + prepare/submit (shared with assistant — flag as cross-cutting) |
| Decisions/integrity | `features/decisions-integrity.js` | Decision cards, travel-integrity rendering |

**The dispatch problem**: all clicks/changes are handled by one delegated listener calling two monolithic functions, `_handleClick` (~700 lines) and `_handleChange`, each a single long `if/else if` chain across every feature area. Two options:
- **Step 1 (do this first, lower risk)**: keep the dispatcher as-is in core, move method *bodies* out as mixins applied to the shared prototype.
- **Step 2 (do this only after all features are isolated)**: convert the dispatcher itself into a per-feature action-map lookup (`{"archive-upload": handler, ...}` merged from each feature module) — the more thorough follow-up, touches every action name at once, highest blast radius of the whole EPIC.

**Extraction order**: infra (panel.py static paths + test-harness `import()` switch) → `lib/styles.js` (zero logic, free win) → `lib/constants.js` → `lib/core-helpers.js` → smallest self-contained feature (universal-import or place-enrichment) → archive → media → decisions/integrity → assistant → trip/day/stop + route/map (largest, most test coverage, last) → only then the dispatcher action-map refactor (step 2 above), as its own dedicated final step.

---

## Open questions

- `context_export.py` (roadplanner.py split) vs. folding into existing `handoff.py` — needs a judgment call during implementation, not resolvable by static analysis alone.
- Whether `roadplanner-panel.js`'s eventual per-feature action-map dispatcher is worth doing in this EPIC at all, or left as a follow-up once the mixin-based split has proven itself.

## Status

Planned. No code changed by this document. Each extraction step above should become its own small RP-XXX task, applied and validated (`python tools/release.py check`) one at a time.
