ALTER TABLE copy_attempts ADD COLUMN phase TEXT;
ALTER TABLE copy_attempts ADD COLUMN source_generation INTEGER;
ALTER TABLE copy_attempts ADD COLUMN destination_generation INTEGER;
ALTER TABLE copy_attempts ADD COLUMN error_code TEXT;

CREATE TABLE transfer_requirements(
    run_id TEXT NOT NULL REFERENCES job_runs(run_id),
    requirement_id TEXT NOT NULL,
    logical_identity TEXT NOT NULL,
    destination_key TEXT NOT NULL REFERENCES repository_registrations(repository_key),
    destination_generation INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('PENDING', 'SATISFIED', 'BLOCKED')),
    PRIMARY KEY(run_id, requirement_id)
);

CREATE TABLE snapshot_provenance(
    repository_key TEXT NOT NULL REFERENCES repository_registrations(repository_key),
    repo_generation INTEGER NOT NULL,
    snapshot_id TEXT NOT NULL,
    logical_identity TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    PRIMARY KEY(repository_key, repo_generation, snapshot_id, profile_id)
);
CREATE INDEX ix_copy_attempt_run ON copy_attempts(run_id, state);
