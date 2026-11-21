# Normative engineering contracts — specification freeze v1.0

## Scope and precedence
This document supplies assistant-selected defaults authorized by the user. It does not supersede confirmed D/R/G/L/A decisions. In a contradiction, stop the affected task and record the conflicting requirement IDs. Never infer a passing certification from a specification.

## Runtime and packaging
Linux x86_64 and aarch64 are initial implementation targets; Python >=3.12,<3.14 for the first release, packaged as a PEP 621 project with a `backup-manager` console entry point. No mandatory daemon, network listener, cloud account, or web UI. SQLite state MUST reside on a local filesystem supporting advisory locks and atomic rename. SQLite WAL and foreign keys enabled. Do not claim Windows support for the manager itself; Windows/NTFS may be a remotely mounted storage destination on Linux.

## Configuration semantics
`schema_version=1` is mandatory in global and job TOML. Strictly reject unknown fields, invalid types, duplicate identifiers and invalid provider nested config. Job ID matches `[a-z][a-z0-9_-]{0,63}`; destination IDs and storage IDs use the same rule. `sources` must be nonempty. Destination paths must be absolute within the mounted storage namespace; reject path traversal, symlink escape and mountpoint mismatch after acquisition. A destination may reference the same physical storage as another destination but MUST use a distinct registered Restic repository path and ID. No implicit shell/environment interpolation. Secret references are nested inline under each destination; file/env only in v1. Config is immutable per batch at start, even for queued jobs.

## Error handling
Never auto-create or replace repositories; fail closed on identity mismatch or unapproved state rollback. Every phase records typed outcome independent of the job's overall FULL/PARTIAL/FAILED classification. User cancellation and process interruption are separately represented; aggregate CLI maps them to documented exit codes. Bounded redacted output; output truncation is explicitly reported. No retry of failed started copy, backup or lifecycle operation within an invocation.

## Snapshot and convergence invariants
Each requirement is a pair (logical snapshot identity, destination repository generation). A transfer has a frozen source snapshot ID set and frozen assigned requirement IDs. Before process launch it can be replanned. After successful launch, failure blocks assigned requirements for that run, including when an alternate source is later available. The absence of a complete enumeration never proves deletion. An unresolved equivalence is NOT a satisfied requirement. Copy evidence is valid only with confirmed Restic semantics for the certified version/profile and fresh destination inventory. Retention runs after normal replication and convergence; one job-level policy is applied independently per available destination. Do not filter snapshot requirements by retention eligibility.

## Crash and process handoff
Record launch intent in SQLite before spawning; process supervisor writes durable launch receipt with boot ID, supervisor identity and process start identity. Atomicity across OS spawn and SQLite commit is impossible. After ambiguous handoff, preserve LAUNCH_UNCERTAIN, prohibit in-run retry and block conflicting work until termination is proven. A durable receipt is not proof of successful Restic execution. No automatic restart/resume after process crash. Use process groups for cancellation and validate Linux cgroup/systemd-scope containment when available. Never treat PID alone as identity. Native Restic locks are additional safeguards and are never automatically removed.

## Resource coordination
Storage must be acquired and physical identity verified before repository claim-set admission. Atomic all-or-none repository claim sets under short BEGIN IMMEDIATE transactions; do not hold a SQLite transaction while waiting or running external commands. Cross-process owner identity uses boot ID + process start evidence + generation. Unknown physical ownership blocks release. No strict FIFO promise (D106 paused). Waiting must be bounded and cancellation-aware. An admitted job consumes a global job slot while waiting; batch admission follows invocation order. Acquire/release lifecycle is independently supervised and durable.

## Recovery and maintenance
Explicit `state init` for fresh instance; missing database is never silently recreated after initialization. `state check` is read-only. `state backup` uses SQLite online backup API and writes an integrity-verifiable manifest with instance ID and generation, excluding secrets. `state recover` requires operator-supplied backup and confirmation; reconcile storage and process state before re-enabling jobs. A restored older generation is unsafe until approved. Schema migrations are versioned and backed up first; never automatically downgrade. A failed migration must not leave the instance falsely reported healthy.

## CLI
JSON output has exactly one final object per command, matching `schemas/cli-result.schema.json`; human mode may stream progress. stdout is reserved for JSON in `--json` mode, diagnostics to stderr. Exit: 0 full; 2 partial; 3 failed; 4 invalid; 5 busy; 6 cancelled; 7 unsupported; 8 unsafe/recovery required; 9 internal error. Batch precedence: failed > partial > full, with other categories mapped to failed at batch aggregate while preserving per-job details. `--dry-run` does no storage acquisition or mutations; `--preflight` may acquire/release storage and must be separately named.

## Security
No shell interpolation for core commands. Never put passwords in argv, logs, DB, telemetry or generated manifests. Secret files must be regular files with owner-only read permissions (or stricter), and provider config may not contain plaintext secrets. Plugins and hooks are trusted OS code; isolate for supervision, not hostile-code containment. Never assume storage paths safe without verifying expected mounted filesystem. No automatic unlock, repair, repository identity approval, or pruning outside configured retention.

## Acceptance and release
Each requirement must map to an automated or explicitly marked manual test with evidence. An unimplemented test is NOT passing. No release until an exact Restic binary version is certified for required repo format, JSON semantics, copy equivalence and lock compatibility; no inconclusive correctness-critical tests. Release must include install/upgrade/rollback/recovery instructions and reproducible reports. Codex may choose libraries and IPC internals but may not change the behavior above.
