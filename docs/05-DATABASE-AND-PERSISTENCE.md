# SQLite persistence and recovery

## Invariants

SQLite WAL, foreign keys ON, busy timeout, transactional migrations; local filesystem database only (do not put WAL DB on unreliable network shares). Transactions short; no external IO or waits inside transactions. Never infer physical storage state or process liveness solely from DB. Persist monotonic process deadline offsets together with wall clock and boot identity; reconstruct safely after reboot. Persist stable instance UUID and state generation; refuse unexpected rollback/replacement until admin recovery.

## Required tables (logical schema)

`schema_migrations(version, checksum, applied_at)`; `instance_state(instance_id, generation, created_at)`; `job_runs(run_id, job_id, config_fingerprint, state, backup_outcome, overall_outcome, started_at, finished_at)`; `phase_results(run_id, phase, target_id, status, error_code)`; `storage_resources(resource_id, coordination_id, expected_identity, identity_generation)`; `storage_claims(claim_id, resource_id, owner_id, generation, state)`; `storage_operations(operation_id, resource_id, kind, worker_identity, state, termination_evidence)`; `storage_ownership(resource_id, generation, owned_by_manager, evidence)`; `repository_registrations(repository_id, destination_key, repo_generation, approved_at)`; `repository_claim_sets(claim_set_id, operation_id UNIQUE, owner_id, state)`; `repository_claims(claim_set_id, repository_id, access_mode, PRIMARY KEY(claim_set_id,repository_id))`; `process_owners(owner_id, boot_id, pid, start_identity, supervisor_id, state)`; `command_executions(execution_id, operation_id, launch_intent_at, launched_at, exit_code, termination_evidence)`; `snapshot_enumerations(enumeration_id, repository_id, generation, status, completed_at)`; `snapshot_observations(enumeration_id, snapshot_id, original_id, tree_id, metadata_json)`; `copy_attempts(attempt_id, run_id, source_id, destination_id, frozen_snapshot_ids, state)`; `copy_evidence(evidence_id, attempt_id, source_snapshot_id, destination_snapshot_id, profile_id, verified_at)`; `events(event_id, entity_id, type, payload_redacted, at)`; `notification_attempts(id, event_id, status, error_code)`.

These are logical table contracts; Codex must add FKs, constraints, indexes, uniqueness and migration tests. Durable event writes and materialized state transitions must be atomic. Use generation-fenced claims to prevent ABA/stale-owner writes. Entire claim set granted or none in one BEGIN IMMEDIATE transaction. `RELEASED` only after verified process termination.

## Inventory and provenance

Complete successful enumeration replaces current view atomically; failed/partial enumeration never implies deletions. Repository ID/generation fences provenance. Evidence index is advisory and rebuildable. A copy attempt succeeds only after successful Restic exit and verified destination representation. Historical failure remains historical failure even if a later run observes the snapshot present.

## Crash recovery

On uncertain launch assume possibly started; block conflicting claims until definitive process termination. Never automatically retry, unlock Restic, unmount unverified resources, or reconstruct missing state as authoritative. Reconcile process identity using boot ID + PID start identity + supervisor/cgroup evidence, then storage inspection and Restic repository state. External mounts preserved. Corrupt/missing/rolled-back DB => refuse automatic work; explicit `state recover` workflow with integrity check, identity reconciliation, evidence-loss report and admin approval. SQLite online backup API for state snapshots; protect file permissions and metadata.

## I3 initialization checkpoint

The shipped first migration runs only during explicit state initialization. The configured database parent directory must already exist, be owned by the invoking account, and be protected against writes by other users. Initialization enables WAL and records the migration checksum and initial instance identity. The required state.anchor_path points to an independent generation witness. Its parent directory must be outside the database directory tree. Operators must also exclude it from every recovery, backup, and snapshot rollback that can rewind the database. A database and witness restored together cannot establish their own freshness. Read-only state checks currently refuse SQLite sidecars pending supervised reconciliation.
