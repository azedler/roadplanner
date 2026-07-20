# Backlog

## P0 — correctness

- Canonical stop ordering based on planned/temporal day progression.
- Rebuild day timeline and route from the same ordered stop list.
- Prevent insertion order from controlling numbering.
- Regression coverage for inherited overnight start plus explicit stops.

## P1 — Roadplanner 3.0 UX

- Phase-aware overview and planning-completion metrics.
- Estimated trip distance before detailed GPS stops exist.
- Assistant conversation shrinking and attachment pruning.
- Mobile-first visual cleanup and calmer information hierarchy.

## P1 — Media Intelligence

- Local duplicate detection.
- Burst grouping.
- Technical quality scoring.
- Top images per stop/day.
- Optional AI highlight and title-image selection.

## P2 — repository and delivery

- Select public license.
- Public-source privacy audit.
- Create `develop` branch.
- First GitHub release from imported baseline.
- Add public repository to HACS as custom integration.

## P2 — architecture

- Roadbook schema v1 documentation.
- Provider interfaces v1.
- Incremental separation of domain, Home Assistant adapter and UI.
- Persistent migrations and rollback test harness.
