"""Supervised transport for side-effecting storage provider methods."""

from dataclasses import asdict
import json
import sys
from time import monotonic_ns

from .supervisor import run_journaled


def execute_lifecycle(path, anchor, plan, owner_id, provider, config, context, ownership_evidence=None):
    if not plan.leader or plan.kind not in {"acquire", "release"}:
        raise ValueError("Storage worker requires an authorized lifecycle plan.")
    if monotonic_ns() >= context.deadline_monotonic_ns:
        raise TimeoutError("Storage deadline already expired.")
    request = json.dumps({
        "provider": provider,
        "operation": plan.kind,
        "config": dict(config),
        "context": asdict(context),
        "ownership_evidence": ownership_evidence,
    }).encode()
    def authorize(database):
        row = database.execute(
            """SELECT kind, worker_identity, state FROM storage_operations
               WHERE operation_id = ?""",
            (plan.operation_id,),
        ).fetchone()
        expected = "ACQUIRING" if plan.kind == "acquire" else "RELEASING"
        return row == (plan.kind, owner_id, expected)

    return run_journaled(
        path, anchor, plan.operation_id, owner_id,
        (sys.executable, "-m", "backup_manager.lifecycle_worker"),
        deadline=context.deadline_monotonic_ns / 1_000_000_000,
        stdin_payload=request, authorize=authorize,
    )
