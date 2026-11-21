"""Durable execution transitions. Observation and SQLite writes are separate."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from uuid import uuid4

from .process_identity import ProcessIdentity
from .state import StateUnsafe


@dataclass(frozen=True)
class LaunchIntent:
    execution_id: str
    operation_id: str
    owner_id: str
    supervisor_id: str


@dataclass(frozen=True)
class LaunchReceipt:
    supervisor_id: str
    containment_id: str
    process: ProcessIdentity
    cgroup_namespace: str | None = None


def now():
    return datetime.now(timezone.utc).isoformat()


def event(database, execution_id, kind):
    database.execute(
        "INSERT INTO events VALUES (?, ?, ?, '{}', ?)",
        (str(uuid4()), execution_id, kind, now()),
    )


def register_owner(database, identity, supervisor_id):
    owner_id = str(uuid4())
    database.execute(
        """INSERT INTO process_owners
           (owner_id, boot_id, pid, process_start_identity, supervisor_id, state)
           VALUES (?, ?, ?, ?, ?, 'ACTIVE')""",
        (owner_id, identity.boot_id, identity.pid, json.dumps(asdict(identity)), supervisor_id),
    )
    return owner_id


def record_intent(database, operation_id, owner_id, *, scope_gate=False):
    """The caller commits this intent before requesting a launch."""
    owner = database.execute(
        "SELECT supervisor_id FROM process_owners WHERE owner_id = ? AND state = 'ACTIVE'",
        (owner_id,),
    ).fetchone()
    if owner is None or not owner[0]:
        raise StateUnsafe("Launch owner has no active supervisor.")
    execution_id = str(uuid4())
    database.execute(
        """INSERT INTO command_executions
           (execution_id, operation_id, owner_id, state, launch_intent_at, process_identity_json)
           VALUES (?, ?, ?, 'LAUNCH_INTENT', ?, ?)""",
        (execution_id, operation_id, owner_id, now(), json.dumps({"supervisor_id": owner[0], "scope_gate": scope_gate})),
    )
    event(database, execution_id, "launch_intent")
    return LaunchIntent(execution_id, operation_id, owner_id, owner[0])


def execution(database, intent):
    row = database.execute(
        """SELECT command.state, command.process_identity_json, owner.boot_id,
                  owner.supervisor_id, owner.state
           FROM command_executions AS command
           JOIN process_owners AS owner USING (owner_id)
           WHERE command.execution_id = ? AND command.operation_id = ? AND command.owner_id = ?""",
        (intent.execution_id, intent.operation_id, intent.owner_id),
    ).fetchone()
    if row is None or row[3] != intent.supervisor_id:
        raise StateUnsafe("Execution intent is absent or fenced.")
    return row


def record_scope_ready(database, intent, receipt):
    state, payload, boot_id, supervisor_id, owner_state = execution(database, intent)
    if (
        state != "LAUNCH_INTENT" or owner_state != "ACTIVE"
        or not json.loads(payload).get("scope_gate")
        or json.loads(payload).get("containment_id") is not None
        or receipt.supervisor_id != supervisor_id or boot_id != receipt.process.boot_id
        or not receipt.cgroup_namespace or not receipt.containment_id
    ):
        raise StateUnsafe("The scope is not associated with the active launch gate.")
    database.execute(
        "UPDATE command_executions SET process_identity_json = ? WHERE execution_id = ?",
        (json.dumps({**asdict(receipt), "scope_gate": True}), intent.execution_id),
    )
    event(database, intent.execution_id, "scope_ready")


def record_launched(database, intent, receipt):
    state, payload, boot_id, supervisor_id, owner_state = execution(database, intent)
    if (
        state != "LAUNCH_INTENT" or owner_state != "ACTIVE"
        or receipt.supervisor_id != supervisor_id or receipt.process.boot_id != boot_id
        or not receipt.containment_id
    ):
        raise StateUnsafe("Launch receipt does not match the active intent.")
    prepared = json.loads(payload)
    if prepared.get("scope_gate") and (
        prepared.get("containment_id") != receipt.containment_id
        or prepared.get("cgroup_namespace") != receipt.cgroup_namespace
    ):
        raise StateUnsafe("Command launch differs from its prepared scope.")
    database.execute(
        """UPDATE command_executions SET state = 'LAUNCHED', launched_at = ?, process_identity_json = ?
           WHERE execution_id = ?""",
        (now(), json.dumps(asdict(receipt)), intent.execution_id),
    )
    event(database, intent.execution_id, "launched")


def fence_uncertain(database, intent):
    state, _, _, _, _ = execution(database, intent)
    if state == "LAUNCH_UNCERTAIN":
        return
    if state not in {"LAUNCH_INTENT", "LAUNCHED"}:
        raise StateUnsafe("Execution cannot transition to launch uncertainty.")
    database.execute(
        "UPDATE command_executions SET state = 'LAUNCH_UNCERTAIN' WHERE execution_id = ?",
        (intent.execution_id,),
    )
    database.execute(
        "UPDATE process_owners SET state = 'RECOVERY_REQUIRED' WHERE owner_id = ?",
        (intent.owner_id,),
    )
    database.execute(
        """UPDATE repository_claim_sets SET state = 'RECOVERY_REQUIRED'
           WHERE owner_id = ? AND state != 'RELEASED'""",
        (intent.owner_id,),
    )
    database.execute(
        """UPDATE storage_claims SET state = 'RECOVERY_REQUIRED'
           WHERE owner_id = ? AND state != 'RELEASED'""",
        (intent.owner_id,),
    )
    event(database, intent.execution_id, "launch_uncertain")


def record_terminated(database, intent, result):
    state, payload, _, _, owner_state = execution(database, intent)
    if state != "LAUNCHED" or owner_state != "ACTIVE":
        raise StateUnsafe("Execution lacks an active launch receipt.")
    receipt = json.loads(payload)
    if not result.containment or receipt["containment_id"] != result.containment:
        raise StateUnsafe("Termination evidence does not match the launch containment.")
    evidence = {"containment_id": result.containment, "observation": "empty_cgroup"}
    database.execute(
        """UPDATE command_executions SET state = 'TERMINATED', exit_code = ?, termination_evidence = ?
           WHERE execution_id = ?""",
        (result.exit_code, json.dumps(evidence), intent.execution_id),
    )
    event(database, intent.execution_id, "terminated")
