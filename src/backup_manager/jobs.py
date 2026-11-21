"""I5 orchestration for a frozen, explicitly configured backup job."""

from dataclasses import dataclass
from pathlib import Path
from time import monotonic_ns, sleep
from uuid import uuid4

from .claims import RepositoryClaim, repository_authorized
from .config import EXECUTION_DEFAULTS
from .errors import Diagnostic, Result, Status
from .job_history import admit, finish, phase, prune_history, transition
from .job_hooks import hook_environment, redact_output, repository_password
from .job_repository import Registration, inspect_repository, registration
from .launch_journal import event, now
from .providers import discover
from .restic import (
    Binary, Repository, Restic, ResticEvidenceError, ResticUnsupported,
    confirm_backup, installed_profile, parse_snapshots,
)
from .secrets import SecretUnavailable
from .state import StateBusy, StateUnsafe
from .storage_coordinator import StorageCoordinator, StorageLease, StorageUnavailable
from .supervisor import SupervisionUncertain, run_journaled

NANOSECONDS = 1_000_000_000


class JobStopped(Exception):
    """A cancellation or job-level deadline prevented further ordinary work."""


class DestinationRejected(Exception):
    """The configured destination could not be used in this invocation."""


@dataclass
class Selected:
    destination: str
    registration: Registration
    lease: StorageLease
    repository: Repository
    claim_id: str
    directory: dict


def destinations(job):
    targets = job["destinations"]
    yield targets["primary"].get("id", "primary"), targets["primary"]
    yield from ((target["id"], target) for target in targets.get("secondary", ()))


def seconds_deadline(seconds):
    return monotonic_ns() + seconds * NANOSECONDS


