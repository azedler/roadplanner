# Roadplanner

Home Assistant custom integration (`custom_components/roadplanner_mcp`) plus a
Remotion/ffmpeg renderer add-on (`apps/roadplanner_renderer`). It plans road
trips and afterwards turns the trip's own photographs into a travel film.

## Language

Answers to Aron in **German**. Code, comments, commit messages and PRs in
**English**.

## Standing authorization: the release workflow

Aron has granted standing approval for the full release chain. Run it without
asking each time — merging a fix PR into `develop`, preparing the release,
opening the release PR, verifying publication, and scheduling the check-ins
that watch it. Code changes themselves still go through a PR he can read.

```
python3 tools/release.py check                 # full suite + repo + HACS + versions
git add -A && git commit
git fetch origin main && git merge origin/main --no-edit
python3 tools/release.py prepare X.Y.Z         # cuts changelog, bumps version, commits
git push -u origin develop
# then open the release PR develop -> main (auto-merge-release merges it when green)
```

Fix branches merge into `develop`; only `develop` merges into `main`, and that
merge is what publishes the release.

Pitfalls that have already cost time:

- `prepare` ends with "GitHub CLI (gh) is required" — that is normal here and
  the work up to that point is done. There is no `gh` in this environment; open
  the PR with the GitHub MCP tools instead.
- If `prepare` hits a timeout, version and changelog are already changed but not
  committed. Run `check` and commit by hand.
- Tags are created by the release workflow. **Never push a tag by hand.**
- **The add-on version IS the contract** between integration and renderer. If
  renderer behaviour changes, bump `apps/roadplanner_renderer/config.yaml`
  *and* `package.json`, or CI publishes no image and the user never gets the
  update. This has been missed three times.
- Only the push run on `develop` publishes the image, never the PR run
  (`if: github.event_name != 'pull_request'`). A red film render means no image.
- The CI film render takes ~18 minutes. Do not poll it; use Monitor or check
  runs.

## Test system is not the live system

This environment provides the **technical** proof. The **product** acceptance is
Aron rendering a full film on his live Home Assistant. Never report a live
result as confirmed when it was only measured here. Final reports always
separate:

**A)** technically demonstrated here · **B)** unconfirmed on live · **C)** live
update instructions.

Paid provider calls are triggered deliberately by the user on live. CI and tests
run exclusively against fixtures and mocks.

## Privacy and security (unchanged, non-negotiable)

OneDrive credentials live only in the integration and never reach the renderer ·
no tokens in the render package or in logs · minimal temporary files under
`/share`, temporary originals deleted · AI video analysis is opt-in and the UI
explains the cloud transfer · send the analysis proxy, never the original ·
original media is never modified · crew portrait routes are bearer secrets and
must not appear in Gemini contexts, render packages, logs or exported story
data · never enable original audio automatically.

## Architecture decisions not to undo

- **The film export has no path to a paid call.** It gets exactly one reading
  question ("what exists, and when does it play?"), not the music service. A
  test asserts `async_generate` and `TripFilmMusicService` do not appear in the
  export module.
- **Music comes last**, muxed into the finished MP4 with the video stream
  copied. Do not go back to "music in the render package".
- **A video is a MediaAsset like a photo.** No parallel video world.
- **A generated or uploaded image is only in the film after confirmation.**
  Confirming is a rename, not a re-render.
- **No place or brand names in rules.** Stop classification contains no chain;
  a test checks the table for that. Likewise: no special case for
  "Wolfsschanze".
- **Remotion renders frames in parallel tabs.** Anything stateful renders
  differently per tab — camera smoothing, music levels and label selection are
  deliberately stateless.
- **`Math.random()` and `Date.now()` have no place** in the renderer or in pure
  modules.

## Failure patterns this project keeps repeating

Worth asking, on every finding, whether it is one of these:

1. **One number in two deployables, one side raised.** Happened four times
   (photo regex 4→10→20, images per chapter 10→14, total images 180→260).
   Remedy: a test that reads both files and compares.
2. **An absent answer rendered as a state.** A missing reason, a missing motif,
   a missing error text. "Expected a PNG with a transparent background" appeared
   on *every* rejection and sent the user to fix the one thing that was not
   broken.
3. **A test that writes the same assumption down again.** The diagnosis read
   `zeigt`/`motive` while the analyses store `motifs`/`shows` — the test had
   invented the same field names and covered the bug. **Write tests against real
   data shapes, not invented ones.**
4. **A module with its own comparison logic beside the real one.** Always use
   the production function.
5. **A parameter that only applies on the first loop round.** `force` applied to
   round 0, so days beyond the first batch came from the cache forever.
6. **A relative import in a test module loaded by file path.** The error message
   does not name the cause. Register as a package (`ModuleSpec(..., is_package=True)`
   plus `__path__`).
7. **`str.casefold()` turns "ß" into "ss".** Patterns with ß never match folded
   names.

## Tests

No pytest. Every test is a standalone script:

```
python3 tests/test_<name>.py
node tests/test_<name>.mjs
```

`python3 tools/release.py check` runs all of them plus the repository validator
and the HACS preflight. Some Python tests need `reportlab` and `ffmpeg`; install
them in a fresh container before a full run.
