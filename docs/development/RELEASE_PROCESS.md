# Release process

## Versioning

Roadplanner uses Semantic Versioning:

- major: user-visible or architectural breaking change with migration,
- minor: backward-compatible feature release,
- patch: backward-compatible defect correction.

The version must match in:

```text
custom_components/roadplanner_mcp/manifest.json
custom_components/roadplanner_mcp/const.py
CHANGELOG.md
```

## Branch contract

- `develop` is the active integration branch.
- `main` is always releasable and is the source for HACS and GitHub releases.
- A release is tagged only from `main`.

## Preparation

1. Complete the relevant task and Definition of Done.
2. Update version and changelog.
3. Document migration, rollback, privacy, provider, and mobile impact.
4. Run the [release checklist](RELEASE_CHECKLIST.md).
5. Merge the reviewed `develop` state into `main`.

## Build

Run from a clean `main` workspace:

```bash
python tools/validate_repository.py --release
python tools/build_release.py
```

The build tool creates a deterministic manual-install archive and SHA-256 checksum under `dist/`. GitHub source archives remain the normal HACS distribution source.

## Publish

1. Create tag `vX.Y.Z` from `main`.
2. Create a GitHub release using the matching changelog section.
3. Attach the optional manual-install archive and checksum.
4. Verify HACS installation or update.
5. Merge or rebase released changes back into `develop` if necessary.

## CI policy

Complex GitHub Actions are intentionally not required during the initial Roadplanner 3.0 phase. Local validation and explicit release evidence remain authoritative. Lightweight CI may be added later if it eliminates real failures rather than duplicating the same checks without benefit.
