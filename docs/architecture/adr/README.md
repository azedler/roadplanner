# Architecture Decision Records

Architecture Decision Records (ADRs) capture decisions that constrain future Roadplanner development.

## Rules

- ADRs are immutable once accepted. Superseded decisions remain in the repository and point to their replacement.
- Every ADR states context, decision, consequences and rejected alternatives.
- Code changes that contradict an accepted ADR require a new ADR that explicitly supersedes it.
- Product ideas and implementation tasks do not belong here; use `ROADMAP.md`, `BACKLOG.md` or a development task.

## Status values

- `Proposed`
- `Accepted`
- `Deprecated`
- `Superseded by ADR-NNN`

## Naming

```text
ADR-NNN-short-kebab-case-title.md
```

## Index

| ADR | Decision | Status |
|---|---|---|
| [ADR-001](ADR-001-domain-authority-boundaries.md) | Domain authority boundaries | Accepted |
| [ADR-002](ADR-002-review-only-write-path.md) | Review-only mutation pipeline | Accepted |
| [ADR-003](ADR-003-media-originals-and-references.md) | Media originals and references | Accepted |
| [ADR-004](ADR-004-canonical-stop-ordering.md) | Canonical stop ordering | Accepted |
| [ADR-005](ADR-005-derived-views-use-domain-services.md) | Derived views use shared domain services | Accepted |
| [ADR-006](ADR-006-money-preserves-original-currency.md) | Money preserves original currency and adds EUR reference values | Accepted; implementation pending |
| [ADR-007](ADR-007-planning-and-travel-image-precedence.md) | Planning and travel image precedence | Accepted |
| [ADR-008](ADR-008-phase-oriented-primary-navigation.md) | Phase-oriented primary navigation | Accepted |
