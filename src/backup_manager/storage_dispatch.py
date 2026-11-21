"""Independent lifecycle dispatch. The worker adopts a committed reservation."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic_ns
from uuid import uuid4

from .launch_journal import register_owner
from .plugin_api import ExecutionContext
from .process_identity import ProcessIdentity
from .providers import discover
from .state import StateUnsafe, state_write
from .storage_claims import LifecyclePlan
from .storage_driver import drive_lifecycle, fence_operation

MAX_DISPATCH = 65536


def start_dispatch(path, anchor, plan, source_owner, provider, config, context, release_seconds, evidence=None):
    payload = json.dumps({
        "db": str(path), "anchor": str(anchor), "plan": asdict(plan), "source_owner": source_owner,
        "provider": provider, "config": dict(config), "context": asdict(context),
        "release_seconds": release_seconds, "evidence": evidence,
    }).encode()
    if len(payload) > MAX_DISPATCH:
        raise StateUnsafe("Storage dispatch request exceeds its limit.")
    process = subprocess.Popen(
        (sys.executable, "-m", "backup_manager.storage_dispatch"),
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        process.stdin.write(payload)
    finally:
        process.stdin.close()
    return process


def adopt(path, anchor, plan, source_owner, identity):
    result = None
    with state_write(path, anchor, wait=5) as database:
        row = database.execute(
            """SELECT kind, state FROM storage_operations
               WHERE operation_id = ? AND worker_identity = ?""",
            (plan.operation_id, source_owner),
        ).fetchone()
        if database.execute(
            "SELECT 1 FROM command_executions WHERE operation_id = ?", (plan.operation_id,),
        ).fetchone() is not None:
            return None
        if row == ("acquire", "CANCEL_REQUESTED"):
            # Adoption is required before launch. A cancellation that wins
            # before adoption therefore has no executable worker.
            database.execute(
                "UPDATE storage_operations SET state = 'TERMINATED' WHERE operation_id = ?",
                (plan.operation_id,),
            )
        elif row == (plan.kind, {"acquire": "ACQUIRING", "release": "RELEASING"}[plan.kind]):
            result = register_owner(database, identity, str(uuid4()))
            database.execute(
                "UPDATE storage_operations SET worker_identity = ? WHERE operation_id = ?",
                (result, plan.operation_id),
            )
    return result


def dispatch(request):
    path, anchor = Path(request["db"]), Path(request["anchor"])
    plan = LifecyclePlan(**request["plan"])
    context = ExecutionContext(**request["context"])
    provider = discover("storage")[request["provider"]]
    owner = adopt(path, anchor, plan, request["source_owner"], ProcessIdentity.capture(os.getpid()))
    if owner is None:
        return
    try:
        _, cleanup = drive_lifecycle(
            path, anchor, plan, owner, provider, request["config"], context,
            ownership_evidence=request["evidence"],
        )
        if cleanup is not None:
            release, evidence = cleanup
            release_context = ExecutionContext(
                release.operation_id, monotonic_ns() + request["release_seconds"] * 1_000_000_000,
                str(uuid4()), context.correlation_id,
            )
            drive_lifecycle(
                path, anchor, release, owner, provider, request["config"], release_context,
                ownership_evidence=evidence,
            )
    except Exception:
        fence_operation(path, anchor, plan.operation_id)
        raise


def main():
    try:
        payload = sys.stdin.buffer.read(MAX_DISPATCH + 1)
        if len(payload) > MAX_DISPATCH or os.getsid(0) != os.getpid():
            return 1
        dispatch(json.loads(payload))
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
