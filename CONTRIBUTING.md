# Contributing to Roadplanner

Roadplanner is developed with a specification-first workflow. Product ideas, architecture decisions, implementation patches, validation, and releases have separate responsibilities.

## Required reading

Before changing code or persistent data behavior, read:

- [AI Development Contract](AI_DEVELOPMENT_CONTRACT.md)
- [Architecture Decision Records](docs/architecture/adr/README.md)
- [Development workflow](docs/development/DEVELOPMENT_WORKFLOW.md)
- [Definition of Done](docs/development/DEFINITION_OF_DONE.md)

## Workflow

1. Define one scoped `RP-XXX` task using [TASK_TEMPLATE.md](docs/development/TASK_TEMPLATE.md).
2. Synchronize `develop` and confirm a clean working tree.
3. Implement directly or apply a reviewed patch.
4. Inspect the diff and run repository plus task-specific validation.
5. Test affected Home Assistant and mobile workflows.
6. Commit and push to `develop` or a short-lived task branch.
7. Merge into `main` only when the change is releasable.

See [DEVELOPMENT_WORKFLOW.md](docs/development/DEVELOPMENT_WORKFLOW.md) for the complete process.

## Language

- Code, identifiers, architecture, and development documentation: English.
- User-facing UI: translated, currently German and English where supported.

## Commits

Follow [COMMIT_CONVENTIONS.md](docs/development/COMMIT_CONVENTIONS.md).

Examples:

```text
fix: use canonical stop order in day route
feat: add EUR reference values for expenses
refactor: isolate routing provider interface
docs: define release and patch workflow
```

## Planning and issues

- Product direction: `ROADMAP.md`
- Prioritized internal work: `BACKLOG.md`
- Concrete implementation: one `RP-XXX` task specification
- GitHub Issues: reproducible bugs and external feedback when useful

Do not duplicate the same work item across all four systems.

## Pull requests

Use the repository pull-request template. A pull request must state the user problem, scope, architecture impact, migration/rollback, privacy impact, validation evidence, and user-visible release notes.

## Contribution license

By submitting a contribution, you certify that you have the right to submit it and agree that it is licensed under the Apache License 2.0. No separate contributor license agreement is currently required.

Do not include code, media, documentation, data, or dependencies that cannot legally be redistributed under the project license or a clearly documented compatible license.

## AI-assisted changes

AI-generated output is untrusted input. Review provenance, copied material, dependencies, licensing, secrets, privacy, migrations, architecture, and tests before committing it. Material AI assistance should be disclosed in the pull request.
