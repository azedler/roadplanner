# Release process

## Versioning

Roadplanner uses Semantic Versioning:

- major: architectural or user-visible breaking change with migration,
- minor: backward-compatible feature release,
- patch: backward-compatible bug fix.

The version must match in:

```text
custom_components/roadplanner_mcp/manifest.json
custom_components/roadplanner_mcp/const.py
CHANGELOG.md
```

## Pre-release checklist

1. Review the task and acceptance criteria.
2. Confirm migration/rollback behavior.
3. Run:

```bash
python tools/validate_repository.py
```

4. Test the relevant workflow in Home Assistant.
5. Test mobile layout for visible UI changes.
6. Update `CHANGELOG.md`.
7. Merge the tested change into `main`.
8. Create a GitHub release with tag `vX.Y.Z`.
9. Verify the HACS update flow.

## No duplicate CI requirement

Automated GitHub Actions are not required in the initial 3.0 phase. The repository contains local validation tools. Lightweight CI may be added later when it materially improves reliability.
