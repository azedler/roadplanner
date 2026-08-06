# Third-party notices

Roadplanner itself is licensed under the terms in [`LICENSE`](LICENSE).
The components listed here are **separately licensed** by their own
authors and are not covered by that licence. Nothing on this page is legal
advice; it is dependency hygiene, and it is meant to be re-checked before
any production release that ships or requires these components.

## Runtime dependencies of the integration

| Component | Used for | Licence |
|---|---|---|
| [reportlab](https://pypi.org/project/reportlab/) | Rendering the trip-summary PDF | BSD-3-Clause |
| [Pillow](https://pypi.org/project/Pillow/) | Decoding, cropping and composing images | MIT-CMU |
| [DejaVu Sans](custom_components/roadplanner_mcp/assets/fonts/) | PDF text, because Helvetica cannot render Polish and Baltic place names | Bitstream Vera / DejaVu licence (see the `LICENSE` file in that folder) |
| [ffmpeg](https://ffmpeg.org/) | Video encoding. Invoked as an external program; **not** bundled or redistributed | LGPL/GPL depending on the build the operator installed |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) tiles | Map snapshots in PDF and video | ODbL — attribution is burned into every rendered map image |

## Experimental: Remotion (subprocess spike)

The Remotion renderer under [`remotion_renderer/`](remotion_renderer/) is an
**experiment** (see [`docs/architecture/REMOTION_SPIKE.md`](docs/architecture/REMOTION_SPIKE.md)).
It is not part of the production video path, and Roadplanner never installs
it on a user's system.

- **Package:** `remotion`, `@remotion/bundler`, `@remotion/renderer`,
  `@remotion/cli`
- **Version pinned at:** `4.0.506`
- **Checked on:** 2026-08-06
- **Licence:** Remotion ships its own licence, separate from any of the
  usual open-source licences. At the time of that check it allowed free
  use for individuals and for companies/teams up to three people,
  including automated rendering. Larger use requires a paid company
  licence.

Three consequences that are deliberately encoded rather than assumed:

1. **The version is pinned exactly** and the lockfile is committed, so the
   licence that was checked corresponds to the code that runs.
2. **The licence status is not treated as permanent.** It must be
   re-checked before any production release that ships or requires
   Remotion — the check date above is what makes a stale assumption
   visible.
3. **Telemetry is disabled** for the spike's child process
   (`REMOTION_DISABLE_TELEMETRY=1`), and no trip content ever reaches it:
   the test render uses a synthetic title and a drawn camper, never family
   photos, place names or manifest data. Rendering stays local.

Current Remotion licence terms: <https://www.remotion.dev/docs/license>
