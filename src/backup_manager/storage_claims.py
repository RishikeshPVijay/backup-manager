"""Storage identity and claim admission under a state write transaction."""

from dataclasses import dataclass
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
             AND operation.state IN ('ACQUIRING', 'RELEASING', 'TERMINATION_UNCERTAIN', 'RECOVERY_REQUIRED')
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
