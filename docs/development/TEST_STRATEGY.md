# Test strategy

Roadplanner uses layered validation. The goal is not duplicate testing; each layer catches a different class of failure.

## 1. Repository validation

Run for every patch:

```bash
python tools/validate_repository.py
```

It checks repository layout, version consistency, JSON, Python syntax, JavaScript syntax when Node.js is available, local Markdown links, licensing, ADR structure, obvious secrets, and forbidden generated artifacts.

## 2. Domain regression tests

Tests under `tests/` should cover stable contracts rather than UI implementation details:

- ChangeSet normalization, validation, atomicity, and revision behavior
- canonical stop ordering and overnight continuity
- routing and ferry segment boundaries
- assistant/change-basket truthfulness
- documents, expenses, todos, decisions, and media assignments
- import formats and migration behavior

A production bug should receive a regression test whenever practical.

## 3. Home Assistant integration tests

When runtime code changes, test at least:

- integration setup and reload,
- affected WebSocket/service/API path,
- persistence across restart where applicable,
- permissions and review-only behavior,
- rollback or recovery for migration changes.

## 4. Mobile and visual checks

User-facing changes require checks on the narrowest supported view:

- iPhone portrait or equivalent width,
- iPad/desktop layout,
- Home Assistant browser and Companion/WebView behavior when possible,
- no horizontal overflow,
- correct safe areas and touch interaction.

## 5. Provider tests

Provider integrations use bounded fixtures or mocked responses for repeatability. Live provider tests are documented separately because they depend on accounts, quotas, networks, and external availability.

## 6. Release validation

Before a release:

```bash
python tools/validate_repository.py --release
python tools/build_release.py
```

Then inspect the archive contents and test the HACS/manual installation path on a controlled Home Assistant instance.
