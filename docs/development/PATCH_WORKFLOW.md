# AI patch workflow for iPad and GitHub Codespaces

GitHub is the source of truth for Roadplanner code. AI tools provide reviewed task specifications and Git patches; they do not require repository credentials.

## Delivery package

A task delivery includes:

- `RP-XXX_SPEC.md`
- `RP-XXX.patch`
- `RP-XXX_APPLY.md`
- validation evidence
- migration and changelog notes when relevant
- the exact base commit or branch state used to generate the patch

The patch contains one logical task only. A consolidated release candidate may contain several tightly coupled fixes only when they are validated and delivered as one atomic package.

## Safe local helper

Roadplanner includes a repository-local helper that automates the repeatable parts without writing to GitHub:

```bash
python tools/dev.py status
python tools/dev.py check
python tools/dev.py apply ../RP-XXX-delivery/RP-XXX.patch
```

`apply` requires a clean worktree, runs `git apply --check --whitespace=error-all`, applies the patch and then runs the canonical release check. The patch should live outside the repository so it does not make the worktree dirty.

To export reviewed staged changes without committing them:

```bash
git add -A
python tools/dev.py export ../RP-XXX.patch
```

The helper never commits, pushes, merges, tags, opens pull requests or changes remotes. Branch creation and every GitHub write remain deliberate user actions.

## Preferred upload path

Upload the patch directly into the Codespace repository root. Do not commit the patch file.

If the patch was uploaded through the GitHub web UI instead, synchronize first:

```bash
git pull --rebase origin develop
```

## Apply in Codespaces

```bash
git switch develop
git pull --rebase origin develop
git status --short
git rev-parse --short HEAD
```

The working tree must be clean.

Apply and validate with the helper when the patch is outside the repository:

```bash
python tools/dev.py apply ../RP-XXX-delivery/RP-XXX.patch
```

The equivalent manual sequence remains supported:

```bash
git apply --check --whitespace=error-all RP-XXX.patch
git apply --whitespace=error-all RP-XXX.patch
git diff --stat
git diff --check
python tools/release.py check
```

Commit only the intended repository changes:

```bash
git add -A
git commit -m "type: concise outcome"
git push origin develop
```

## Patch failure

Never use `--reject`, `--3way`, manual force, or partial application as the first response to a failed patch.

Check:

- current branch is `develop`,
- remote changes were pulled,
- working tree is clean,
- patch baseline matches the current commit,
- the patch file was not modified by a text editor.

Provide the current baseline when requesting a rebased patch:

```bash
git rev-parse --short HEAD
git status --short
```

## Repository hygiene

Temporary delivery files do not belong in Git:

```text
RP-*.patch
*_bundle.zip
*_VALIDATION.log
*_SHA256SUMS.txt
```

The specification may be copied into permanent project documentation only when it represents a lasting contract rather than a transient delivery artifact.
