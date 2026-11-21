"""Conservative reconciliation of interrupted coordination owners."""

from dataclasses import dataclass
import json

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
    return not uncertain
