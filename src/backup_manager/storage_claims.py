"""Storage identity and claim admission under a state write transaction."""

from dataclasses import dataclass
import json
from uuid import uuid4

from .state import StateUnsafe


@dataclass(frozen=True)
class ResourceGeneration:
    resource_id: str
    coordination_id: str
    generation: int


def reconcile_identity(database, resource_id, identity, explicit_coordination_id):
    """Bind a first observation and refuse unapproved identity changes."""
    if identity.verified_physical:
        physical = identity.physical_identity
        if not physical:
            raise StateUnsafe("Verified storage identity has no physical identifier.")
        coordination = identity.coordination_id
        other = database.execute(
            """SELECT resource_id FROM storage_resources
               WHERE expected_identity = ? AND resource_id != ?""",
            (physical, resource_id),
        ).fetchone()
        if other is not None:
            raise StateUnsafe("Another resource already uses this verified filesystem.")
    else:
        physical = None
        coordination = explicit_coordination_id
        if not coordination:
            raise StateUnsafe("Unverified storage requires an explicit coordination identity.")
    current = database.execute(
        """SELECT coordination_id, expected_identity, identity_generation
           FROM storage_resources WHERE resource_id = ?""",
        (resource_id,),
    ).fetchone()
    if current is None:
        database.execute(
            """INSERT INTO storage_resources (resource_id, coordination_id, expected_identity)
               VALUES (?, ?, ?)""",
            (resource_id, coordination, physical),
        )
        return ResourceGeneration(resource_id, coordination, 1)
    if current[:2] != (coordination, physical):
        raise StateUnsafe("Storage identity differs from the registered generation.")
    return ResourceGeneration(resource_id, coordination, current[2])


def admit_storage_claim(database, resource, owner_id):
    """Attach a verified claimant, or wait while a conflicting transition runs."""
    current = database.execute(
        "SELECT coordination_id, identity_generation FROM storage_resources WHERE resource_id = ?",
        (resource.resource_id,),
    ).fetchone()
    owner = database.execute(
        "SELECT state FROM process_owners WHERE owner_id = ?", (owner_id,),
    ).fetchone()
    if current != (resource.coordination_id, resource.generation) or owner != ("ACTIVE",):
        raise StateUnsafe("Storage generation or owner is fenced.")
    conflict = database.execute(
        """SELECT 1 FROM storage_operations AS operation
           JOIN storage_resources AS resource USING (resource_id)
           WHERE resource.coordination_id = ?
             AND operation.state IN ('ACQUIRING', 'RELEASING', 'TERMINATION_UNCERTAIN', 'RECOVERY_REQUIRED', 'CANCEL_REQUESTED')
           UNION ALL
           SELECT 1 FROM storage_claims AS claim
           JOIN storage_resources AS resource USING (resource_id)
           JOIN process_owners AS owner USING (owner_id)
           WHERE resource.coordination_id = ? AND claim.state != 'RELEASED'
             AND (claim.state = 'RECOVERY_REQUIRED' OR owner.state != 'ACTIVE')
           LIMIT 1""",
        (resource.coordination_id, resource.coordination_id),
    ).fetchone()
    if conflict is not None:
        return None
    claim_id = str(uuid4())
    database.execute(
        "INSERT INTO storage_claims VALUES (?, ?, ?, ?, 'ACTIVE')",
        (claim_id, resource.resource_id, owner_id, resource.generation),
    )
    return claim_id


@dataclass(frozen=True)
class LifecyclePlan:
    operation_id: str
    kind: str
    leader: bool
    claim_id: str | None = None


def lifecycle_view(database, coordination_id):
    """Snapshot lifecycle metadata before inspecting a filesystem outside SQLite."""
    return tuple(database.execute(
        """SELECT operation.operation_id, operation.kind, operation.state
           FROM storage_operations AS operation
           JOIN storage_resources AS resource USING (resource_id)
           WHERE resource.coordination_id = ?
           ORDER BY operation.rowid""",
        (coordination_id,),
    ))


