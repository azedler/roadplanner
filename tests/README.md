# Tests

Roadplanner 3.0 is migrating historical release checks into stable repository tests.

## Test groups

- repository and packaging contracts,
- Roadbook and ChangeSet contracts,
- canonical stop/day ordering, location completeness, partial-route UX, and overnight continuity,
- assistant and change basket,
- routing and ferry segments,
- documents, expenses, and todos,
- decisions,
- OneDrive/media assignment, deterministic duplicate reduction and highlight selection,
- universal import,
- migrations,
- mobile panel smoke tests.

## Conventions

- Name Python tests `test_*.py`.
- Keep provider tests deterministic with fixtures or mocked responses.
- Do not use real trips, documents, tokens, account IDs, or photo metadata.
- A production bug should receive a regression test whenever practical.
- Test canonical domain behavior instead of copying implementation logic into assertions.

## Commands

Canonical local and CI validation:

```bash
python tools/release.py check
```

This runs all dependency-light contract tests, JavaScript syntax checks, repository validation, HACS preflight, and Python-cache cleanup through one shared entry point.

Individual diagnostics remain available:

```bash
python tools/validate_repository.py
for test in tests/*.py; do python "$test"; done
for test in tests/*.mjs; do node "$test"; done
```

See [Test strategy](../docs/development/TEST_STRATEGY.md) and [Release automation](../docs/development/RELEASE_AUTOMATION.md).
