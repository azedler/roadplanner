# HACS setup

## Repository requirements

Roadplanner keeps this layout at the repository root:

```text
custom_components/roadplanner_mcp/
README.md
hacs.json
```

Only one integration exists under `custom_components/`.

The integration manifest includes:

- domain,
- name,
- version,
- documentation,
- issue tracker,
- code owners.

Brand assets live under:

```text
custom_components/roadplanner_mcp/brand/
```

## Public repository requirement

HACS does not support private GitHub repositories. The repository must be made public before adding it to HACS.

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

GitHub releases are recommended but not required. If no release exists, HACS uses the default branch. For stable operation, Roadplanner will use full GitHub releases and keep `main` releasable.
