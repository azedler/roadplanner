# HACS deployment

Roadplanner is distributed as a HACS custom integration from the public GitHub repository:

```text
https://github.com/azedler/roadplanner
```

The repository contains exactly one integration at:

```text
custom_components/roadplanner_mcp
```

HACS installs that directory into the Home Assistant configuration directory. Canonical Roadbook data, private documents, media references, expenses, tasks, handoffs, and credentials are stored outside the integration directory and are not part of a HACS update.

## Release model

- `develop` contains reviewed work in progress.
- `main` contains the stable source used for releases.
- Stable HACS versions are full GitHub Releases created from `main`.
- The Git tag is `v<manifest version>`, for example `v2.6.5`.
- `custom_components/roadplanner_mcp/manifest.json` remains the authoritative application version.
- `hacs.json` hides the default branch so users install explicit releases only.
- Roadplanner does not use `zip_release`; HACS uses GitHub's source archive for the selected release.

## Repository settings required before the first HACS installation

The repository must be public. HACS cannot install a private GitHub repository.

In GitHub repository settings verify:

- repository visibility: **Public**;
- Issues: enabled;
- description: `AI-powered travel planner and travel journal for Home Assistant`;
- topics: `home-assistant`, `hacs`, `custom-integration`, `travel-planner`, `roadtrip`, `roadplanner`;
- default branch: `main`.

The integration remains a custom HACS repository. Submission to the default HACS catalogue is a separate future task and requires additional checks, including Home Assistant Brands and hassfest.

## Preflight

From a clean commit on `main` run:

```bash
python tools/validate_repository.py
python tools/hacs_preflight.py --tag v2.6.5
```

The second command verifies the HACS repository layout, manifest metadata, repository-level brand assets, README installation information, and release-tag/version alignment.

GitHub also provides a manual workflow:

```text
Actions → HACS preflight → Run workflow
```

It intentionally does not run on every push. This avoids duplicating the normal Roadplanner test process while still allowing the exact official HACS validator to be run before publication.

The workflow temporarily ignores the external Home Assistant Brands check. Root HACS brand assets are included. Remove the ignore only when preparing a future submission to the default HACS repository catalogue.

## Create the first release from an iPad

1. Merge the tested `develop` state into `main`.
2. Open **Actions → HACS preflight** and run the workflow on `main`.
3. Open **Releases → Draft a new release**.
4. Choose **Create new tag** and enter `v2.6.5`.
5. Set the target to `main`.
6. Use title `Roadplanner 2.6.5`.
7. Add concise release notes.
8. Do not mark the release as a prerelease.
9. Publish the release.

No additional ZIP asset is required for HACS. GitHub's release source archive contains the HACS-compatible repository layout.

## Add Roadplanner to HACS

After the repository is public and the first release exists:

1. Open HACS in Home Assistant.
2. Open the menu in the upper-right corner.
3. Select **Custom repositories**.
4. Enter `https://github.com/azedler/roadplanner`.
5. Select category **Integration**.
6. Add the repository.
7. Search for **Roadplanner** and select **Download**.
8. Select release `v2.6.5`.
9. Restart Home Assistant.

Direct My Home Assistant link:

```text
https://my.home-assistant.io/redirect/hacs_repository/?owner=azedler&repository=roadplanner&category=integration
```

## Adopt an existing manual installation

Before the first HACS-managed download, create a full Home Assistant backup.

Try the normal HACS download of the same version first. HACS writes only the integration code under:

```text
/config/custom_components/roadplanner_mcp
```

The existing config entry and private Roadplanner data remain in place.

If HACS refuses because the directory already exists:

1. Stop or restart Home Assistant only when instructed by the UI.
2. Rename the current component directory to `roadplanner_mcp.manual-backup` using the Home Assistant File editor or Terminal add-on.
3. Download Roadplanner through HACS.
4. Restart Home Assistant.
5. Confirm the integration and panel work.
6. Remove the backup directory only after successful validation.

Do not remove or move:

```text
/config/www/roadbook
/config/.roadplanner_handoffs
/config/.roadplanner_archive
Home Assistant .storage data
```

## Future stable release

For every stable release:

1. update `manifest.json` version;
2. update changelog and release notes;
3. run the repository and HACS preflights;
4. merge to `main`;
5. create a full GitHub Release with matching `vX.Y.Z` tag;
6. verify HACS shows the update;
7. install on a test Home Assistant instance before production use.

## Rollback

HACS offers recent published releases. To roll back:

1. open the Roadplanner repository in HACS;
2. choose **Redownload**;
3. select the previous release;
4. restart Home Assistant;
5. verify that the selected code version remains compatible with the current Roadbook schema.

A Home Assistant backup remains the authoritative rollback path for schema or data migrations.
