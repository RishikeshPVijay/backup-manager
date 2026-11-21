# System architecture — normative implementation baseline

Status: assistant-selected engineering baseline, subject to mandatory certification gates. The confirmed product decisions in 01-PRD.md prevail on conflict.

## Components and dependencies

CLI → application service/job orchestrator → storage coordinator, repository coordinator, Restic adapter, hook runner, provenance service, planner, reporting. SQLite persists operations, claims, ownership, history and evidence. Providers are trusted installed entry-point packages. Core-supervised subprocesses execute Restic, hooks and storage workers. No mandatory daemon, no web UI, no remote agent, no generic backup-engine abstraction.

## Execution order

Validate frozen configuration → job admission → select usable primary/first usable secondary → acquire storage → pre-hooks (all, sequential) → source validation if pre succeeded → Restic backup and expected snapshot verification → post-hooks only after successful backup → cleanup hooks after preparation began → normal full-history replication from fixed backup source → additive cross-repository convergence → common retention per available destination → relinquish claims → persist job result/notification. Shared storage physical release can finish later and has separate outcome/event.

## Concurrency

Global running-job limit counts resource waits. Same-job overlap rejected; batch jobs wait for global slots in declaration order, individual invocations reject. SQLite BEGIN IMMEDIATE atomically admits entire repository claim sets; never wait with a transaction open. Acquire storage before repository claims; do not acquire additional storage while holding repository claims. No same-destination concurrent copy (A9). Storage resource identity canonicalized and cross-process claims tracked. OS liveness and physical state must be reconciled before stale-claim reclamation. D106 strict fairness remains paused: bounded cancellation-aware polling/wakeup with no strict FIFO promise.

## Operational invariants

Never initialize/register/replace repositories automatically. Never auto-unlock Restic. Never delete destination-only snapshots through replication. No automatic retries after launched Restic transfers. No claim release until process termination established. No external mount release without proven manager ownership. Failure of retention/hooks/notifications/release remains independently recorded. On corrupted/missing/restored state DB, fail closed pending explicit recovery.

## Technical assumptions requiring evidence

Restic snapshot-copy equivalence and exact lock matrix are *not certified*. The included POC is exploratory, not proof. Worker containment and recovery across OS crashes require fault injection. No implementation may declare the project complete until the acceptance and certification gates pass.
