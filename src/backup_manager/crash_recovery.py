"""Conservative reconciliation of interrupted coordination owners."""

from dataclasses import dataclass
import json

from .launch_journal import event
from .process_identity import Presence, ProcessIdentity
from .state import StateUnsafe


@dataclass(frozen=True)
class OwnerObservation:
    owner_id: str
    identity: ProcessIdentity
    presence: Presence


def observe_owner(owner_id, identity_json):
    identity = ProcessIdentity(**json.loads(identity_json))
    return OwnerObservation(owner_id, identity, identity.presence())


def reconcile_owner(database, observation):
    """Fence uncertain ownership and reclaim only after known containment exit."""
    row = database.execute(
        "SELECT process_start_identity, state FROM process_owners WHERE owner_id = ?",
        (observation.owner_id,),
    ).fetchone()
    if row is None or not row[0] or ProcessIdentity(**json.loads(row[0])) != observation.identity:
        raise StateUnsafe("Observed owner identity no longer matches state.")
    if observation.presence is Presence.SAME:
        return False
    if row[1] == "INTERRUPTED":
        return True
    unresolved_command = database.execute(
        """SELECT 1 FROM command_executions
           WHERE owner_id = ?
           AND (state != 'TERMINATED' OR termination_evidence IS NULL) LIMIT 1""",
        (observation.owner_id,),
    ).fetchone()
    uncertain = observation.presence is not Presence.GONE or unresolved_command is not None
    owner_state = "RECOVERY_REQUIRED" if uncertain else "INTERRUPTED"
    claim_state = "RECOVERY_REQUIRED" if uncertain else "RELEASED"
    database.execute(
        "UPDATE process_owners SET state = ? WHERE owner_id = ?",
        (owner_state, observation.owner_id),
    )
    for table in ("repository_claim_sets", "storage_claims"):
        database.execute(
            f"UPDATE {table} SET state = ? WHERE owner_id = ? AND state != 'RELEASED'",
            (claim_state, observation.owner_id),
        )
    database.execute(
        """UPDATE storage_operations SET state = 'RECOVERY_REQUIRED'
           WHERE worker_identity = ? AND state != 'TERMINATED'""",
        (observation.owner_id,),
    )
    if not uncertain:
        database.execute(
            """UPDATE copy_attempts SET state = 'INTERRUPTED', error_code = 'TRANSFER_INTERRUPTED'
               WHERE run_id IN (SELECT run_id FROM job_runs WHERE owner_id = ? AND finished_at IS NULL)
               AND state IN ('PLANNED', 'LAUNCH_INTENT', 'RUNNING', 'LAUNCH_UNCERTAIN')""",
            (observation.owner_id,),
        )
    return not uncertain


@dataclass(frozen=True)
class ScopeObservation:
    execution_id: str
    receipt_json: str
    proof: str


def observe_interrupted_scope(execution_id, receipt_json):
    """Inspect durable launch-gate evidence outside the SQLite write transaction."""
    import os
    from pathlib import Path
    from uuid import UUID

    from .supervisor import CGROUP_ROOT, empty_cgroup

    receipt = json.loads(receipt_json)
    name = receipt.get("containment_id")
    if not name:
        if receipt.get("scope_gate") is True:
            return ScopeObservation(execution_id, receipt_json, "command_gate_never_opened")
        return None
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or relative.name != f"backup-manager-{UUID(execution_id).hex}.scope":
        return None
    process = ProcessIdentity(**receipt["process"])
    if (
        process.presence() is Presence.GONE
        and receipt.get("cgroup_namespace") == os.readlink("/proc/self/ns/cgroup")
        and Path("/proc/sys/kernel/random/boot_id").read_text().strip() == process.boot_id
        and empty_cgroup(CGROUP_ROOT / relative)
    ):
        return ScopeObservation(execution_id, receipt_json, "empty_recorded_cgroup")
    return None


def record_interruption(database, owner, scope):
    """Persist an interrupted command after testing the owner and scope identities."""
    if owner.presence is not Presence.GONE:
        return False
    row = database.execute(
        """SELECT command.state, command.process_identity_json, process.process_start_identity
           FROM command_executions AS command JOIN process_owners AS process USING (owner_id)
           WHERE command.execution_id = ? AND command.owner_id = ?""",
        (scope.execution_id, owner.owner_id),
    ).fetchone()
    if (
        row is None or row[1] != scope.receipt_json or not row[2]
        or ProcessIdentity(**json.loads(row[2])) != owner.identity
        or row[0] not in {"LAUNCH_INTENT", "LAUNCHED", "LAUNCH_UNCERTAIN"}
    ):
        return False
    database.execute(
        """UPDATE command_executions SET state = 'TERMINATED', termination_evidence = ?
           WHERE execution_id = ?""",
        (json.dumps({"interrupted": True, "observation": scope.proof}), scope.execution_id),
    )
    database.execute(
        """UPDATE copy_attempts SET state = 'INTERRUPTED', error_code = 'TRANSFER_INTERRUPTED'
           WHERE execution_id = ? AND state IN ('LAUNCH_INTENT', 'RUNNING', 'LAUNCH_UNCERTAIN')""",
        (scope.execution_id,),
    )
    event(database, scope.execution_id, "interrupted")
    return True


def reconcile_crashed_owners(path, anchor):
    """One pass. This function frees proven stale claims and performs no unmount."""
    from .state import state_read, state_write

    with state_read(path, anchor, wait=5) as database:
        owners = database.execute(
            """SELECT owner_id, process_start_identity FROM process_owners
               WHERE state IN ('ACTIVE', 'RECOVERY_REQUIRED') AND process_start_identity IS NOT NULL""",
        ).fetchall()
        commands = database.execute(
            """SELECT owner_id, execution_id, process_identity_json FROM command_executions
               WHERE state IN ('LAUNCH_INTENT', 'LAUNCHED', 'LAUNCH_UNCERTAIN')""",
        ).fetchall()
    impacted = []
    for owner_id, identity in owners:
        observation = observe_owner(owner_id, identity)
        if observation.presence is Presence.SAME:
            continue
        proofs = [
            scope for command_owner, execution_id, receipt in commands if command_owner == owner_id
            if (scope := observe_interrupted_scope(execution_id, receipt)) is not None
        ]
        with state_write(path, anchor, wait=5) as database:
            for proof in proofs:
                record_interruption(database, observation, proof)
            impacted.append((owner_id, reconcile_owner(database, observation)))
    return impacted
