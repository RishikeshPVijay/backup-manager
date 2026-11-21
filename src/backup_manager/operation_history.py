"""Read durable operation records without exposing invocation secrets."""


def operation_rows(database, *, operation_id=None, active_only=False):
    records = database.execute("""
        SELECT run_id AS id, 'job' AS kind, state, job_id AS subject_id,
               started_at AS started_at, NULL AS exit_code, finished_at IS NULL AS active
        FROM job_runs
        UNION ALL
        SELECT operation_id, 'command', state, owner_id, launch_intent_at, exit_code, state != 'TERMINATED'
        FROM command_executions
        UNION ALL
        SELECT storage_operations.operation_id, 'storage', storage_operations.state,
               resource_id, NULL, NULL, storage_operations.state != 'TERMINATED'
        FROM storage_operations
        UNION ALL
        SELECT attempt_id, 'copy', state, run_id, NULL, NULL,
               state IN ('PLANNED', 'LAUNCH_INTENT', 'RUNNING')
        FROM copy_attempts
        ORDER BY started_at DESC, id, kind
    """)
    fields = tuple(column[0] for column in records.description)
    return [
        {**dict(zip(fields, row)), "active": bool(row[-1])}
        for row in records
        if (operation_id is None or row[0] == operation_id) and (not active_only or row[-1])
    ]
