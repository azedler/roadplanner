# iPad-only GitHub and Codespaces workflow

No PC is required for the normal Roadplanner development and HACS deployment workflow.

## Choose the lightest environment

- GitHub web UI: repository settings, pull requests, releases, and small file edits.
- `github.dev`: small Markdown or configuration edits.
- GitHub Codespaces: patch application, Python validation, larger edits, and release preparation.

Roadplanner intentionally uses the default Codespaces environment. A repository-specific `.devcontainer` must not be added without a tested need and an explicit task.

## Start a work session

Open a Codespace on `develop`, then run:

```bash
git switch develop
git pull --rebase origin develop
git status --short
```

A clean working tree is required before applying a patch.

## Apply changes

Follow [PATCH_WORKFLOW.md](PATCH_WORKFLOW.md). Prefer uploading a temporary patch directly to Codespaces rather than committing it through the GitHub repository.

## Finish a work session

```bash
python tools/validate_repository.py
git status
git push origin develop
```

Stop the Codespace when finished to avoid unnecessary usage. Unsaved or uncommitted changes can be lost when a Codespace is deleted.

## Git push rejection

If Git reports `fetch first`, integrate remote work safely:

```bash
git pull --rebase origin develop
git push origin develop
```

Do not force-push `develop`.

## HACS deployment

HACS uses public `main` and stable releases. `develop` is never the production source. Follow [HACS_SETUP.md](HACS_SETUP.md) and [RELEASE_PROCESS.md](RELEASE_PROCESS.md).
