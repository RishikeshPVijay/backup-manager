"""Durable inventories, provenance, and independent transfer outcomes."""

import json
from uuid import uuid4

from .convergence import logical_identity
from .launch_journal import event, now
from .state import StateUnsafe


def record_inventory(database, registration, profile_id, inventory):
    key, generation = registration.key, registration.generation
    if database.execute(
        "SELECT repo_generation FROM repository_registrations WHERE repository_key = ?", (key,),
    ).fetchone() != (generation,):
        raise StateUnsafe("Inventory repository generation changed.")
    previous = dict(database.execute(
        """SELECT snapshot_id, logical_identity FROM snapshot_provenance
           WHERE repository_key = ? AND repo_generation = ? AND profile_id = ?""",
        (key, generation, profile_id),
    ))
    identities = {} if inventory is None else {snapshot.id: logical_identity(snapshot) for snapshot in inventory}
    if any(snapshot in previous and previous[snapshot] != logical for snapshot, logical in identities.items()):
        raise StateUnsafe("A previously observed snapshot ID returned different provenance.")
    enumeration = str(uuid4())
    status = "failed" if inventory is None else "complete"
    database.execute(
        "INSERT INTO snapshot_enumerations VALUES (?, ?, ?, ?, ?)",
        (enumeration, key, generation, status, now()),
    )
    if inventory is not None:
        database.executemany(
            "INSERT INTO snapshot_observations VALUES (?, ?, ?, ?, ?)",
            [(enumeration, item.id, item.original, item.tree, json.dumps({"logical_identity": identities[item.id]})) for item in inventory],
        )
        database.execute(
            "DELETE FROM snapshot_provenance WHERE repository_key = ? AND repo_generation = ? AND profile_id = ?",
            (key, generation, profile_id),
        )
        database.executemany(
            "INSERT INTO snapshot_provenance VALUES (?, ?, ?, ?, ?)",
            [(key, generation, snapshot, logical, profile_id) for snapshot, logical in identities.items()],
        )
    # A failed inventory leaves the last complete view and its provenance intact.
    old = database.execute(
        """SELECT enumeration_id FROM snapshot_enumerations
           WHERE repository_key = ? AND repo_generation = ? AND status = ? AND enumeration_id != ?""",
        (key, generation, status, enumeration),
    ).fetchall()
    database.executemany("DELETE FROM snapshot_observations WHERE enumeration_id = ?", old)
    database.executemany("DELETE FROM snapshot_enumerations WHERE enumeration_id = ?", old)


def requirements(database, run_id, required):
    database.executemany(
        """INSERT INTO transfer_requirements VALUES (?, ?, ?, ?, ?, 'PENDING')
           ON CONFLICT(run_id, requirement_id) DO NOTHING""",
        [(run_id, item.id, item.logical, item.destination_key, item.destination_generation) for item in required],
    )


def plan_attempt(database, run_id, phase, plan, source, destination):
    attempt = str(uuid4())
    requirements(database, run_id, plan.requirements)
    database.execute(
        """INSERT INTO copy_attempts
           (attempt_id, run_id, source_key, destination_key, snapshot_ids_json, requirement_ids_json,
            state, phase, source_generation, destination_generation)
           VALUES (?, ?, ?, ?, ?, ?, 'PLANNED', ?, ?, ?)""",
        (attempt, run_id, source.key, destination.key, json.dumps([item.id for item in plan.snapshots]),
         json.dumps([item.id for item in plan.requirements]), phase, source.generation, destination.generation),
    )
    event(database, attempt, "transfer_planned")
    return attempt


def finish_attempt(database, attempt, outcome, *, code=None, profile_id=None, evidence=()):
    row = database.execute(
        """SELECT run_id, requirement_ids_json, state, launched, snapshot_ids_json, execution_id
           FROM copy_attempts WHERE attempt_id = ?""",
        (attempt,),
    ).fetchone()
    if row is None or row[2] not in {"PLANNED", "LAUNCH_INTENT", "RUNNING"}:
        raise StateUnsafe("Transfer attempt cannot be finalized twice.")
    run, assigned, state, launched, frozen, execution_id = row
    if outcome not in {"SUCCEEDED", "FAILED", "UNVERIFIED", "PRELAUNCH_FAILED"}:
        raise ValueError("Unsupported terminal transfer state.")
    command = database.execute(
        "SELECT state, exit_code, termination_evidence FROM command_executions WHERE execution_id = ?",
        (execution_id,),
    ).fetchone()
    if command is not None and (command[0] != "TERMINATED" or not command[2]):
        raise StateUnsafe("Transfer command has no verified termination.")
    if outcome == "PRELAUNCH_FAILED":
        if launched or (state != "PLANNED" and command is None):
            raise StateUnsafe("A started transfer cannot be classified as a prelaunch failure.")
    elif state != "RUNNING" or not launched or command is None:
        raise StateUnsafe("Transfer outcome requires a durable launch receipt.")
    if outcome == "SUCCEEDED":
        snapshot_ids = json.loads(frozen)
        if (
            command[1] != 0 or not profile_id or len(evidence) != len(snapshot_ids)
            or {source for source, _ in evidence} != set(snapshot_ids)
        ):
            raise StateUnsafe("Transfer success requires complete evidence for the frozen source set.")
    database.execute("UPDATE copy_attempts SET state = ?, error_code = ? WHERE attempt_id = ?", (outcome, code, attempt))
    if outcome == "SUCCEEDED":
        database.executemany(
            "INSERT INTO copy_evidence VALUES (?, ?, ?, ?, ?, ?)",
            [(str(uuid4()), attempt, source, destination, profile_id, now()) for source, destination in evidence],
        )
    if outcome in {"SUCCEEDED", "FAILED", "UNVERIFIED"}:
        state = "SATISFIED" if outcome == "SUCCEEDED" else "BLOCKED"
        database.executemany(
            """UPDATE transfer_requirements SET state = ? WHERE run_id = ? AND requirement_id = ?
               AND state != 'BLOCKED'""",
            [(state, run, item) for item in json.loads(assigned)],
        )
    event(database, attempt, "transfer_" + outcome.lower())


def blocked_requirements(database, run_id):
    return {
        row[0] for row in database.execute(
            "SELECT requirement_id FROM transfer_requirements WHERE run_id = ? AND state = 'BLOCKED'",
            (run_id,),
        )
    }


def completed_requirements(database, run_id):
    return {
        row[0] for row in database.execute(
            """SELECT assigned.value FROM copy_attempts AS attempt, json_each(attempt.requirement_ids_json) AS assigned
               WHERE attempt.run_id = ? AND attempt.state = 'SUCCEEDED'""",
            (run_id,),
        )
    }
