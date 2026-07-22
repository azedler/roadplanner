# Roadplanner Architecture

## 1. Product architecture

Roadplanner is a travel platform implemented today as a Home Assistant custom integration. Its architecture must keep the option open to extract a standalone core and additional clients later.

```text
Roadplanner UI
      │
      ▼
Home Assistant integration API
      │
      ├── Roadbook domain services
      ├── Assistant and change basket
      ├── Routing and navigation
      ├── Documents, expenses and tasks
      ├── Decisions
      ├── Media providers and albums
      └── Import and handoff adapters
      │
      ▼
Canonical Roadbook + private sidecars
```


## Architecture governance

Accepted decisions are recorded in [`docs/architecture/adr/`](docs/architecture/adr/README.md). A code change that contradicts an accepted ADR requires a new ADR that explicitly supersedes it. Detailed implementation boundaries live under [`docs/architecture/`](docs/architecture/); this root document remains the product-level overview.

## 2. Canonical data model

The canonical hierarchy is:

```text
Trip
└── Day
    └── Stop
```

Related entities reference canonical objects by stable IDs:

```text
Document
Expense
Todo
Decision
Media
Handoff
```

### Stops are the primary route elements

A stop can represent:

- start or destination,
- sightseeing,
- restaurant or snack,
- shopping,
- parking,
- fuel or charging,
- ferry terminal or ferry segment,
- campsite, motorhome site, or wild camp,
- activity, service point, or pause.

Days remain first-class objects because they carry date, notes, weather context, route metrics and daily execution state.

### Overnight continuity

The final overnight stop of day N is the effective start of day N+1. It is stored once and referenced by the following day. UI, routing and assistant context must not duplicate it.

## 3. Ordering contract

The route order is a domain property, not an insertion-order accident.

Roadplanner uses one canonical order source for every day:

1. a complete set of explicit planned `position` values,
2. otherwise the user-confirmed stored Roadbook list order for legacy data.

Arrival and departure times describe the schedule and never reorder stops. Every
mutation leaves a complete one-based position set behind. The map, day timeline,
Google Maps handoff, routing provider and assistant context all use the same
ordered stop list. Location completeness is tracked separately; a GPS-less stop
remains visible in sequence while the physical map and route are explicitly
partial.

## 4. Write path

All writes use the same domain and ChangeSet engine:

```text
User action or assistant proposal
→ normalized intent
→ ChangeSet proposal
→ complete validation
→ review preview
→ explicit apply
→ atomic write
→ revision +1 exactly once
```

No assistant, plugin, importer, or external service may write canonical files directly.

## 5. Revision contract

- Every successful business transaction increments the trip revision exactly once.
- Reads, previews, rejected proposals, failed imports, no-ops and synchronization do not increment it.
- `base_revision` is always taken from the current canonical trip by the server.
- Stale proposals become explicit revision conflicts.

## 6. Storage boundaries

### Canonical Roadbook

```text
/config/www/roadbook/
```

Contains the active trip pointer and canonical trip/day JSON.

### Private runtime data

```text
/config/.roadplanner_handoffs/
/config/.roadplanner_archive/
```

Contains handoffs, private documents, media indices, expenses, tasks, decisions and provider state. These directories are never committed.

## 7. Provider architecture

Provider interfaces must be explicit and replaceable:

- `AssistantProvider`
- `RoutingProvider`
- `GeocodingProvider`
- `MediaProvider`
- future `WeatherProvider`
- future `DocumentAnalysisProvider`

Providers may supply context or enrich a validated proposal. They may not apply canonical changes.

## 8. Roadplanner 3.0 architecture goals

1. Preserve the existing `roadplanner_mcp` domain and installed config entry.
2. Define stable APIs between domain, providers and UI.
3. Remove duplicate sources of truth.
4. Make stop ordering canonical and shared by all views.
5. Derive day routes only from the canonical ordered stop list.
6. Provide planning-distance estimates before full GPS detail exists.
7. Introduce Media Intelligence with local deduplication and optional AI highlight selection.
8. Keep HACS-compatible repository structure.
9. Prepare gradual extraction of Roadplanner Core without a big-bang rewrite.

## 9. Roadplanner 3.0 experience services

Roadplanner 3.0 introduces shared derived services instead of duplicating business logic in panel cards:

- `CanonicalDayService` supplies one day model to maps, route flow, navigation, decisions and assistant context.
- `MediaSelectionService` performs deterministic local deduplication, burst suppression and highlight selection.
- `ImageProviderService` keeps planning images separate from personal travel photos.
- panel view-models prefer personal travel media after a visit and fall back to attributed planning images before it.

The product target is documented in [Roadplanner 3.0 Vision & UX Blueprint](docs/product/ROADPLANNER_3_0_VISION_UX_BLUEPRINT.md).

## Intelligent travel foundation (3.2)

Roadplanner derives a bounded trip-integrity report from the canonical day payload and existing sidecars. The report is read-only and evaluates four dimensions independently:

- confirmed stop sequence,
- location/GPS completeness,
- route freshness and coverage,
- visual readiness.

Schedule times remain descriptive and are not integrity or ordering inputs. Repair actions create review-only change-basket drafts through the existing assistant/geocoding path.

Planning images are enriched by a bounded backend scheduler for the active trip. The scheduler prioritizes current and upcoming days, skips stops with personal OneDrive media, persists provider results, and isolates provider failures. The panel may start one small best-effort batch for immediate visible content, but no longer scans the whole trip on each load.

Release publication is triggered by the merge commit reaching `main`. The GitHub workflow validates that exact commit, builds artifacts, creates an immutable lower-case tag and release, and fast-forwards `develop` only when no unpublished work would be overwritten.
