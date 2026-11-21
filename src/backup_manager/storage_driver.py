"""Execute and reconcile a single durable shared-storage lifecycle plan."""

import asyncio
from dataclasses import asdict
import json

from .plugin_api import StorageState
from .state import StateUnsafe, state_write
from .storage_claims import complete_acquisition, complete_release, plan_release
from .storage_execution import execute_lifecycle
from .supervisor import SupervisionUncertain


def verified_reply(result, inspection):
    if result.exit_code != 0 or result.truncated:
        return None
    try:
        receipt = json.loads(result.stdout)
        if (
            type(receipt["changed_physical_state"]) is not bool
            or not isinstance(receipt["ownership_evidence"], dict)
            or any(type(key) is not str or type(value) is not str for key, value in receipt["ownership_evidence"].items())
            or receipt["inspection"] != asdict(inspection)
        ):
            return None
        return receipt
    except (ValueError, TypeError, KeyError):
        return None


def fence_operation(path, anchor, operation_id):
    with state_write(path, anchor, wait=5) as database:
        database.execute(
            """UPDATE storage_operations SET state = 'RECOVERY_REQUIRED'
               WHERE operation_id = ? AND state IN ('ACQUIRING', 'CANCEL_REQUESTED', 'RELEASING')""",
            (operation_id,),
        )


def drive_lifecycle(path, anchor, plan, owner_id, provider, config, context, *, ownership_evidence=None):
    try:
        result = execute_lifecycle(
            path, anchor, plan, owner_id, provider.provider_id, config, context, ownership_evidence,
        )
        physical = asyncio.run(provider.inspect(config, context))
    except (SupervisionUncertain, TimeoutError, StateUnsafe):
        fence_operation(path, anchor, plan.operation_id)
        raise
    except Exception:
        fence_operation(path, anchor, plan.operation_id)
        raise StateUnsafe("Storage inspection failed after worker exit.") from None

    receipt = verified_reply(result, physical)
    absent = physical.state is StorageState.UNAVAILABLE and not physical.mounted
    if provider.provider_id == "linux_filesystem":
        absent = absent and physical.details.get("mount_count") == "0"

    if plan.kind == "release":
        with state_write(path, anchor, wait=5) as database:
            released = complete_release(
                database, plan.operation_id,
                physical_release_confirmed=bool(receipt and absent and receipt["changed_physical_state"]),
            )
        return released, None

    available = physical.state is StorageState.AVAILABLE and physical.mounted
    evidence = receipt["ownership_evidence"] if receipt else {}
    if result.exit_code == 0 and (
        not receipt or not available
        or (receipt["changed_physical_state"] and (not evidence or evidence != physical.identity.evidence))
        or (not receipt["changed_physical_state"] and evidence)
    ):
        fence_operation(path, anchor, plan.operation_id)
        raise StateUnsafe("Acquisition result does not match fresh physical evidence.")
    with state_write(path, anchor, wait=5) as database:
        row = database.execute(
            """SELECT operation.state, resource.coordination_id, resource.expected_identity
               FROM storage_operations AS operation JOIN storage_resources AS resource USING (resource_id)
               WHERE operation.operation_id = ?""",
            (plan.operation_id,),
        ).fetchone()
        if row is None:
            raise StateUnsafe("Acquisition plan is absent.")
        prior, coordination, expected = row
        if available and expected is not None and (
            not physical.identity.verified_physical or physical.identity.physical_identity != expected
        ):
            database.execute(
                "UPDATE storage_operations SET state = 'RECOVERY_REQUIRED' WHERE operation_id = ?",
                (plan.operation_id,),
            )
            return False, None
        complete_acquisition(
            database, plan.operation_id, available=bool(receipt and available),
            ownership_evidence=evidence if result.exit_code == 0 else {},
            absence_confirmed=absent,
        )
        # Reserve cleanup atomically before another claimant can reuse the mount.
        cleanup = plan_release(database, coordination, owner_id) if prior == "CANCEL_REQUESTED" and evidence else None
    return bool(receipt and available and prior != "CANCEL_REQUESTED"), cleanup
