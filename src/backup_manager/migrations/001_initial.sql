-- Initial runtime migration. The connection enables foreign keys before applying it.
CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL);
CREATE TABLE instance_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1), instance_id TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0), created_at TEXT NOT NULL);
CREATE TABLE job_runs(run_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, config_fingerprint TEXT NOT NULL, state TEXT NOT NULL, backup_outcome TEXT, overall_outcome TEXT, started_at TEXT NOT NULL, finished_at TEXT);
CREATE INDEX ix_job_runs_job_time ON job_runs(job_id, started_at DESC);
CREATE TABLE phase_results(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES job_runs(run_id), phase TEXT NOT NULL, target_id TEXT, status TEXT NOT NULL, error_code TEXT, details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE process_owners(owner_id TEXT PRIMARY KEY, boot_id TEXT NOT NULL, pid INTEGER, process_start_identity TEXT, supervisor_id TEXT, state TEXT NOT NULL, termination_evidence TEXT);
CREATE TABLE storage_resources(resource_id TEXT PRIMARY KEY, coordination_id TEXT NOT NULL, expected_identity TEXT, identity_generation INTEGER NOT NULL DEFAULT 1);
CREATE INDEX ix_storage_coord ON storage_resources(coordination_id);
CREATE TABLE storage_claims(claim_id TEXT PRIMARY KEY, resource_id TEXT NOT NULL REFERENCES storage_resources(resource_id), owner_id TEXT NOT NULL REFERENCES process_owners(owner_id), generation INTEGER NOT NULL, state TEXT NOT NULL);
CREATE INDEX ix_storage_claim_active ON storage_claims(resource_id, state);
CREATE TABLE storage_operations(operation_id TEXT PRIMARY KEY, resource_id TEXT NOT NULL REFERENCES storage_resources(resource_id), kind TEXT NOT NULL, worker_identity TEXT, state TEXT NOT NULL, termination_evidence TEXT);
CREATE TABLE storage_ownership(resource_id TEXT NOT NULL REFERENCES storage_resources(resource_id), generation INTEGER NOT NULL, owned_by_manager INTEGER NOT NULL CHECK(owned_by_manager IN(0,1)), evidence_json TEXT NOT NULL, PRIMARY KEY(resource_id,generation));
CREATE TABLE repository_registrations(repository_key TEXT PRIMARY KEY, job_id TEXT NOT NULL, destination_id TEXT NOT NULL, restic_repository_id TEXT NOT NULL, repo_generation INTEGER NOT NULL, approved_at TEXT NOT NULL, UNIQUE(job_id,destination_id));
CREATE TABLE repository_claim_sets(claim_set_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE, owner_id TEXT NOT NULL REFERENCES process_owners(owner_id), state TEXT NOT NULL);
CREATE TABLE repository_claims(claim_set_id TEXT NOT NULL REFERENCES repository_claim_sets(claim_set_id), repository_key TEXT NOT NULL REFERENCES repository_registrations(repository_key), access_mode TEXT NOT NULL, PRIMARY KEY(claim_set_id,repository_key));
CREATE INDEX ix_repo_claim_lookup ON repository_claims(repository_key,access_mode);
CREATE TABLE command_executions(execution_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE, owner_id TEXT NOT NULL REFERENCES process_owners(owner_id), state TEXT NOT NULL, launch_intent_at TEXT, launched_at TEXT, process_identity_json TEXT, exit_code INTEGER, termination_evidence TEXT);
CREATE TABLE snapshot_enumerations(enumeration_id TEXT PRIMARY KEY, repository_key TEXT NOT NULL REFERENCES repository_registrations(repository_key), repo_generation INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('incomplete','complete','failed')), completed_at TEXT);
CREATE INDEX ix_snapshot_enum ON snapshot_enumerations(repository_key,repo_generation,status);
CREATE TABLE snapshot_observations(enumeration_id TEXT NOT NULL REFERENCES snapshot_enumerations(enumeration_id), snapshot_id TEXT NOT NULL, original_id TEXT, tree_id TEXT, metadata_json TEXT NOT NULL, PRIMARY KEY(enumeration_id,snapshot_id));
CREATE TABLE copy_attempts(attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES job_runs(run_id), source_key TEXT NOT NULL REFERENCES repository_registrations(repository_key), destination_key TEXT NOT NULL REFERENCES repository_registrations(repository_key), snapshot_ids_json TEXT NOT NULL, requirement_ids_json TEXT NOT NULL, state TEXT NOT NULL, launched INTEGER NOT NULL DEFAULT 0 CHECK(launched IN(0,1)), execution_id TEXT UNIQUE REFERENCES command_executions(execution_id));
CREATE TABLE copy_evidence(evidence_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES copy_attempts(attempt_id), source_snapshot_id TEXT NOT NULL, destination_snapshot_id TEXT NOT NULL, profile_id TEXT NOT NULL, verified_at TEXT NOT NULL);
CREATE TABLE events(event_id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, type TEXT NOT NULL, payload_redacted TEXT NOT NULL, occurred_at TEXT NOT NULL);
CREATE INDEX ix_events_entity ON events(entity_id,occurred_at);
CREATE TABLE notification_attempts(id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(event_id), status TEXT NOT NULL, error_code TEXT);
-- Process-level claim fencing is enforced by the coordinator.
-- Cross-repo claim compatibility is checked under BEGIN IMMEDIATE before inserting claim rows.
-- Immutable event records are append-only by application policy.