def plan_acquisition(database, resource, owner_id, observed_view, *, available, on_demand):
    if lifecycle_view(database, resource.coordination_id) != observed_view:
        return None
    unresolved = [row for row in observed_view if row[2] in {
        "ACQUIRING", "RELEASING", "TERMINATION_UNCERTAIN", "RECOVERY_REQUIRED", "CANCEL_REQUESTED",
    }]
    if unresolved:
        if len(unresolved) != 1 or unresolved[0][2] != "ACQUIRING":
            return None
        claim = insert_waiting_claim(database, resource, owner_id)
        return LifecyclePlan(unresolved[0][0], "acquire", False, claim)
    if available:
        claim = admit_storage_claim(database, resource, owner_id)
        return LifecyclePlan("", "reuse", False, claim) if claim else None
    if not on_demand or live_claims(database, resource.coordination_id):
        return None
    claim = insert_waiting_claim(database, resource, owner_id)
    operation_id = str(uuid4())
    database.execute(
        """INSERT INTO storage_operations (operation_id, resource_id, kind, worker_identity, state)
           VALUES (?, ?, 'acquire', ?, 'ACQUIRING')""",
        (operation_id, resource.resource_id, owner_id),
    )
    return LifecyclePlan(operation_id, "acquire", True, claim)


def insert_waiting_claim(database, resource, owner_id):
    current = database.execute(
        """SELECT coordination_id, identity_generation FROM storage_resources
           WHERE resource_id = ?""",
        (resource.resource_id,),
    ).fetchone()
    owner = database.execute(
        "SELECT state FROM process_owners WHERE owner_id = ?", (owner_id,),
    ).fetchone()
    if current != (resource.coordination_id, resource.generation) or owner != ("ACTIVE",):
        raise StateUnsafe("Storage generation or owner is fenced.")
    claim = str(uuid4())
    database.execute(
        "INSERT INTO storage_claims VALUES (?, ?, ?, ?, 'WAITING')",
        (claim, resource.resource_id, owner_id, resource.generation),
    )
    return claim


def live_claims(database, coordination_id):
    return bool(database.execute(
        """SELECT 1 FROM storage_claims AS claim
           JOIN storage_resources AS resource USING (resource_id)
           WHERE resource.coordination_id = ? AND claim.state != 'RELEASED'
           LIMIT 1""",
        (coordination_id,),
    ).fetchone())


def drop_claim(database, claim_id, owner_id):
    row = database.execute(
        """SELECT claim.state, resource.coordination_id FROM storage_claims AS claim
           JOIN storage_resources AS resource USING (resource_id)
           WHERE claim_id = ? AND owner_id = ?""",
        (claim_id, owner_id),
    ).fetchone()
    if row is None or row[0] not in {"WAITING", "ACTIVE"}:
        raise StateUnsafe("Storage claim is absent or recovery is required.")
    state, coordination_id = row
    if state == "ACTIVE" and database.execute(
        """SELECT 1 FROM repository_claim_sets
           WHERE owner_id = ? AND state != 'RELEASED' LIMIT 1""",
        (owner_id,),
    ).fetchone() is not None:
        raise StateUnsafe("Release repository claims before relinquishing storage.")
    database.execute("UPDATE storage_claims SET state = 'RELEASED' WHERE claim_id = ?", (claim_id,))
    if live_claims(database, coordination_id):
        return None
    if state == "WAITING":
        operation = database.execute(
            """SELECT operation_id FROM storage_operations
               JOIN storage_resources USING (resource_id)
               WHERE coordination_id = ? AND state = 'ACQUIRING'""",
            (coordination_id,),
        ).fetchone()
        if operation is not None:
            database.execute(
                "UPDATE storage_operations SET state = 'CANCEL_REQUESTED' WHERE operation_id = ?",
                operation,
            )
            return LifecyclePlan(operation[0], "cancel", True)
    return None


