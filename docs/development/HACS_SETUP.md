# HACS setup

## Repository requirements

Roadplanner keeps the integration directly at the repository root:

```text
custom_components/roadplanner_mcp/
README.md
hacs.json
```

Exactly one integration exists under `custom_components/`.

The integration manifest includes:

- domain,
- name and version,
- documentation URL,
- issue tracker,
- code owners.

Brand assets live under:

```text
custom_components/roadplanner_mcp/brand/
```

## Branch and release source

- `main` is the only HACS/release source.
- `develop` is not a production installation source.
- Stable GitHub releases use tags such as `v3.0.0`.
- The version in the tag must match `manifest.json` and `const.py`.

## Public repository requirement

HACS custom repositories require a public GitHub repository. Keep Roadplanner private until the [publication checklist](PUBLICATION_CHECKLIST.md) is complete.

## Custom repository installation

In Home Assistant:

```text
HACS
→ menu
→ Custom repositories
→ https://github.com/azedler/roadplanner
→ Integration
→ Add
```

Then download Roadplanner, restart Home Assistant, and add or reload the integration.

## Update verification

For every stable release:

1. confirm the GitHub release is created from `main`,
2. verify HACS detects the new version,
3. install/update on a controlled Home Assistant instance,
4. restart Home Assistant,
5. confirm the reported version and core workflows,
6. keep the previous tag available for rollback.
