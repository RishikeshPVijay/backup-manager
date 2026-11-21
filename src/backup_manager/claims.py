"""Transactional, generation-fenced repository claim-set primitives."""

from dataclasses import dataclass
from uuid import uuid4

from .state import StateUnsafe


@dataclass(frozen=True, order=True)
class RepositoryClaim:
    repository_key: str
    generation: int
    access_mode: str


def admit_repository_claims(database, operation_id, owner_id, requests, compatible_pairs=frozenset()):
    """Return an entire claim set, or None when any requested repo conflicts.

    The caller must hold the state write transaction and verified storage
    claims. With no certified compatibility rules all repository access is
    serialized.
    """
    ordered = sorted(requests)
    if not ordered or len({request.repository_key for request in ordered}) != len(ordered):
        raise ValueError("Repository claim set must contain distinct repositories.")
    owner = database.execute(
        "SELECT state FROM process_owners WHERE owner_id = ?", (owner_id,),
    ).fetchone()
    if owner != ("ACTIVE",):
        raise StateUnsafe("Claim owner is absent or fenced.")
    for request in ordered:
        registration = database.execute(
            "SELECT repo_generation FROM repository_registrations WHERE repository_key = ?",
            (request.repository_key,),
        ).fetchone()
        if registration != (request.generation,):
            raise StateUnsafe("Repository generation changed.")
        existing = database.execute(
            """SELECT claim.access_mode, claim_set.state, owner.state,
                      EXISTS (
                          SELECT 1 FROM command_executions AS command
                          WHERE command.owner_id = owner.owner_id
                          AND command.state IN ('LAUNCH_INTENT', 'LAUNCH_UNCERTAIN')
                      )
               FROM repository_claims AS claim
               JOIN repository_claim_sets AS claim_set USING (claim_set_id)
               JOIN process_owners AS owner USING (owner_id)
               WHERE claim.repository_key = ? AND claim_set.state != 'RELEASED'""",
            (request.repository_key,),
        )
        for access_mode, state, owner_state, uncertain in existing:
            pair = frozenset((access_mode, request.access_mode))
            if (
                state != "ACTIVE" or owner_state != "ACTIVE" or uncertain
                or pair not in compatible_pairs
                or access_mode == request.access_mode == "COPY_DESTINATION"
            ):
                return None
    claim_set_id = str(uuid4())
    database.execute(
        "INSERT INTO repository_claim_sets VALUES (?, ?, ?, 'ACTIVE')",
        (claim_set_id, operation_id, owner_id),
    )
    database.executemany(
        "INSERT INTO repository_claims VALUES (?, ?, ?)",
        [(claim_set_id, request.repository_key, request.access_mode) for request in ordered],
    )
    return claim_set_id


def repository_authorized(database, operation_id, owner_id):
    return database.execute(
        """SELECT 1 FROM repository_claim_sets AS claim
           JOIN process_owners AS owner USING (owner_id)
           WHERE claim.operation_id = ? AND claim.owner_id = ?
             AND claim.state = 'ACTIVE' AND owner.state = 'ACTIVE'""",
        (operation_id, owner_id),
    ).fetchone() is not None


def relinquish_repository_claims(database, operation_id, owner_id):
    if not repository_authorized(database, operation_id, owner_id):
        raise StateUnsafe("Repository claims are absent or require recovery.")
    unresolved = database.execute(
        """SELECT 1 FROM command_executions
           WHERE (operation_id = ? OR claim_operation_id = ?) AND owner_id = ?
           AND (state != 'TERMINATED' OR termination_evidence IS NULL) LIMIT 1""",
        (operation_id, operation_id, owner_id),
    ).fetchone()
    if unresolved is not None:
        raise StateUnsafe("Repository command termination is unconfirmed.")
    return database.execute(
        """UPDATE repository_claim_sets SET state = 'RELEASED'
           WHERE operation_id = ? AND owner_id = ?""",
        (operation_id, owner_id),
    ).rowcount == 1
