# Development workflow

GitHub is the source of truth for code. The canonical integration branch is `develop`; `main` is releasable.

## 1. Define the task

Every implementation begins with one scoped `RP-XXX` specification containing:

- user problem and outcome,
- in-scope and out-of-scope work,
- architecture constraints and ADRs,
- files expected to change and files protected from change,
- migration, rollback, privacy, and mobile impact,
- acceptance criteria and validation evidence.

Do not start implementation from a conversational description alone.

## 2. Synchronize the workspace

In Codespaces:

```bash
git switch develop
git pull --rebase origin develop
git status --short
git rev-parse --short HEAD
```

The working tree must be clean before applying a task patch.

## 3. Review the delivery

An AI or human delivery must provide:

- the task specification,
- a patch based on the stated `develop` commit,
- apply instructions,
- validation performed,
- migration and changelog notes where relevant.

Review the specification before applying the patch.

## 4. Apply and inspect

Follow [PATCH_WORKFLOW.md](PATCH_WORKFLOW.md). Never force a patch that fails `git apply --check`.

Inspect the diff before committing:

```bash
git diff --stat
git diff --check
git diff
```

## 5. Validate

Run:

```bash
python tools/validate_repository.py
```

Then run the task-specific tests and perform the relevant Home Assistant and mobile checks.

## 6. Commit and push

Use a concise conventional-style commit message:

```bash
git add -A
git commit -m "type: concise outcome"
git push origin develop
```

Temporary patch files, ZIP archives, logs, and screenshots used only for development must not be committed.

## 7. Test the integrated branch

Deploy or install the `develop` result only in a controlled test workflow. Record actual evidence and any known limitations.

## 8. Merge and release

Create a pull request from `develop` to `main` only when the change satisfies the [Definition of Done](DEFINITION_OF_DONE.md). Releases are created only from `main`.