class JobRunner:
    def __init__(self, config, job, *, timeout=None, interrupted=lambda: False):
        self.config = config
        self.job = job
        self.job_id = job["job"]["id"]
        self.budgets = {**EXECUTION_DEFAULTS, **config.get("execution", {}), **job.get("timeouts", {})}
        cap = self.budgets["job_timeout_seconds"]
        self.deadline = seconds_deadline(min(cap, timeout) if timeout is not None else cap)
        self.interrupted = interrupted
        self.explicit_cancel = False
        self.known = []
        self.run_id = None
        self.backup_status = "not_run"
        self.snapshot = None
        self.selected = None
        self.fenced = False
        self.storage = StorageCoordinator(
            Path(config["state"]["path"]), Path(config["state"]["anchor_path"]), config["storage"],
            acquire_seconds=self.budgets["acquire_timeout_seconds"],
            release_seconds=self.budgets["release_timeout_seconds"],
            notifications=config.get("notifications", {}).get("storage", ()),
        )
        self.secrets = discover("secrets")

    def cancelled(self):
        if self.explicit_cancel or self.interrupted():
            self.explicit_cancel = True
            return True
        if self.run_id is not None:
            try:
                with self.storage.read() as database:
                    row = database.execute(
                        "SELECT cancel_requested FROM job_runs WHERE run_id = ?", (self.run_id,),
                    ).fetchone()
            except StateBusy:
                return False
            if row == (1,):
                self.explicit_cancel = True
        return self.explicit_cancel

    def check_running(self):
        if self.cancelled() or monotonic_ns() >= self.deadline:
            raise JobStopped

    def operation_deadline(self):
        return min(self.deadline, seconds_deadline(self.budgets["operation_timeout_seconds"]))

    def record(self, name, outcome, **kwargs):
        with self.storage.write() as database:
            phase(database, self.run_id, name, outcome, **kwargs)

    def move(self, name):
        with self.storage.write() as database:
            transition(database, self.run_id, name)

    def run_command(self, invocation, *, deadline, selection=None, observe_cancel=True, user=None, group=None,
                    cwd=None, operation_id=None, source_repository=None):
        if observe_cancel:
            self.check_running()
        if monotonic_ns() >= deadline:
            raise JobStopped
        claim = selection.claim_id if selection is not None else None
        result = run_journaled(
            self.storage.path, self.storage.anchor, operation_id or str(uuid4()), self.storage.owner,
            invocation.argv, deadline=deadline / NANOSECONDS, env=invocation.env, cwd=cwd,
            user=user, group=group, repository=selection.directory if selection else None,
            source_repository=source_repository,
            claim_operation_id=claim,
            authorize=(lambda database: repository_authorized(database, claim, self.storage.owner)) if claim else None,
            cancelled=self.cancelled if observe_cancel else None,
        )
        if result.stop_reason == "cancelled" and observe_cancel:
            self.explicit_cancel = True
        return result

    def load_restic(self):
        binary = Binary.identify(self.config.get("restic", {}).get("executable", "restic"))
        installed_profile(binary)
        return Restic.certified(binary, self.run_command(Restic.version(binary), deadline=self.operation_deadline()))

    def get_destination(self, adapter, name, target, *, access_mode="BACKUP", expected_id=None):
        with self.storage.read() as database:
            expected = registration(database, self.job_id, name)
        path = Path(target["repository"])
        if expected is None or (expected.storage_id, expected.path) != (target["storage"], path):
            raise DestinationRejected("REPOSITORY_UNREGISTERED")
        try:
            password = repository_password(self.secrets, target["password"], self.operation_deadline())
            self.known.append(password)
            repo = Repository(path, password)
        except (SecretUnavailable, ValueError):
            raise DestinationRejected("CREDENTIAL_UNAVAILABLE") from None
        try:
            lease = self.storage.acquire(
                target["storage"], deadline=min(self.deadline, seconds_deadline(self.budgets["acquire_timeout_seconds"])),
                cancelled=self.cancelled,
            )
        except (StorageUnavailable, TimeoutError, StateUnsafe):
            raise DestinationRejected("STORAGE_UNAVAILABLE") from None
        claimed = False
        claim = str(uuid4())
        try:
            try:
                directory = inspect_repository(self.storage, lease, path, self.operation_deadline())
            except (StateUnsafe, TimeoutError):
                raise DestinationRejected("REPOSITORY_PATH_UNSAFE") from None
            selected = Selected(name, expected, lease, repo, claim, directory)
            try:
                self.storage.reserve_repositories(
                    claim, [RepositoryClaim(expected.key, expected.generation, access_mode)], [lease],
                    deadline=self.operation_deadline(), cancelled=self.cancelled,
                    compatible_pairs=adapter.profile.native_overlap,
                )
                claimed = True
            except (StorageUnavailable, TimeoutError, StateBusy):
                raise DestinationRejected("REPOSITORY_BUSY") from None
            self.verify_identity(adapter, selected, expected_id=expected_id)
            return selected
        except SupervisionUncertain:
            self.fenced = True
            raise
        except Exception:
            if claimed:
                self.storage.release_repositories(claim)
            self.storage.release(lease)
            raise

    def verify_identity(self, adapter, selected, *, expected_id=None):
        self.check_running()
        try:
            actual = inspect_repository(self.storage, selected.lease, selected.repository.path, self.operation_deadline())
            if actual != selected.directory:
                raise StateUnsafe("Repository directory changed during operation.")
        except (StateUnsafe, TimeoutError):
            raise DestinationRejected("REPOSITORY_DISCONNECTED") from None
        result = self.run_command(adapter.identity(selected.repository), selection=selected, deadline=self.operation_deadline())
        if result.stop_reason == "cancelled":
            raise JobStopped
        try:
            identity = adapter.repository_identity(result)
        except (ResticEvidenceError, ResticUnsupported):
            diagnostics = redact_output(result, self.known)
            self.record("repository_identity", "failed", target=selected.destination,
                        code="REPOSITORY_IDENTITY_UNAVAILABLE", data=diagnostics)
            raise DestinationRejected("REPOSITORY_IDENTITY_UNAVAILABLE") from None
        if identity.id != (expected_id or selected.registration.restic_id):
            raise DestinationRejected("REPOSITORY_ID_MISMATCH")
        return identity

    def select(self, adapter):
        self.move("SELECTING_SOURCE")
        for name, target in destinations(self.job):
            self.check_running()
            try:
                selected = self.get_destination(adapter, name, target)
            except DestinationRejected as error:
                self.record("destination_selection", "unavailable", target=name, code=str(error))
                continue
            self.record("destination_selection", "selected", target=name)
            return selected
        raise DestinationRejected("NO_USABLE_DESTINATION")

    def hooks(self, name, *, cleanup=False):
        from .restic import Invocation

        success = True
        for index, hook in enumerate(self.job.get("hooks", {}).get(name, ()), 1):
            if not cleanup:
                self.check_running()
            ceiling = self.budgets["cleanup_timeout_seconds"] if cleanup else self.budgets["operation_timeout_seconds"]
            duration = min(ceiling, hook.get("timeout_seconds", ceiling))
            deadline = seconds_deadline(duration)
            if not cleanup:
                deadline = min(deadline, self.deadline)
            values = {
                "RUN_ID": self.run_id, "JOB_ID": self.job_id, "PHASE": name,
                "DESTINATION_ID": self.selected.destination, "BACKUP_OUTCOME": self.backup_status,
            }
            data = {"index": index}
            code = None
            try:
                env, secrets = hook_environment(self.secrets, hook, values, deadline)
                self.known.extend(secrets)
                result = self.run_command(
                    Invocation((hook["executable"], *hook.get("args", ())), env),
                    deadline=deadline, observe_cancel=not cleanup, user=hook.get("user"),
                    group=hook.get("group"), cwd=hook.get("cwd"),
                )
                data.update(redact_output(result, self.known))
                if result.exit_code != 0:
                    code = {
                        "cancelled": "HOOK_CANCELLED", "timeout": "HOOK_TIMEOUT",
                        "not_started": "HOOK_LAUNCH_FAILED",
                    }.get(result.stop_reason, "HOOK_FAILED")
            except SecretUnavailable:
                code = "HOOK_SECRET_UNAVAILABLE"
            except SupervisionUncertain:
                self.fenced = True
                self.record(name, "uncertain", code="HOOK_TERMINATION_UNCERTAIN", data=data)
                raise
            self.record(name, "failed" if code else "succeeded", code=code, data=data)
            success &= code is None
        return success

    def sources(self):
        selected = []
        required_missing = False
        for index, source in enumerate(self.job["sources"], 1):
            self.check_running()
            path = Path(source["path"])
            try:
                path.stat()
                selected.append(path)
            except (OSError, ValueError):
                required = source.get("required", True)
                self.record(
                    "sources", "failed" if required else "skipped",
                    code="REQUIRED_SOURCE_MISSING" if required else "OPTIONAL_SOURCE_MISSING", data={"index": index},
                )
                required_missing |= required
        if required_missing or not selected:
            raise DestinationRejected("REQUIRED_SOURCE_MISSING" if required_missing else "NO_SOURCES_AVAILABLE")
        self.record("sources", "succeeded", data={"count": len(selected)})
        return selected

    def backup(self, adapter, sources):
        # Never substitute another repository after the preparation phase.
        selected = self.selected
        self.move("BACKING_UP")
        self.verify_identity(adapter, selected)
        before = parse_snapshots(self.run_command(
            adapter.snapshots(selected.repository), selection=selected, deadline=self.operation_deadline(),
        ))
        result = self.run_command(
            adapter.backup(selected.repository, sources, excludes=self.job.get("excludes", ()),
                           tags=self.job.get("tags", ())),
            selection=selected, deadline=self.operation_deadline(),
        )
        data = redact_output(result, self.known)
        if result.stop_reason == "cancelled":
            self.record("backup", "cancelled", code="BACKUP_CANCELLED", data=data)
            raise JobStopped
        if result.exit_code != 0:
            self.record("backup", "failed", code="BACKUP_RESTIC_FAILED", data=data)
            raise ResticEvidenceError("Backup command did not exit successfully.")
        self.verify_identity(adapter, selected)
        after = parse_snapshots(self.run_command(
            adapter.snapshots(selected.repository), selection=selected, deadline=self.operation_deadline(),
        ))
        self.snapshot = confirm_backup(result, before, after, expected_paths=sources)
        self.backup_status = "succeeded"
        self.record("backup", "succeeded", target=selected.destination, data={"snapshot_id": self.snapshot, **data})

    def acquire_slot(self, wait):
        while True:
            self.check_running()
            with self.storage.write() as database:
                run, busy = admit(database, self.storage.owner, self.job_id, self.job, self.budgets["max_jobs"])
            if run is not None:
                self.run_id = run
                return None
            if not wait or busy == "JOB_BUSY":
                return busy
            sleep(0.05)

    def execute(self, *, wait=False, admission_done=lambda: None):
        errors = []
        status = Status.FAILED
        prepared = False
        backup_claim_released = False
        try:
            try:
                busy = self.acquire_slot(wait)
            finally:
                admission_done()
            if busy:
                return Result(Status.BUSY, errors=(Diagnostic(busy, "Job admission is busy."),))
            adapter = self.load_restic()
            self.selected = self.select(adapter)
            self.move("PREPARING")
            prepared = True
            if not self.hooks("pre"):
                self.check_running()
                raise DestinationRejected("PRE_HOOK_FAILED")
            sources = self.sources()
            self.backup_status = "failed"
            self.backup(adapter, sources)
            self.move("POST_AND_CLEANUP")
            self.hooks("post")
            self.check_running()
            status = Status.FULL
        except JobStopped:
            status = Status.CANCELLED if self.cancelled() else Status.FAILED
            errors.append(Diagnostic("JOB_CANCELLED" if status is Status.CANCELLED else "JOB_TIMEOUT",
                                     "Job was cancelled." if status is Status.CANCELLED else "Job deadline expired."))
        except ResticUnsupported:
            status = Status.UNSUPPORTED
            errors.append(Diagnostic("RESTIC_UNSUPPORTED", "No exact certification profile matches Restic."))
        except DestinationRejected as error:
            errors.append(Diagnostic(str(error), "The required backup phase could not complete."))
        except ResticEvidenceError:
            if self.cancelled():
                status = Status.CANCELLED
            errors.append(Diagnostic("BACKUP_EVIDENCE", "Restic did not provide successful backup evidence."))
        except SupervisionUncertain:
            self.fenced = True
            status = Status.UNSAFE
            errors.append(Diagnostic("TERMINATION_UNCERTAIN", "Execution remains fenced pending recovery."))
        except (StateUnsafe, OSError, TimeoutError):
            status = Status.UNSAFE
            errors.append(Diagnostic("JOB_STATE_UNSAFE", "Job state or storage could not be verified."))
        except Exception:
            status = Status.ERROR
            errors.append(Diagnostic("JOB_INTERNAL_ERROR", "The job could not complete safely."))
        finally:
            if self.run_id is not None:
                with self.storage.read() as database:
                    unresolved = database.execute(
                        """SELECT 1 FROM command_executions
                           WHERE owner_id = ? AND state != 'TERMINATED' LIMIT 1""",
                        (self.storage.owner,),
                    ).fetchone()
                self.fenced |= unresolved is not None
                if prepared and not self.fenced:
                    try:
                        self.hooks("cleanup", cleanup=True)
                    except (StateUnsafe, SupervisionUncertain, OSError, JobStopped):
                        self.fenced = True
                        self.record("cleanup", "uncertain", code="CLEANUP_UNCERTAIN")
                elif prepared:
                    self.record("cleanup", "deferred", code="CLEANUP_EXECUTION_FENCED")
                if status is Status.FULL and len(tuple(destinations(self.job))) > 1:
                    from .replication import run_replication
                    from .maintenance import integrated_retention

                    status = Status.PARTIAL
                    if not self.fenced:
                        try:
                            self.storage.release_repositories(self.selected.claim_id)
                            backup_claim_released = True
                            if run_replication(self, adapter, after=integrated_retention):
                                status = Status.FULL
                        except JobStopped:
                            if self.cancelled():
                                status = Status.CANCELLED
                            self.record("transfer", "incomplete", code="TRANSFER_INTERRUPTED")
                        except (StateUnsafe, SupervisionUncertain, OSError):
                            self.fenced = True
                            self.record("transfer", "uncertain", code="TRANSFER_STATE_UNSAFE")
                        except Exception:
                            self.fenced = True
                            self.record("transfer", "uncertain", code="TRANSFER_INTERNAL_ERROR")
                    if status is Status.PARTIAL:
                        errors.append(Diagnostic("TRANSFER_INCOMPLETE", "Required replication or convergence is incomplete."))
                if status is Status.FULL and len(tuple(destinations(self.job))) == 1 and not self.fenced:
                    from .maintenance import integrated_retention

                    try:
                        self.storage.release_repositories(self.selected.claim_id)
                        backup_claim_released = True
                        integrated_retention(self, adapter, {self.selected.destination: self.selected})
                    except JobStopped:
                        if self.cancelled():
                            status = Status.CANCELLED
                        self.record("retention", "incomplete", code="RETENTION_INTERRUPTED")
                    except (StateUnsafe, SupervisionUncertain, OSError):
                        self.fenced = True
                        self.record("retention", "uncertain", code="RETENTION_STATE_UNSAFE")
                if self.selected is not None and not self.fenced:
                    try:
                        if not backup_claim_released:
                            self.storage.release_repositories(self.selected.claim_id)
                        self.storage.release(self.selected.lease)
                    except (StateUnsafe, OSError):
                        self.fenced = True
                        self.record("storage_release", "uncertain", code="STORAGE_RELEASE_UNCERTAIN")
                with self.storage.read() as database:
                    active = database.execute(
                        """SELECT 1 FROM repository_claim_sets WHERE owner_id = ? AND state != 'RELEASED'
                           UNION ALL SELECT 1 FROM storage_claims WHERE owner_id = ? AND state != 'RELEASED'
                           UNION ALL SELECT 1 FROM command_executions WHERE owner_id = ? AND state != 'TERMINATED'
                           LIMIT 1""",
                        (self.storage.owner, self.storage.owner, self.storage.owner),
                    ).fetchone()
                if active is not None:
                    self.fenced = True
                if self.fenced and status not in {Status.FULL, Status.PARTIAL}:
                    status = Status.UNSAFE
                elif not self.fenced and self.cancelled():
                    status = Status.CANCELLED
                with self.storage.write() as database:
                    finish(database, self.run_id, status.value[0], self.backup_status,
                           self.selected.destination if self.selected else None, self.snapshot, fenced=self.fenced)
                    prune_history(database, self.job_id, self.config.get("logging", {}), self.run_id)
                if not self.fenced:
                    from .notifications import notify

                    try:
                        notify(
                            self.storage.path, self.storage.anchor, self.storage.owner,
                            self.config.get("notifications", {}).get("job", ()), "job", self.run_id,
                            {"run_id": self.run_id, "job_id": self.job_id, "status": status.value[0],
                             "backup": self.backup_status, "snapshot_id": self.snapshot},
                            self.budgets["operation_timeout_seconds"],
                        )
                    except (StateUnsafe, OSError):
                        pass
            if not self.fenced:
                with self.storage.write() as database:
                    database.execute(
                        "UPDATE process_owners SET state = 'TERMINATED' WHERE owner_id = ? AND state = 'ACTIVE'",
                        (self.storage.owner,),
                    )
        outcomes = []
        if self.run_id is not None:
            with self.storage.read() as database:
                outcomes = [
                    {"phase": phase_name, "target_id": target, "status": outcome, "error_code": code}
                    for phase_name, target, outcome, code in database.execute(
                        """SELECT phase, target_id, status, error_code
                           FROM phase_results WHERE run_id = ? ORDER BY rowid""",
                        (self.run_id,),
                    )
                ]
        return Result(status, {
            "job_id": self.job_id, "backup": self.backup_status, "phases": outcomes,
            "destination_id": self.selected.destination if self.selected else None,
            "snapshot_id": self.snapshot,
        }, tuple(errors), self.run_id)


