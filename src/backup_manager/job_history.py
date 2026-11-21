"""Durable admission, phase results, cancellation and history."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from uuid import uuid4

from .config import plain
from .launch_journal import event, now


def admit(database, owner, job_id, job, maximum):
    for run_id, owner_state in database.execute(
        """SELECT job_runs.run_id, process_owners.state
           FROM job_runs LEFT JOIN process_owners USING (owner_id)
           WHERE finished_at IS NULL AND (owner_id IS NULL OR process_owners.state != 'ACTIVE')"""
    ):
        state = "INTERRUPTED" if owner_state == "INTERRUPTED" else "RECOVERY_REQUIRED"
        database.execute(
            "UPDATE job_runs SET state = ?, overall_outcome = ?, finished_at = ? WHERE run_id = ?",
            (state, "interrupted", now() if state == "INTERRUPTED" else None, run_id),
        )
    if database.execute(
        "SELECT 1 FROM job_runs WHERE job_id = ? AND finished_at IS NULL", (job_id,),
    ).fetchone():
        return None, "JOB_BUSY"
    if database.execute("SELECT count(*) FROM job_runs WHERE finished_at IS NULL").fetchone()[0] >= maximum:
        return None, "GLOBAL_BUSY"
    run_id = str(uuid4())
    fingerprint = hashlib.sha256(json.dumps(plain(job), sort_keys=True).encode()).hexdigest()
    database.execute(
        """INSERT INTO job_runs (run_id, job_id, config_fingerprint, state, started_at, owner_id)
           VALUES (?, ?, ?, 'ADMITTED', ?, ?)""",
        (run_id, job_id, fingerprint, now(), owner),
    )
    event(database, run_id, "job_admitted")
    return run_id, None


def phase(database, run_id, name, status, *, target=None, code=None, data=None):
    database.execute(
        "INSERT INTO phase_results VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid4()), run_id, name, target, status, code, json.dumps(data or {})),
    )


def transition(database, run_id, name):
    database.execute("UPDATE job_runs SET state = ? WHERE run_id = ? AND finished_at IS NULL", (name, run_id))
    event(database, run_id, name.lower())


def finish(database, run_id, status, backup, destination, snapshot, *, fenced=False):
    final_state = "RECOVERY_REQUIRED" if fenced else ("CANCELLED" if status == "cancelled" else "COMPLETED")
    database.execute(
        """UPDATE job_runs SET state = ?, overall_outcome = ?, backup_outcome = ?,
           destination_id = ?, snapshot_id = ?, finished_at = ? WHERE run_id = ?""",
        (final_state, status, backup, destination, snapshot, None if fenced else now(), run_id),
    )
    event(database, run_id, "job_outcome")


def request_cancel(database, run_id):
    row = database.execute(
        "SELECT state, finished_at, cancel_requested FROM job_runs WHERE run_id = ?", (run_id,),
    ).fetchone()
    if row is None:
        return False
    if row[1] is not None or row[0] == "RECOVERY_REQUIRED":
        return False
    if not row[2]:
        database.execute("UPDATE job_runs SET cancel_requested = 1 WHERE run_id = ?", (run_id,))
        event(database, run_id, "cancel_requested")
    return True


def prune_history(database, job_id, logging, keep_run=None):
    days = logging.get("history_days")
    count = logging.get("history_count")
    current = datetime.now(timezone.utc)
    cutoff = (current - timedelta(days=days)).isoformat() if days and days < current.toordinal() else None
    runs = database.execute(
        """SELECT run_id, finished_at FROM job_runs
           WHERE job_id = ? AND finished_at IS NOT NULL ORDER BY finished_at DESC, rowid DESC""",
        (job_id,),
    ).fetchall()
    for index, (run_id, ended) in enumerate(runs):
        if run_id == keep_run or not ((cutoff and ended < cutoff) or (count and index >= count)):
            continue
        # Provenance and transfer audit objects are durable beyond I5 history.
        if database.execute("SELECT 1 FROM copy_attempts WHERE run_id = ?", (run_id,)).fetchone():
            continue
        database.execute("DELETE FROM phase_results WHERE run_id = ?", (run_id,))
        database.execute("DELETE FROM job_runs WHERE run_id = ?", (run_id,))


def run_rows(database, *, job_id=None, run_id=None, limit=100, active_only=False):
    database.row_factory = sqlite3.Row
    return [dict(row) for row in database.execute(
        """SELECT run_id, job_id, state, backup_outcome, overall_outcome, started_at,
                  finished_at, destination_id, snapshot_id, cancel_requested
           FROM job_runs WHERE (? IS NULL OR job_id = ?) AND (? IS NULL OR run_id = ?)
           AND (? = 0 OR finished_at IS NULL)
           ORDER BY started_at DESC, rowid DESC LIMIT ?""",
        (job_id, job_id, run_id, run_id, active_only, limit),
    )]


def phase_rows(database, run_id):
    rows = database.execute(
        "SELECT phase, target_id, status, error_code, details_json FROM phase_results WHERE run_id = ? ORDER BY rowid",
        (run_id,),
    )
    return [
        {"phase": name, "target_id": target, "status": status, "error_code": code, "data": json.loads(data)}
        for name, target, status, code, data in rows
    ]
