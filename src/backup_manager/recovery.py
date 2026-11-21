"""Explicit state recovery from an integrity-verified independent backup."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from uuid import uuid4

from .convergence import parse_native_inventory
from .crash_recovery import observe_owner
from .inspection import MountedInspector, observed_repository, read_adapter, read_command
from .jobs import destinations, seconds_deadline
from .launch_journal import event
from .process_identity import Presence
from .state import (
    MIGRATIONS, StateUnsafe, advance_witness, checked_record, connect, migration,
    private_file, state_lock, sync_directory,
)
from .transfer_state import record_inventory


def verify_drained(database):
    for statement in (
        "SELECT 1 FROM command_executions WHERE state != 'TERMINATED' OR termination_evidence IS NULL",
        "SELECT 1 FROM storage_operations WHERE state != 'TERMINATED'",
        "SELECT 1 FROM repository_initializations",
        "SELECT 1 FROM repository_claim_sets WHERE state != 'RELEASED'",
        "SELECT 1 FROM storage_claims WHERE state != 'RELEASED'",
        "SELECT 1 FROM job_runs WHERE finished_at IS NULL",
    ):
        if database.execute(statement + " LIMIT 1").fetchone() is not None:
            raise StateUnsafe("Unsettled claims or processes require operator reconciliation before recovery.")
    for owner, identity in database.execute(
        "SELECT owner_id, process_start_identity FROM process_owners WHERE state NOT IN ('INTERRUPTED', 'TERMINATED')",
    ):
        if not identity or observe_owner(owner, identity).presence is not Presence.GONE:
            raise StateUnsafe("An owner cannot be proved terminated.")


def recover(config, backup, jobs, *, timeout=None):
    path = Path(config["state"]["path"])
    anchor = Path(config["state"]["anchor_path"])
    deadline = seconds_deadline(min(timeout or 120, 120))
    with state_lock(path, anchor, exclusive=True, wait=5) as lock:
        private_file(anchor)
        witness = json.loads(anchor.read_text())
        manifest_path = backup.with_name(backup.name + ".manifest.json")
        private_file(backup)
        private_file(manifest_path)
        manifest = json.loads(manifest_path.read_text())
        if (
            manifest.get("schema_version") != 1
            or manifest.get("instance_id") != witness.get("instance_id")
            or type(manifest.get("generation")) is not int
            or type(witness.get("generation")) is not int
            or not 0 < manifest["generation"] <= witness["generation"]
        ):
            raise StateUnsafe("Backup identity or generation is incompatible with the independent witness.")
        for candidate in (backup, path):
            if any(os.path.lexists(candidate.with_name(candidate.name + suffix)) for suffix in ("-wal", "-shm", "-journal")):
                raise StateUnsafe("SQLite sidecars must be reconciled before recovery.")
        if manifest["generation"] < witness["generation"]:
            try:
                checked_record(path, anchor)
            except (StateUnsafe, OSError, sqlite3.Error, ValueError):
                raise StateUnsafe("An older backup cannot prove termination of operations missing from its ledger.") from None
            with closing(connect(path, "ro&immutable=1")) as live:
                verify_drained(live)
        with TemporaryDirectory(prefix=".backup-manager-recover-", dir=path.parent) as temporary:
            staged = Path(temporary) / "state.sqlite3"
            with backup.open("rb") as stream, staged.open("xb") as output:
                os.fchmod(output.fileno(), 0o600)
                digest = hashlib.sha256()
                while chunk := stream.read(1 << 20):
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != manifest.get("sha256"):
                raise StateUnsafe("Backup SHA-256 does not match its manifest.")
            with closing(connect(staged, "rw")) as database:
                if (
                    database.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
                    or database.execute("PRAGMA foreign_key_check").fetchone() is not None
                    or database.execute("SELECT instance_id, generation FROM instance_state").fetchall()
                    != [(manifest["instance_id"], manifest["generation"])]
                    or database.execute("SELECT version, checksum FROM schema_migrations ORDER BY version").fetchall()
                    != [(index, migration(name)[1]) for index, name in enumerate(MIGRATIONS, 1)]
                ):
                    raise StateUnsafe("Backup integrity or schema verification failed.")
                verify_drained(database)
                adapter = read_adapter(config, deadline, lock)
                inspector = MountedInspector(config)
                views = []
                for job_id, name in database.execute("SELECT job_id, destination_id FROM repository_registrations").fetchall():
                    if job_id not in jobs or name not in dict(destinations(jobs[job_id])):
                        raise StateUnsafe("Every backed-up registration needs a valid current configuration.")
                    expected, repository, binding, _ = observed_repository(
                        database, inspector, adapter, job_id, name, dict(destinations(jobs[job_id]))[name],
                        deadline, lock,
                    )
                    result = read_command(adapter.snapshots(repository), deadline / 1_000_000_000, lock, binding)
                    views.append((expected, parse_native_inventory(result)))
                next_witness = {**witness, "generation": witness["generation"] + 1}
                with database:
                    database.execute("UPDATE instance_state SET generation = ? WHERE singleton = 1", (next_witness["generation"],))
                    database.execute("UPDATE storage_ownership SET owned_by_manager = 0")
                    for expected, view in views:
                        record_inventory(database, expected, adapter.binary.sha256, view)
                    event(database, manifest["instance_id"], "state_recovered")
            with staged.open("rb") as stream:
                os.fsync(stream.fileno())
            if os.path.lexists(path):
                private_file(path)
                # Preserve the replaced file before publishing its replacement.
                archive = path.with_name(f"{path.name}.before-recovery-{uuid4().hex}")
                os.link(path, archive, follow_symlinks=False)
                sync_directory(path.parent)
            advance_witness(anchor, next_witness)
            os.replace(staged, path)
            sync_directory(path.parent)
        return {
            **next_witness, "restored_generation": manifest["generation"],
            "newer_history_may_be_missing": manifest["generation"] < witness["generation"],
            "verified_repositories": len(views),
        }
