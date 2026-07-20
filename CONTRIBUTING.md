# Contributing to Roadplanner

Roadplanner is currently developed as a focused private product project. Contributions follow a specification-first workflow.

## Workflow

1. Define the problem and user outcome.
2. Create a development task using `docs/development/TASK_TEMPLATE.md`.
3. Read `AI_DEVELOPMENT_CONTRACT.md`.
4. Work on `develop` or a short-lived feature branch.
5. Run `python tools/validate_repository.py`.
6. Review migration, privacy and mobile impact.
7. Merge only a tested, reviewable change into `main`.

## Language

- Code, identifiers, architecture and development documentation: English.
- User-facing UI: German and English translations where applicable.

## Commit style

Use clear conventional-style messages, for example:

```text
fix: use canonical stop order in day route
feat: add local photo deduplication
refactor: isolate routing provider interface
docs: define Roadbook schema v1
```

## Issues

GitHub Issues are optional for internal planning. Use them for reproducible bugs, external feedback and work that benefits from discussion. The internal product backlog remains in `BACKLOG.md` until the contributor base grows.

## Pull requests

A pull request must state:

- user problem,
- implementation summary,
- affected modules,
- migration impact,
- validation performed,
- screenshots for visible UI changes.
