CREATE INDEX ix_storage_operations_resource_state ON storage_operations(resource_id, state);
CREATE INDEX ix_storage_claims_owner_state ON storage_claims(owner_id, state);
CREATE INDEX ix_command_executions_owner_state ON command_executions(owner_id, state);
CREATE INDEX ix_repository_claim_sets_owner_state ON repository_claim_sets(owner_id, state);
