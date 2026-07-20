# Tests

Roadplanner 3.0 is migrating historical release checks into stable repository tests.

## Test groups

- repository and packaging contracts,
- Roadbook and ChangeSet contracts,
- canonical stop ordering and overnight continuity,
- assistant and change basket,
- routing and ferry segments,
- documents, expenses, and todos,
- decisions,
- OneDrive/media assignment,
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

Repository validation:

```bash
python tools/validate_repository.py
```

Python unit tests as coverage is added:

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

See [Test strategy](../docs/development/TEST_STRATEGY.md).
