"""Fixed-source normal replication and additive convergence."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from uuid import uuid4

from .claims import RepositoryClaim
from .convergence import (
    Requirement, logical_identity, parse_native_inventory, plan_union, representation, representation_index,
    transfer, verify_transfer,
)
from .job_hooks import redact_output
from .jobs import DestinationRejected, JobStopped, destinations
from .restic import ResticEvidenceError
from .state import StateBusy, StateUnsafe
from .storage_coordinator import StorageUnavailable
from .supervisor import SupervisionUncertain
from .transfer_state import (
    blocked_requirements, completed_requirements, finish_attempt, plan_attempt, record_inventory, requirements,
)

EXPECTED_FAILURE = (DestinationRejected, ResticEvidenceError, StorageUnavailable, TimeoutError, StateBusy)
UNAVAILABLE = {"REPOSITORY_DISCONNECTED", "REPOSITORY_ID_MISMATCH"}


class Replication:
    def __init__(self, runner, adapter):
        self.runner = runner
        self.adapter = adapter
        self.profile = adapter.profile.binary_sha256
        self.source = runner.selected.destination
        self.repos = {self.source: runner.selected}
        self.unavailable = set()

    def discover(self):
        for name, target in destinations(self.runner.job):
            self.runner.check_running()
            if name == self.source:
                continue
            try:
                repo = self.runner.get_destination(self.adapter, name, target, access_mode="READ")
            except DestinationRejected as error:
                self.unavailable.add(name)
                self.runner.record("replication", "unavailable", target=name, code=str(error))
                continue
            self.repos[name] = repo
            self.runner.storage.release_repositories(repo.claim_id)

    @contextmanager
    def claims(self, *requests):
        operation = str(uuid4())
        repos = tuple(self.repos[name] for name, _ in requests)
        self.runner.storage.reserve_repositories(
            operation,
            [RepositoryClaim(repo.registration.key, repo.registration.generation, mode)
             for repo, (_, mode) in zip(repos, requests)],
            [repo.lease for repo in repos],
            deadline=self.runner.operation_deadline(), cancelled=self.runner.cancelled,
            compatible_pairs=self.adapter.profile.native_overlap,
        )
        try:
            yield tuple(replace(repo, claim_id=operation) for repo in repos)
        except SupervisionUncertain:
            self.runner.fenced = True
            raise
        finally:
            if not self.runner.fenced:
                self.runner.storage.release_repositories(operation)

    def inventory(self, repo):
        try:
            identity = self.runner.verify_identity(self.adapter, repo)
            result = self.runner.run_command(
                self.adapter.snapshots(repo.repository), selection=repo,
                deadline=self.runner.operation_deadline(),
            )
            if result.stop_reason == "cancelled":
                raise JobStopped
            rows = parse_native_inventory(result)
        except EXPECTED_FAILURE:
            with self.runner.storage.write() as database:
                record_inventory(database, repo.registration, self.profile, None)
            raise
        with self.runner.storage.write() as database:
            record_inventory(database, repo.registration, self.profile, rows)
        return identity, rows

    def enumerate(self, name):
        with self.claims((name, "READ")) as (repo,):
            return self.inventory(repo)[1]

    def finish_copy(self, attempt, phase, plan, state, code=None, *, evidence=(), details=None):
        with self.runner.storage.write() as database:
            finish_attempt(database, attempt, state, code=code, profile_id=self.profile, evidence=evidence)
        self.runner.record(
            phase, "succeeded" if state == "SUCCEEDED" else "failed", target=plan.destination, code=code,
            data={"attempt_id": attempt, "source": plan.source, "snapshot_count": len(plan.snapshots), **(details or {})},
        )

    def copy(self, phase, plan):
        source = self.repos[plan.source].registration
        target = self.repos[plan.destination].registration
        with self.runner.storage.write() as database:
            attempt = plan_attempt(database, self.runner.run_id, phase, plan, source, target)
        refreshed = {}
        launched = False
        observing = None
        try:
            with self.claims((plan.source, "COPY_SOURCE"), (plan.destination, "COPY_DESTINATION")) as (origin, destination):
                observing = plan.source
                source_identity, before = self.inventory(origin)
                refreshed[plan.source] = before
                current = {snapshot.id: snapshot for snapshot in before}
                if any(current.get(snapshot.id) != snapshot for snapshot in plan.snapshots):
                    self.finish_copy(attempt, phase, plan, "PRELAUNCH_FAILED", "SOURCE_SNAPSHOT_MISSING")
                    return "PRELAUNCH_FAILED", refreshed
                observing = plan.destination
                target_identity, target_before = self.inventory(destination)
                refreshed[plan.destination] = target_before
                if source_identity.chunker_polynomial != target_identity.chunker_polynomial:
                    self.runner.record(phase, "warning", target=plan.destination, code="CHUNKER_MISMATCH",
                                       data={"source": plan.source, "attempt_id": attempt})
                result = self.runner.run_command(
                    self.adapter.copy(origin.repository, destination.repository, [item.id for item in plan.snapshots]),
                    selection=destination, source_repository=origin.directory, operation_id=attempt,
                    deadline=self.runner.operation_deadline(),
                )
                launched = result.stop_reason != "not_started"
                diagnostics = redact_output(result, self.runner.known)
                if not launched:
                    self.finish_copy(attempt, phase, plan, "PRELAUNCH_FAILED", "COPY_NOT_STARTED", details=diagnostics)
                    return "PRELAUNCH_FAILED", refreshed
                if result.exit_code != 0 or result.truncated:
                    self.finish_copy(attempt, phase, plan, "FAILED", "COPY_EXECUTION_FAILED", details=diagnostics)
                    if result.stop_reason == "cancelled":
                        raise JobStopped
                    return "FAILED", refreshed

                observing = plan.source
                _, source_after = self.inventory(origin)
                refreshed[plan.source] = source_after
                observing = plan.destination
                _, target_after = self.inventory(destination)
                refreshed[plan.destination] = target_after
                evidence = verify_transfer(plan, source_after, target_after)
                if len(evidence) != len(plan.snapshots):
                    self.finish_copy(attempt, phase, plan, "UNVERIFIED", "COPY_REPRESENTATION_UNVERIFIED",
                                     details={"verified_count": len(evidence)})
                    return "UNVERIFIED", refreshed
                self.finish_copy(attempt, phase, plan, "SUCCEEDED", evidence=evidence)
                return "SUCCEEDED", refreshed
        except EXPECTED_FAILURE as error:
            if observing is not None and isinstance(error, DestinationRejected) and str(error) in UNAVAILABLE:
                refreshed[observing] = None
            state = "UNVERIFIED" if launched else "PRELAUNCH_FAILED"
            self.finish_copy(attempt, phase, plan, state, "COPY_INVENTORY_UNAVAILABLE")
            return state, refreshed
        except JobStopped:
            with self.runner.storage.read() as database:
                state = database.execute("SELECT state FROM copy_attempts WHERE attempt_id = ?", (attempt,)).fetchone()[0]
            if state in {"PLANNED", "LAUNCH_INTENT", "RUNNING"}:
                self.finish_copy(attempt, phase, plan, "UNVERIFIED" if launched else "PRELAUNCH_FAILED", "COPY_CANCELLED")
            raise

    def normal(self):
        if len(self.repos) == 1:
            return not self.unavailable
        try:
            baseline = self.enumerate(self.source)
        except EXPECTED_FAILURE:
            self.runner.record("replication", "incomplete", target=self.source, code="SOURCE_ENUMERATION_FAILED")
            return False
        if not any(item.id == self.runner.snapshot for item in baseline):
            self.runner.record("replication", "incomplete", target=self.source, code="SOURCE_SNAPSHOT_MISSING")
            return False
        plans = [
            transfer(self.source, name, repo.registration, baseline)
            for name, repo in self.repos.items() if name != self.source
        ]
        with ThreadPoolExecutor(max_workers=min(4, len(plans))) as pool:
            futures = [pool.submit(self.copy, "replication", plan) for plan in plans]
            outcomes = [future.result()[0] for future in futures]
        return not self.unavailable and all(outcome == "SUCCEEDED" for outcome in outcomes)

    def refresh(self, name, inventories, *, failure_code):
        try:
            inventories[name] = self.enumerate(name)
        except EXPECTED_FAILURE as error:
            if isinstance(error, DestinationRejected) and str(error) in UNAVAILABLE:
                self.unavailable.add(name)
                inventories.pop(name, None)
                self.runner.record("convergence", "unavailable", target=name, code=str(error))
                return True
            self.runner.record("convergence", "incomplete", target=name, code=failure_code)
            return False
        return True

    def convergence(self):
        for name in sorted(self.unavailable):
            self.runner.record("convergence", "unavailable", target=name, code="DESTINATION_UNAVAILABLE")
        inventories = {}
        if len(self.repos) == 1:
            return not self.unavailable
        for name in self.repos:
            if not self.refresh(name, inventories, failure_code="INVENTORY_ENUMERATION_FAILED"):
                return False
        registration = {name: repo.registration for name, repo in self.repos.items()}
        union, rejected = {}, set()
        while True:
            self.runner.check_running()
            with self.runner.storage.read() as database:
                blocked = blocked_requirements(database, self.runner.run_id)
                completed = completed_requirements(database, self.runner.run_id)
            plan, pending = plan_union(inventories, registration, union, blocked, rejected, completed)
            with self.runner.storage.write() as database:
                requirements(database, self.runner.run_id, pending)
                for name, items in inventories.items():
                    repo = registration[name]
                    present = representation_index(items)
                    database.executemany(
                        """UPDATE transfer_requirements SET state = ? WHERE run_id = ?
                           AND destination_key = ? AND destination_generation = ?
                           AND logical_identity = ? AND state != 'BLOCKED'""",
                        [("SATISFIED" if representation(snapshot, present) is not None else "PENDING", self.runner.run_id,
                          repo.key, repo.generation, logical) for logical, snapshot in union.items()
                         if Requirement(logical, repo.key, repo.generation).id not in completed],
                    )
            if plan is None:
                if any(item.id in completed for item in pending):
                    self.runner.record("convergence", "incomplete", code="VERIFIED_REPRESENTATION_LOST")
                return not (self.unavailable or pending or blocked)
            outcome, refreshed = self.copy("convergence", plan)
            for name, items in refreshed.items():
                if items is None:
                    inventories.pop(name, None)
                    if name not in self.unavailable:
                        self.unavailable.add(name)
                        self.runner.record("convergence", "unavailable", target=name, code="REPOSITORY_UNAVAILABLE")
                else:
                    inventories[name] = items
            if outcome == "PRELAUNCH_FAILED":
                by_logical = {logical_identity(snapshot): snapshot.id for snapshot in plan.snapshots}
                rejected.update(
                    (plan.source, requirement.id, by_logical[requirement.logical])
                    for requirement in plan.requirements
                )
            if outcome in {"FAILED", "UNVERIFIED"}:
                # A new complete inventory is needed before using these
                # repositories in another plan. Failed reads never shrink union.
                for name in (plan.source, plan.destination):
                    if name not in self.unavailable and not self.refresh(
                        name, inventories, failure_code="INVENTORY_REFRESH_FAILED",
                    ):
                        return False


def run_replication(runner, adapter, *, after=None):
    """Run both required phases, holding storage claims until all copies settle."""
    replication = Replication(runner, adapter)
    try:
        runner.move("NORMAL_REPLICATION")
        replication.discover()
        normal = replication.normal()
        runner.record("replication", "succeeded" if normal else "incomplete")
        runner.move("CONVERGENCE")
        converged = replication.convergence()
        runner.record("convergence", "succeeded" if converged else "incomplete")
        if after is not None:
            try:
                after(runner, adapter, {
                    name: repo for name, repo in replication.repos.items() if name not in replication.unavailable
                })
            except JobStopped:
                runner.record("retention", "incomplete", code="RETENTION_INTERRUPTED")
                if runner.cancelled():
                    raise
            except (StateUnsafe, SupervisionUncertain, OSError):
                runner.fenced = True
                runner.record("retention", "uncertain", code="RETENTION_STATE_UNSAFE")
        return normal and converged
    except SupervisionUncertain:
        runner.fenced = True
        raise
    finally:
        if not runner.fenced:
            for name, repo in replication.repos.items():
                if name == replication.source:
                    continue
                try:
                    runner.storage.release(repo.lease)
                except (StateUnsafe, OSError):
                    runner.record("storage_release", "uncertain", target=name, code="STORAGE_RELEASE_UNCERTAIN")
