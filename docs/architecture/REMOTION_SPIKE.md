# Remotion as a local subprocess — technical spike

**Status: experiment. Not a product feature, not enabled by default, not
released.** The production video export (ffmpeg) is untouched and remains
the only renderer that produces a trip video.

## The one question

> Can this Home Assistant installation start, supervise and terminate a
> local Node/Remotion child process, and use it to produce a five-second
> H.264 test video reproducibly, without destabilising the existing
> Roadplanner or ffmpeg pipelines?

Everything below exists to answer that and nothing else. A **No** is a
valid and useful outcome: it keeps ffmpeg productive and moves Remotion to
a later, different question (add-on or external worker), which is
explicitly out of scope here.

## What was built

Three layers, deliberately separable.

### A. Runtime diagnosis — ships with the integration, changes nothing

`custom_components/roadplanner_mcp/remotion_diagnostics.py`

Read-only. It reports OS and architecture, whether `node` exists **and can
actually be spawned** (not merely whether it is on `PATH`), the Node
version against a floor of 20, `npm`, `ffmpeg`, `ffprobe`, whether the
export directory is writable, free disk space, any browser it can find,
and whether a renderer package is present at the configured path.

It installs nothing. No `npm install`, no `npx remotion browser ensure`,
no package manager, no downloads, no writes outside Roadplanner's own
directories. **A missing runtime is the answer**, not a problem to fix
behind the user's back — a diagnosis that repaired its own preconditions
would be answering a different question.

Every result is one stable code (`READY`, `NODE_MISSING`,
`SUBPROCESS_FORBIDDEN`, `BROWSER_MISSING`, …) inside a fixed envelope with
a German summary and a recommended next step. The panel, the tests and the
Go/No-Go write-up all read the same codes.

### B. The renderer — in this repository, exercised by CI

`remotion_renderer/`

A minimal Remotion project with exactly one composition
(`roadplanner-test`): a title, a subtitle and a drawn camper that crosses
the frame. No trip photos, no map, no Gemini text, no network access —
anything richer would make a failed render ambiguous between "the
subprocess route does not work" and "the content pipeline has a bug".

Contract: `argv[2]` is a JSON job file, stdout carries **one JSON object
per line and nothing else**, stderr carries technical noise (stored,
bounded), exit code 0 means a complete valid video was written. Remotion
and its browser are chatty, so every diagnostic goes to stderr — a stray
log line on stdout would corrupt the protocol.

Dependencies are pinned exactly and the lockfile is committed. The render
uses the SSR API (`bundle` → `selectComposition` → `renderMedia`), not
scraped CLI output.

`.github/workflows/remotion-spike.yml` installs the pinned dependencies,
type-checks, provisions a browser **in CI only**, renders the five-second
video and validates it with ffprobe (codec, container, resolution,
duration). It is a separate workflow on purpose: the experiment must never
be able to slow down or block an ordinary Roadplanner release.

### C. The supervised run on Home Assistant — opt-in

`custom_components/roadplanner_mcp/remotion_spike.py`

Reuses the shape the video export already established — background job,
status polling, durable artefact — rather than inventing a second
mechanism beside it. What is new is only the protocol.

- never `shell=True`, never a composed command line; the executable and
  the script are absolute paths that were verified to exist;
- a minimal, controlled environment, with Remotion telemetry disabled;
- the output path is built server-side from a validated job id and proven
  to resolve inside Roadplanner's own directory, with both sides
  `resolve()`d so a symlink cannot escape;
- own process group, so a timeout ends the browser and not just the Node
  parent: SIGTERM first, SIGKILL after a grace period;
- `asyncio.CancelledError` handled explicitly (it is a `BaseException`, so
  `except Exception` misses it and would leave a status on "running"
  forever);
- a status that says "running" with no live task — what a restart leaves
  behind — is reported as cancelled rather than spinning indefinitely;
- **exit code 0 is a claim, ffprobe is the check**: codec, container,
  resolution and duration are verified before the file counts as a result;
- its own status, its own folder, its own result. A test render can never
  be mistaken for, or overwrite, the trip's "Letztes Video".

## Deployment: what this spike does and does not settle

The existing route is unchanged and remains the simple one:

```
develop in this repository → tools/release.py check → PR → release → HACS
```

No second repository, no second release channel, no add-on, no registry,
no HTTP service.

**The open packaging question, stated plainly.** With a HACS integration,
only files inside the shipped integration directory arrive on a user's
system. `remotion_renderer/` sits *outside* `custom_components/roadplanner_mcp/`,
so it serves development and CI — it is **not** automatically present
after a HACS update, and nothing here pretends otherwise. That is why the
renderer path is an explicit, empty-by-default option: the runtime
diagnosis works everywhere, while the render only runs where an operator
has pointed Roadplanner at an existing renderer.

Deciding how Remotion would eventually be delivered is deliberately **not**
part of this spike. Answering it early would have meant building a
deployment route before knowing whether the subprocess route works at all.

Two small changes were needed to keep the existing tooling honest with a
Node project in the tree: `tools/validate_repository.py` and
`tools/release.py` now skip `node_modules` and build output. Without that,
the repository validators would walk thousands of third-party files —
including deliberately malformed JS/JSON fixtures — and fail on content
the repository does not ship.

## Result of the local (non-Home-Assistant) run

Run in the development container on 2026-08-06 with Node v22.22.2:

| | |
|---|---|
| `npm ci` | 248 packages, ~17 s |
| `tsc --noEmit` | clean |
| First attempt, full Chrome | refused — Chrome removed old headless mode; reported as `BROWSER_MISSING` with Remotion's own explanation preserved |
| Second attempt, `chrome-headless-shell` | rendered |
| ffprobe | `h264`, `1280x720`, `30/1`, `mp4`, `5.056 s`, 278 kB |

The protocol behaved as designed on the failing attempt too: the refusal
arrived as a `failed` event with a code, not as a crash.

**This says nothing yet about Home Assistant.** A development container is
not a Home Assistant OS installation; it has Node, a browser and ample
CPU. The live result has to be filled in on the real system — that is the
whole point of the spike, and it is the section the PR leaves open.

## Go / No-Go

**GO** if the real Home Assistant environment can spawn Node, a compatible
browser is present or later packageable, the required system libraries
exist, the test renders repeatedly, resource use is acceptable, Home
Assistant stays responsive, and updates still look feasible through the
plain HACS route.

**NO-GO** if Node is missing or unreliable there, if the system libraries
cannot be provided without an invasive change, if browser and native
Remotion parts cannot sensibly travel through HACS, if the process
destabilises Home Assistant or eats memory, or if package size and
architecture dependence would wreck the simple update route.

A No-Go keeps ffmpeg productive and defers Remotion to an optional add-on
or external worker — a different question, for a different day.

## Explicitly not built

No TravelStoryManifest, no Gemini story, no real trip or family photos, no
animated real route, no PDF changes, no replacement for the ffmpeg
pipeline, no Remotion player in the panel, no cloud rendering, no Docker
or add-on deployment, no second repository, no licence change, no
automatic installation, no user-supplied React components or projects.
