# Branching model

## `main`

- always installable,
- only tested and documented changes,
- source for releases and HACS.

## `develop`

- integration branch for Roadplanner 3.x work,
- may contain incomplete features behind safe boundaries,
- must not be used by production HACS installations.

## Feature branches

Use only when a change is too large or risky to develop directly on `develop`.

Naming examples:

```text
feature/media-intelligence
fix/canonical-stop-order
refactor/provider-api
```
