# Commit conventions

Use short, outcome-oriented conventional-style messages.

## Types

- `feat`: backward-compatible user feature
- `fix`: backward-compatible defect correction
- `refactor`: internal restructuring without intended behavior change
- `docs`: documentation only
- `test`: tests only
- `chore`: repository, tooling, or release maintenance
- `security`: security hardening or vulnerability correction

## Format

```text
type: concise imperative outcome
```

Examples:

```text
fix: use canonical stop order in day route
feat: add EUR reference values for expenses
refactor: isolate media provider contract
docs: define release and patch workflow
chore: update repository validation
```

## Rules

- One logical task per commit when practical.
- Do not include temporary patch files or generated archives.
- Do not hide breaking changes in `refactor` or `chore` commits.
- Reference an `RP-XXX` task in the pull-request description; the commit subject may remain concise.
- Never rewrite shared remote history unless the maintainer explicitly decides to do so.