def register_destination(config, job, destination_id, *, timeout=None, interrupted=lambda: False, initialize=False):
    runner = JobRunner(config, job, timeout=timeout, interrupted=interrupted)
    target = dict(destinations(job))[destination_id]
    lease = None
    reservation = None
    uncertain = False
    try:
        adapter = runner.load_restic()
        with runner.storage.read() as database:
            legacy = database.execute(
                """SELECT repository_key, restic_repository_id, storage_id, repository_path
                   FROM repository_registrations WHERE job_id = ? AND destination_id = ?""",
                (runner.job_id, destination_id),
            ).fetchone()
        if legacy is not None and (initialize or legacy[2:] != (None, None)):
            raise DestinationRejected("REPOSITORY_ALREADY_REGISTERED")
        password = repository_password(runner.secrets, target["password"], runner.operation_deadline())
        repo = Repository(Path(target["repository"]), password)
        lease = runner.storage.acquire(
            target["storage"],
            deadline=min(runner.deadline, seconds_deadline(runner.budgets["acquire_timeout_seconds"])),
            cancelled=runner.cancelled,
        )
        if initialize:
            from .repository_admin import initialize_repository

            reservation = (lease.coordination_id, str(repo.path))
            initialize_repository(runner, adapter, lease, repo)
        directory = inspect_repository(runner.storage, lease, repo.path, runner.operation_deadline())
        invocation = adapter.identity(repo)
        result = run_journaled(
            runner.storage.path, runner.storage.anchor, str(uuid4()), runner.storage.owner,
            invocation.argv, env=invocation.env, deadline=runner.operation_deadline() / NANOSECONDS,
            repository=directory, cancelled=runner.cancelled,
        )
        identity = adapter.repository_identity(result)
        fresh = inspect_repository(runner.storage, lease, repo.path, runner.operation_deadline())
        if directory != fresh:
            raise StateUnsafe("Repository directory changed during registration.")
        key = legacy[0] if legacy is not None else str(uuid4())
        with runner.storage.write() as database:
            initialization = database.execute(
                """SELECT 1 FROM repository_initializations
                   WHERE coordination_id = ? AND repository_path = ? AND owner_id != ?""",
                (lease.coordination_id, str(repo.path), runner.storage.owner),
            ).fetchone()
            if initialization is not None:
                raise StateUnsafe("Repository initialization is still fenced.")
            duplicate = database.execute(
                """SELECT 1 FROM repository_registrations WHERE repository_key != ? AND (
                       (job_id = ? AND destination_id = ?) OR restic_repository_id = ?
                       OR (storage_id = ? AND repository_path = ?))""",
                (key, runner.job_id, destination_id, identity.id, lease.resource_id, str(repo.path)),
            ).fetchone()
            if duplicate is not None:
                raise DestinationRejected("REPOSITORY_ALREADY_REGISTERED")
            if legacy is not None:
                # Old rows can receive the new binding only under explicit
                # registration and after matching their originally approved ID.
                updated = database.execute(
                    """UPDATE repository_registrations SET storage_id = ?, repository_path = ?
                       WHERE repository_key = ? AND restic_repository_id = ?
                         AND storage_id IS NULL AND repository_path IS NULL""",
                    (lease.resource_id, str(repo.path), key, identity.id),
                )
                if updated.rowcount != 1:
                    raise DestinationRejected("REPOSITORY_ID_MISMATCH")
            else:
                database.execute(
                    """INSERT INTO repository_registrations
                       (repository_key, job_id, destination_id, restic_repository_id, repo_generation, approved_at, storage_id, repository_path)
                       VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
                    (key, runner.job_id, destination_id, identity.id, now(), lease.resource_id, str(repo.path)),
                )
            event(database, key, "repository_registered")
        return Result(data={"job_id": runner.job_id, "destination_id": destination_id, "repository_id": identity.id})
    except ResticUnsupported:
        return Result(Status.UNSUPPORTED, errors=(Diagnostic("RESTIC_UNSUPPORTED", "No exact certification profile matches Restic."),))
    except JobStopped:
        return Result(Status.CANCELLED if runner.cancelled() else Status.FAILED)
    except SupervisionUncertain:
        uncertain = True
        return Result(Status.UNSAFE, errors=(Diagnostic("TERMINATION_UNCERTAIN", "Repository registration remains fenced."),))
    except (DestinationRejected, SecretUnavailable, ResticEvidenceError, StateUnsafe, StorageUnavailable, TimeoutError, ValueError):
        return Result(Status.FAILED, errors=(Diagnostic("REGISTRATION_FAILED", "Repository identity could not be registered safely."),))
    finally:
        if not uncertain:
            if lease is not None:
                runner.storage.release(lease)
            with runner.storage.write() as database:
                if reservation is not None:
                    database.execute(
                        "DELETE FROM repository_initializations WHERE coordination_id = ? AND repository_path = ? AND owner_id = ?",
                        (*reservation, runner.storage.owner),
                    )
                database.execute(
                    "UPDATE process_owners SET state = 'TERMINATED' WHERE owner_id = ? AND state = 'ACTIVE'",
                    (runner.storage.owner,),
                )
