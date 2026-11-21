# Codex agent instructions — Backup Manager

Read `docs/14-DECISION-REGISTER.md`, `docs/01-PRD.md`, `docs/SPECIFICATION-STATUS.md`, and `docs/13-IMPLEMENTATION-ROADMAP.md` before coding. Read relevant technical documents for each milestone.

Authority: confirmed user decisions > explicit supersessions > PRD > architecture/contracts > selected defaults > examples. Never silently change product semantics. Restic ONLY; no generic engine abstraction. No web UI, remote agent or mandatory daemon. D106 strict fairness paused.

Work through I1–I9 in order. For each milestone implement code, tests, migrations, documentation, and progress evidence. Run tests; do not claim a test ran if it did not. Before production convergence, certify exact Restic binary against disposable repos and record test reports. Never auto-init, auto-unlock, auto-retry started transfers, or assume crashed process dead. Respect storage/repository claims and fail-closed recovery.

Maintain `IMPLEMENTATION-STATUS.md` with milestone, tests run, evidence, blockers, deviations. If an experiment disproves a proposed equivalence or lock assumption, stop dependent implementation, preserve safe behavior, and document the issue. Do not manufacture pass evidence. If an unspecified choice affects safety or user-visible behavior, mark blocker; for internal libraries and structure, choose and document the simplest compliant option.

Definition of done: all critical acceptance scenarios, Restic certification, crash/storage recovery tests, security checks, and packaging pass on supported Linux environment; trace every confirmed requirement to a test. Do not mark release complete otherwise.

## Specification freeze extension
Read `docs/16-NORMATIVE-ENGINEERING-CONTRACTS.md`, `docs/17-EXECUTION-AND-RECOVERY-ALGORITHMS.md`, `docs/18-CLI-GRAMMAR-AND-JSON.md`, `docs/19-RELEASE-AND-OPERATIONS.md`, and `docs/20-TRACEABILITY-AND-ACCEPTANCE.md` before implementing any milestone. `contracts/plugin_api_v1.py`, `db/001_initial.sql`, and `schemas/cli-result.schema.json` are reference contracts, not installed runtime modules. Run `python tools/validate_handoff.py` before changes and after schema/config changes. Static validation is not acceptance testing.

## Milestone gate procedure
For each I1–I9: create issue/task checklist with exact requirement IDs; implement; run tests and failure injections; record command/exit/evidence in `IMPLEMENTATION-STATUS.md`; update traceability row to precise test assertion; audit safety invariants; commit only after tests. Never invent Restic certification evidence. If an experimental fact contradicts the spec, record a blocker and halt dependent work.
