CREATE TABLE repository_initializations(
    coordination_id TEXT NOT NULL,
    repository_path TEXT NOT NULL,
    owner_id TEXT NOT NULL REFERENCES process_owners(owner_id),
    state TEXT NOT NULL,
    PRIMARY KEY(coordination_id, repository_path)
);
