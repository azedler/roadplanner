# Definition of Done

A Roadplanner task is done only when every applicable item below is satisfied.

## Product behavior

- Acceptance criteria are demonstrably met.
- User-visible behavior is truthful and understandable.
- Existing supported workflows remain functional.
- Failure and empty states are defined.

## Architecture

- Relevant ADRs were followed.
- No duplicate source of truth, mutation path, ordering rule, or provider contract was introduced.
- Canonical IDs and revisions remain server-controlled.
- Derived data declares provenance and completeness where relevant.

## Data and migration

- Schema impact is documented.
- Existing trips remain readable or have a tested migration.
- Backup and rollback behavior is documented for risky changes.
- No personal or runtime data is included in source or fixtures.

## Security and privacy

- Secrets and provider tokens are not exposed.
- External data is minimized and validated.
- New permissions and network endpoints are documented.
- Diagnostics remain redacted.

## Mobile UX

- iPhone/iPad layout has no horizontal overflow.
- Touch targets and dialogs are usable in Home Assistant mobile views.
- Loading, offline, retry, and error states are visible.

## Validation

- `git diff --check` passes.
- `python tools/validate_repository.py` passes.
- Relevant regression tests pass.
- Home Assistant runtime behavior is tested when code changed.
- Mobile screenshots or evidence are provided when UI changed.
- Changelog and documentation are updated when user-visible behavior changed.

## Delivery evidence

The delivery states clearly:

- what was implemented,
- what was actually tested,
- what was not live-tested,
- migration and rollback impact,
- remaining known limitations.
