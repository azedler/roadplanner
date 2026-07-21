# AI Development Contract

This contract applies to every AI system and human contributor working on Roadplanner.

## 1. Inspect before changing

- Read the real current code before proposing or implementing a change.
- Reuse existing manager, ChangeSet, storage, validation and UI paths.
- Do not create parallel inboxes, data models or write paths without an approved architecture decision.

## 2. Evidence before claims

Never claim that a change is built, tested, installable or production-ready unless the relevant artifact was actually produced and the stated checks actually ran.

Every delivery must distinguish:

- implemented and tested,
- implemented but not live-tested,
- designed only,
- blocked or uncertain.

## 3. Single source of truth

- Home Assistant and the canonical Roadbook are authoritative.
- Chat history, model memory, Drive documents and provider caches are not authoritative.
- External assistants and providers create proposals only.

## 4. IDs and revisions

- Never invent an ID for an existing object.
- Existing day, stop and preference IDs must come from the current Roadbook.
- New objects may receive temporary client references that the server resolves.
- The server sets `trip_id`, `base_revision`, `changeset_id`, timestamps and review mode.
- Models never increment revisions.

## 5. Review-only AI

AI-generated changes must follow:

```text
conversation/research
→ change basket
→ normalized ChangeSet
→ preview
→ explicit user apply or reject
```

No AI path may silently apply or delete canonical travel data.

## 6. Atomicity and migration

- A ChangeSet is validated completely before any write.
- Partial writes are forbidden.
- Breaking schema changes require a documented migration, backup and rollback path.
- Existing trips must remain readable across upgrades or be migrated explicitly.

## 7. Privacy and secrets

Never commit, log, quote or expose:

- API keys or OAuth tokens,
- Home Assistant secrets,
- private documents,
- booking references unless required in a protected runtime view,
- real Roadbook data,
- personal photos or media metadata,
- provider refresh tokens.

Diagnostic output must be redacted and minimal.

## 8. Mobile first

Every user-facing feature must work on iPhone/iPad and Home Assistant Companion views:

- no horizontal overflow,
- touch targets large enough for mobile use,
- safe-area support,
- graceful loading and error states,
- no desktop-only workflow for normal operation.

## 9. One canonical order

Stop numbering, map order, route calculation, timeline, navigation export and assistant context must use one canonical ordered-stop function. Never reproduce ordering logic independently in multiple modules.

## 10. Provider boundaries

- Provider-specific fields must not leak into canonical entities unless normalized.
- Providers may fail without corrupting the Roadbook.
- Provider output is untrusted until validated.
- Network calls need timeouts, understandable errors and bounded retries.

## 11. UI truthfulness

The UI may only state that an item was queued, saved, applied, synchronized or deleted after the server confirms it.

Assistant prose must not contradict the actual change basket or Roadbook state.

## 12. Scope control

Every development task must state:

- objective,
- files/modules allowed to change,
- files/modules not to change,
- acceptance criteria,
- tests,
- migration impact,
- security/privacy impact.

Do not expand scope silently.

## 13. Testing contract

At minimum, a code change must run the relevant subset of:

- Python compilation,
- JavaScript syntax validation,
- JSON/YAML validation,
- domain regression tests,
- migration tests,
- mobile layout checks,
- package integrity and secret scan.

New bugs require a regression test whenever practical.

## 14. Release contract

A release must provide:

- consistent version in `manifest.json` and constants,
- changelog entry,
- migration/rollback notes where applicable,
- validation results,
- no runtime or personal data in the repository or release.

## 15. Long-term architecture

Prefer changes that move toward:

```text
Roadplanner Core
Roadplanner Home Assistant adapter
Roadplanner UI
replaceable providers
```

Do not perform a big-bang rewrite. Preserve working behavior and extract boundaries incrementally.


## 16. Licensing and provenance

- Roadplanner source code is licensed under the Apache License 2.0.
- Do not introduce code, media, documentation, datasets, fonts, or generated assets with unclear provenance.
- Preserve required third-party copyright, license, and NOTICE information.
- New dependencies must have a license compatible with Roadplanner distribution and must be documented when attribution or redistribution conditions apply.
- AI-generated code is untrusted input: review it for provenance, copied passages, incompatible dependencies, private data, secrets, and license obligations before committing.
- Release artifacts must include the root `LICENSE` file and the root `NOTICE` file.

## 17. Patch delivery contract

- GitHub is the source of truth for code.
- An AI implementation must be delivered as a scoped specification and a Git patch based on the current `develop` commit.
- Never assume direct repository access, credentials, or the ability to push.
- A patch must pass `git apply --check`, repository validation, and the task-specific acceptance tests.
- Do not bundle unrelated features into the same patch.
- Do not create release artifacts in chat when the repository release process is available.

## 18. Repository hygiene

- GitHub is authoritative; chat attachments and local patch files are temporary transport artifacts.
- Never commit `.patch`, release ZIP, validation log, checksum, personal screenshot, or runtime export unless a task explicitly establishes it as a permanent project asset.
- Repository-level contracts and runtime code changes may share a task only when they are inseparable; otherwise deliver them separately.
- Root documentation is an entry point. Detailed procedures belong under `docs/` and must not create competing process definitions.
- A new configuration value requires one authoritative storage location and a migration from any legacy duplicate source.

## 19. Delivery evidence

Every implementation delivery must include:

- task ID and baseline commit,
- exact scope and affected files,
- patch or reviewed commit diff,
- validation commands actually run and their results,
- Home Assistant/mobile/provider checks actually run,
- explicit list of items not live-tested,
- migration, rollback, security, privacy, and licensing notes.

Do not substitute confidence, plausibility, or prose for execution evidence.
