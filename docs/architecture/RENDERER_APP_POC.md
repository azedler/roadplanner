# Renderer app: deployment proof of concept

**Status: built, awaiting the device test.** No Remotion, no browser, no
video export. The production PDF and ffmpeg paths are untouched.

## Why this exists

The previous spike asked whether Home Assistant could run Remotion as a
child process of the integration. The answer was **NO-GO**: Node.js and a
browser are absent from Home Assistant Core, and HACS can only deliver
files inside the integration directory — not a runtime.

That failure was about the *runtime boundary*, not about Remotion. An app
is a separate container and may legitimately bring its own runtime. So
before building anything with Remotion in it, this proves the route:

> Can Roadplanner and a separate Home Assistant app be installed from the
> same public repository, find each other, exchange a job reliably, and
> survive restarts — without weakening anything that already works?

A **No** is a useful answer here too. It would end the app idea and leave
ffmpeg as the only renderer, which is exactly where things stand today.

## What one repository has to serve

Two consumers read this repository and never look at the same files:

```
GitHub repository
├── hacs.json + custom_components/  →  HACS installs the integration
└── repository.yaml + apps/         →  Supervisor installs the app
```

Neither is affected by the other's metadata, so the existing HACS route,
release process and version scheme stay exactly as they are.

**One trap is worth naming**, because nothing in the layout prevents it:
when this repository is added as an app repository, the Supervisor globs
`**/config.*` across the *whole checkout* and reads every hit as an app
manifest. A `config.yaml` added anywhere later — a test fixture, a tool's
settings, a vendored example — would appear in the user's app store as a
broken app. `tools/validate_repository.py` now rejects any `config.*`
outside `apps/<slug>/`.

## The exchange channel

The two processes never call each other. They share one directory:

```
/share/roadplanner-renderer/poc-v1/
├── renderer-status.json   # heartbeat, every 5 s
├── jobs/                  # Roadplanner writes here
├── processing/            # claimed jobs
├── status/                # per-job state
└── results/<job_id>/      # result.json + artefacts
```

`/share` is mounted into both the Home Assistant container and any app that
asks for it. That makes it the smallest channel that works — no port, no
socket, no token, no Supervisor API, nothing to authenticate.

Three properties make a directory usable as a channel:

- **Nothing is written in place.** Every file goes to a temporary name in
  the same directory and is then renamed. Within one filesystem `rename` is
  atomic, so a reader sees the old file or the complete new one, never a
  half-written one.
- **A job is claimed by moving it**, not by setting a flag. Two workers
  cannot both rename the same file; the loser simply gets an error. The PoC
  runs one worker, but the property costs nothing now and is what makes the
  design extensible later.
- **Terminal is terminal.** `completed`/`failed`/`expired` never return to
  `running`. A restarted worker cannot resurrect a finished job, which is
  the specific way a file channel would otherwise produce a job the panel
  polls forever.

Filenames come from a server-generated UUID and nothing else. No part of
any path originates in user text — the defence against traversal is that
the input never exists.

## Restart behaviour

| Situation | Result |
|---|---|
| App starts after Home Assistant | "nicht erreichbar" until the first valid heartbeat, then detected automatically |
| Home Assistant starts after the app | heartbeat and terminal statuses are read from disk; nothing is lost |
| App restarts mid-job | the interrupted job is marked `failed` with code `INTERRUPTED` on startup, never left claimed |
| Home Assistant restarts mid-job | the job stays findable by its id; re-submitting the same id replaces the file rather than queueing a second job |
| Corrupt JSON | reported as a protocol error; the app stays up and keeps taking work |
| Stale heartbeat | shown as not reachable, never as ready |
| App absent entirely | no error, no startup delay, no effect on the rest of Roadplanner |

Each of these is covered by a test that runs the real worker against a real
directory.

## Permissions

The app receives exactly one thing beyond a plain container: a read/write
mount of `/share`. Explicitly not requested: ports, ingress, host network,
privileged mode, the Docker socket, Home Assistant's `/config`, secrets,
the Supervisor API and the Home Assistant API. An AppArmor profile enforces
that only the exchange tree is writable.

`stage: experimental` and `boot: manual` mean the store marks it as an
experiment and it never starts by itself after a reboot.

## One thing the artefact forced

The returned SVG is **untrusted**. The exchange folder is writable by
another container, and the SHA-256 that "verifies" an artefact sits in the
same file as the artefact — whoever can forge one can forge both. The hash
therefore proves transport integrity, not authenticity. The panel embeds
the SVG through an `<img>` data: URL, where scripts and external references
are inert, rather than injecting it into the DOM.

## Verification so far

| | |
|---|---|
| Protocol rules | pass |
| Home Assistant side, including absence cases | pass |
| End-to-end, real worker against a real folder | pass, four consecutive runs |
| Cross-file wiring and permission boundaries | pass |
| `tools/release.py check` | pass |
| Container build and start | **not run locally** — no Docker daemon in the development environment; the CI workflow builds the image, starts it, waits for a heartbeat and checks that SIGTERM is honoured |

**Nothing here says anything about the real system yet.** The device test is
the point of the PoC, and it is the section this document leaves open.

## Result of the live run on Home Assistant

*Not yet filled in.* Required before any Remotion work begins:

- environment probe (Supervisor present, `/share` reachable, architecture),
- app installed from the repository and started,
- heartbeat seen by Roadplanner,
- test job submitted, claimed, completed, artefacts verified,
- app restart, Home Assistant restart, app stopped,
- CPU/RAM/disk during idle and during the job,
- PDF and ffmpeg video still working.

## Explicitly not built

No Remotion, no Chromium, no React, no TravelStoryManifest, no camper
animation, no PDF or video changes, no cloud rendering, no general renderer
API, no migration of Roadplanner modules into the app, no second
repository, no production release of the app.
