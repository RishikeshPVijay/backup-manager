"""Explicit repository initialization and identity replacement."""

import os
from pathlib import Path
from uuid import uuid4

from .errors import Diagnostic, Result, Status
from .job_repository import inspect_repository
from .jobs import DestinationRejected, JobRunner, JobStopped, destinations
from .launch_journal import event, now
from .restic import ResticEvidenceError, ResticUnsupported, snapshot_id
from .state import StateUnsafe
from .supervisor import SupervisionUncertain, run_journaled


def initialize_repository(runner, adapter, lease, repo):
    key = (lease.coordination_id, str(repo.path))
    with runner.storage.write() as database:
        prior = database.execute(
            "SELECT state FROM repository_initializations WHERE coordination_id = ? AND repository_path = ?", key,
        ).fetchone()
        registered = database.execute(
            """SELECT 1 FROM repository_registrations AS registration
               JOIN storage_resources AS storage ON storage.resource_id = registration.storage_id
               WHERE storage.coordination_id = ? AND registration.repository_path = ?""", key,
        ).fetchone()
        if prior or registered:
            raise StateUnsafe("Repository path is registered or already fenced for initialization.")
        database.execute(
            "INSERT INTO repository_initializations VALUES (?, ?, ?, 'INITIALIZING')",
            (*key, runner.storage.owner),
        )
    if not repo.path.exists():
        from .scope_worker import directory_identity

        parent = inspect_repository(runner.storage, lease, repo.path.parent, runner.operation_deadline())
        fd = os.open(repo.path.parent, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            if directory_identity(fd) != parent["identity"]:
                raise StateUnsafe("Repository parent changed before creation.")
            os.mkdir(repo.path.name, 0o700, dir_fd=fd)
        finally:
            os.close(fd)
    binding = inspect_repository(runner.storage, lease, repo.path, runner.operation_deadline())
    if any(repo.path.iterdir()):
        raise StateUnsafe("Repository initialization requires an empty directory.")
    invocation = adapter.explicit_init(repo, max(adapter.profile.repository_formats))
    result = run_journaled(
        runner.storage.path, runner.storage.anchor, str(uuid4()), runner.storage.owner,
        invocation.argv, env=invocation.env, deadline=runner.operation_deadline() / 1_000_000_000,
        repository={**binding, "empty": True}, cancelled=runner.cancelled,
    )
    if result.exit_code != 0:
        raise ResticEvidenceError("Repository initialization did not succeed.")


def approve_replacement(config, job, target_id, expected_id, *, timeout=None, interrupted=lambda: False):
    try:
        snapshot_id(expected_id)
    except ResticEvidenceError:
        return Result(Status.INVALID, errors=(Diagnostic("EXPECTED_ID_INVALID", "Supply the complete expected repository ID."),))
    runner = JobRunner(config, job, timeout=timeout, interrupted=interrupted)
    selected = None
    try:
        adapter = runner.load_restic()
        selected = runner.get_destination(
            adapter, target_id, dict(destinations(job))[target_id],
            access_mode="REPLACEMENT", expected_id=expected_id,
        )
        registered = selected.registration
        with runner.storage.write() as database:
            if database.execute(
                "SELECT 1 FROM repository_registrations WHERE restic_repository_id = ? AND repository_key != ?",
                (expected_id, registered.key),
            ).fetchone():
                raise StateUnsafe("The expected ID is assigned to another destination.")
            if registered.restic_id != expected_id:
                updated = database.execute(
                    """UPDATE repository_registrations SET restic_repository_id = ?, repo_generation = repo_generation + 1,
                                                          approved_at = ?
                       WHERE repository_key = ? AND repo_generation = ? AND restic_repository_id = ?""",
                    (expected_id, now(), registered.key, registered.generation, registered.restic_id),
                )
                if updated.rowcount != 1:
                    raise StateUnsafe("Repository generation changed before approval.")
                event(database, registered.key, "repository_replacement_approved")
        return Result(data={"destination_id": target_id, "repository_id": expected_id})
    except ResticUnsupported:
        return Result(Status.UNSUPPORTED)
    except JobStopped:
        return Result(Status.CANCELLED if runner.cancelled() else Status.FAILED)
    except (DestinationRejected, StateUnsafe, OSError, SupervisionUncertain):
        return Result(Status.UNSAFE, errors=(Diagnostic("REPLACEMENT_UNVERIFIED", "Repository replacement could not be verified."),))
    finally:
        if not runner.fenced:
            if selected is not None:
                runner.storage.release_repositories(selected.claim_id)
                runner.storage.release(selected.lease)
            with runner.storage.write() as database:
                database.execute("UPDATE process_owners SET state = 'TERMINATED' WHERE owner_id = ?", (runner.storage.owner,))
