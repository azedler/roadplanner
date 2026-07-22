# Branching model

## `main`

- always installable and releasable,
- contains only tested and documented changes,
- source for tags, GitHub releases, and HACS,
- never used for unfinished development.

## `develop`

- integration branch for Roadplanner 3.x work,
- receives reviewed task patches and short-lived feature branches,
- may contain unreleased work,
- is not the production HACS source.

## Short-lived branches

Use a dedicated branch when a task is large, risky, or requires iterative collaboration:

```text
feature/RP-305-media-intelligence
fix/RP-303-canonical-stop-order
refactor/RP-310-provider-boundaries
```

Small, scoped documentation and maintenance patches may be committed directly to `develop` by the maintainer.

## Synchronization

Before work:

```bash
git switch develop
git pull --rebase origin develop
git status --short
```

After a release, ensure `develop` contains the released `main` history:

```bash
python tools/release.py sync
```

The command only performs fast-forward operations. Avoid force-pushing shared branches.