def complete_acquisition(database, operation_id, *, available, ownership_evidence, cancelled_clean=False):
    row = database.execute(
        """SELECT operation.resource_id, operation.state, resource.coordination_id, resource.identity_generation
           FROM storage_operations AS operation JOIN storage_resources AS resource USING (resource_id)
           WHERE operation.operation_id = ? AND operation.kind = 'acquire'""",
        (operation_id,),
    ).fetchone()
    if row is None or row[1] not in {"ACQUIRING", "CANCEL_REQUESTED"}:
        raise StateUnsafe("Acquisition is absent or fenced.")
    command = database.execute(
        """SELECT command.state, command.exit_code FROM command_executions AS command
           JOIN storage_operations AS operation USING (operation_id)
           WHERE command.operation_id = ? AND command.owner_id = operation.worker_identity""",
        (operation_id,),
    ).fetchone()
    if command is None or command[0] != "TERMINATED":
        raise StateUnsafe("Acquisition containment termination has not been confirmed.")
    resource_id, previous, coordination_id, generation = row
    if ownership_evidence and (not available or command[1] != 0):
        raise StateUnsafe("Acquisition ownership has inconsistent physical evidence.")
    if available and command[1] == 0 and ownership_evidence:
        stored = database.execute(
            """INSERT INTO storage_ownership VALUES (?, ?, 1, ?)
               ON CONFLICT(resource_id, generation) DO UPDATE
               SET owned_by_manager = 1, evidence_json = excluded.evidence_json
               WHERE storage_ownership.owned_by_manager = 0""",
            (resource_id, generation, json.dumps(ownership_evidence)),
        )
        if stored.rowcount != 1:
            raise StateUnsafe("Existing ownership requires physical reconciliation.")
    cancelled_with_no_mount = (
        previous == "CANCEL_REQUESTED" and command[1] is None
        and cancelled_clean and not available and not ownership_evidence
    )
    database.execute(
        "UPDATE storage_operations SET state = ? WHERE operation_id = ?",
        ("TERMINATED" if command[1] == 0 or cancelled_with_no_mount else "RECOVERY_REQUIRED", operation_id),
    )
    if command[1] != 0 or previous == "CANCEL_REQUESTED":
        return
    state = "ACTIVE" if available else "RELEASED"
    database.execute(
        """UPDATE storage_claims SET state = ? WHERE state = 'WAITING'
           AND resource_id IN (SELECT resource_id FROM storage_resources WHERE coordination_id = ?)
           AND owner_id IN (SELECT owner_id FROM process_owners WHERE state = 'ACTIVE')""",
        (state, coordination_id),
    )


def plan_release(database, coordination_id, owner_id):
    """Reserve release only after the last user and before any new acquisition."""
    owner = database.execute(
        "SELECT state FROM process_owners WHERE owner_id = ?", (owner_id,),
    ).fetchone()
    if owner != ("ACTIVE",):
        raise StateUnsafe("Release owner is fenced.")
    if live_claims(database, coordination_id) or any(
        row[2] != "TERMINATED" for row in lifecycle_view(database, coordination_id)
    ):
        return None
    ownership = database.execute(
        """SELECT ownership.resource_id, ownership.evidence_json
           FROM storage_ownership AS ownership JOIN storage_resources AS resource USING (resource_id)
           WHERE resource.coordination_id = ? AND ownership.owned_by_manager = 1
             AND ownership.generation = resource.identity_generation""",
        (coordination_id,),
    ).fetchall()
    if not ownership:
        return None
    if len(ownership) != 1:
        raise StateUnsafe("Conflicting storage ownership evidence.")
    resource_id, evidence = ownership[0]
    operation_id = str(uuid4())
    database.execute(
        """INSERT INTO storage_operations (operation_id, resource_id, kind, worker_identity, state)
           VALUES (?, ?, 'release', ?, 'RELEASING')""",
        (operation_id, resource_id, owner_id),
    )
    return LifecyclePlan(operation_id, "release", True), json.loads(evidence)


def complete_release(database, operation_id, *, physical_release_confirmed):
    row = database.execute(
        """SELECT operation.resource_id, resource.identity_generation, command.state, command.exit_code
           FROM storage_operations AS operation
           JOIN storage_resources AS resource USING (resource_id)
           LEFT JOIN command_executions AS command
             ON command.operation_id = operation.operation_id AND command.owner_id = operation.worker_identity
           WHERE operation.operation_id = ? AND operation.kind = 'release'
             AND operation.state = 'RELEASING'""",
        (operation_id,),
    ).fetchone()
    if row is None:
        raise StateUnsafe("Release is absent or fenced.")
    resource_id, generation, command_state, exit_code = row
    if command_state != "TERMINATED" or exit_code != 0 or not physical_release_confirmed:
        database.execute(
            "UPDATE storage_operations SET state = 'RECOVERY_REQUIRED' WHERE operation_id = ?",
            (operation_id,),
        )
        return False
    database.execute(
        """UPDATE storage_ownership SET owned_by_manager = 0
           WHERE resource_id = ? AND generation = ?""",
        (resource_id, generation),
    )
    database.execute(
        "UPDATE storage_operations SET state = 'TERMINATED' WHERE operation_id = ?",
        (operation_id,),
    )
    return True
