"""Job-facing storage coordination across independently running processes."""

import asyncio
from dataclasses import dataclass
import os
from time import monotonic_ns, sleep
from uuid import uuid4

from .claims import admit_repository_claims, relinquish_repository_claims
from .crash_recovery import reconcile_crashed_owners
from .launch_journal import event, register_owner
from .plugin_api import ExecutionContext, StorageState
from .process_identity import ProcessIdentity
from .providers import discover
from .state import StateUnsafe, state_read, state_write
from .storage_claims import drop_claim, lifecycle_view, live_claims, plan_acquisition, plan_release, reconcile_identity
from .storage_dispatch import start_dispatch
from .storage_driver import fence_operation


@dataclass(frozen=True)
class StorageLease:
    claim_id: str
    resource_id: str
    coordination_id: str


class StorageUnavailable(Exception):
    """No usable lease could be established before the invocation ended."""


def context(deadline):
    return ExecutionContext(str(uuid4()), deadline, str(uuid4()), str(uuid4()))


class StorageCoordinator:
    def __init__(self, path, anchor, resources, *, acquire_seconds, release_seconds):
        self.path = path
        self.anchor = anchor
        self.resources = resources
        self.acquire_seconds = acquire_seconds
        self.release_seconds = release_seconds
        self.providers = discover("storage")
        self.dispatches = []
        reconcile_crashed_owners(path, anchor)
        with self.write() as database:
            self.owner = register_owner(database, ProcessIdentity.capture(os.getpid()), str(uuid4()))

    def write(self):
        return state_write(self.path, self.anchor, wait=5)

    def read(self):
        return state_read(self.path, self.anchor, wait=5)

    def observe(self, resource_id, deadline):
        settings = self.resources[resource_id]
        provider = self.providers[settings["provider"]]
        config = settings["config"]

        async def inspect():
            remaining = (deadline - monotonic_ns()) / 1_000_000_000
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining):
                observation = await provider.inspect(config, context(deadline))
                identity = observation.identity
                if not identity.verified_physical and settings["lifecycle"] == "on_demand":
                    identity = await provider.resolve_identity(config, context(deadline))
                return observation, identity

        return asyncio.run(inspect())

    def dispatch(self, plan, resource_id, seconds, evidence=None):
        settings = self.resources[resource_id]
        deadline = monotonic_ns() + seconds * 1_000_000_000
        self.dispatches = [process for process in self.dispatches if process.poll() is None]
        try:
            process = start_dispatch(
                self.path, self.anchor, plan, self.owner, settings["provider"], settings["config"],
                context(deadline), self.release_seconds, evidence,
            )
        except (OSError, StateUnsafe):
            fence_operation(self.path, self.anchor, plan.operation_id)
            raise
        self.dispatches.append(process)
        return process

    def acquire(self, resource_id, *, deadline, cancelled=lambda: False):
        settings = self.resources[resource_id]
        plan = None
        process = None
        lease = None
        try:
            _, identity = self.observe(resource_id, deadline)
            with self.write() as database:
                if database.execute(
                    "SELECT 1 FROM repository_claim_sets WHERE owner_id = ? AND state != 'RELEASED' LIMIT 1",
                    (self.owner,),
                ).fetchone() is not None:
                    raise StateUnsafe("Acquire storage before taking repository claims.")
                resource = reconcile_identity(database, resource_id, identity, settings.get("coordination_id"))
            while monotonic_ns() < deadline and not cancelled():
                with self.read() as database:
                    observed = lifecycle_view(database, resource.coordination_id)
                physical, identity = self.observe(resource_id, deadline)
                with self.write() as database:
                    current = reconcile_identity(database, resource_id, identity, settings.get("coordination_id"))
                    if current != resource:
                        raise StateUnsafe("Storage generation changed during acquisition.")
                    if physical.state is not StorageState.UNCERTAIN:
                        plan = plan_acquisition(
                            database, resource, self.owner, observed,
                            available=physical.state is StorageState.AVAILABLE,
                            on_demand=settings["lifecycle"] == "on_demand",
                        )
                if plan is not None:
                    break
                if physical.state is StorageState.UNCERTAIN:
                    raise StorageUnavailable("Storage identity or physical state is uncertain.")
                sleep(0.05)
            if plan is None:
                raise StorageUnavailable("Storage admission was cancelled or timed out.")
            lease = StorageLease(plan.claim_id, resource_id, resource.coordination_id)
            if plan.leader:
                process = self.dispatch(plan, resource_id, self.acquire_seconds)
            while monotonic_ns() < deadline and not cancelled():
                with self.read() as database:
                    claim = database.execute(
                        "SELECT state FROM storage_claims WHERE claim_id = ?", (lease.claim_id,),
                    ).fetchone()
                    operation = database.execute(
                        "SELECT state FROM storage_operations WHERE operation_id = ?", (plan.operation_id,),
                    ).fetchone()
                if claim == ("ACTIVE",):
                    physical, identity = self.observe(resource_id, deadline)
                    with self.write() as database:
                        current = reconcile_identity(database, resource_id, identity, settings.get("coordination_id"))
                        if current != resource or physical.state is not StorageState.AVAILABLE:
                            raise StorageUnavailable("Acquired storage failed fresh validation.")
                    return lease
                if claim != ("WAITING",) or operation in {("RECOVERY_REQUIRED",), ("TERMINATION_UNCERTAIN",)}:
                    raise StorageUnavailable("Storage acquisition failed or needs recovery.")
                if process is not None and process.poll() not in {None, 0}:
                    fence_operation(self.path, self.anchor, plan.operation_id)
                    raise StorageUnavailable("Storage dispatcher failed.")
                sleep(0.05)
            raise StorageUnavailable("Storage admission was cancelled or timed out.")
        except Exception:
            if lease is not None:
                self.release(lease)
            raise

    def release(self, lease):
        with self.write() as database:
            claim = database.execute(
                "SELECT state FROM storage_claims WHERE claim_id = ? AND owner_id = ?",
                (lease.claim_id, self.owner),
            ).fetchone()
            if claim not in {("WAITING",), ("ACTIVE",)}:
                return
            drop_claim(database, lease.claim_id, self.owner)
            pending = plan_release(database, lease.coordination_id, self.owner) if claim == ("ACTIVE",) else None
            if pending:
                plan, evidence = pending
                resource_id = database.execute(
                    "SELECT resource_id FROM storage_operations WHERE operation_id = ?", (plan.operation_id,),
                ).fetchone()[0]
        if pending:
            self.dispatch(plan, resource_id, self.release_seconds, evidence)


    def recover_storage(self, resource_id, *, deadline):
        """Reconcile an idle resource without performing any physical release."""
        reconcile_crashed_owners(self.path, self.anchor)
        settings = self.resources[resource_id]
        inspection, identity = self.observe(resource_id, deadline)
        if inspection.state is StorageState.UNCERTAIN:
            raise StateUnsafe("Cannot verify current storage state.")
        with self.write() as database:
            resource = reconcile_identity(database, resource_id, identity, settings.get("coordination_id"))
            coordination = resource.coordination_id
            if live_claims(database, coordination):
                raise StateUnsafe("Storage recover requires all resource claims to be clear.")
            pending = database.execute(
                """SELECT operation.operation_id, operation.state, owner.state, command.state
                   FROM storage_operations AS operation JOIN storage_resources AS resource USING (resource_id)
                   LEFT JOIN process_owners AS owner ON owner.owner_id = operation.worker_identity
                   LEFT JOIN command_executions AS command ON command.operation_id = operation.operation_id
                   WHERE resource.coordination_id = ? AND operation.state != 'TERMINATED'""",
                (coordination,),
            ).fetchall()
            if any(
                state != "RECOVERY_REQUIRED"
                or owner_state not in {"INTERRUPTED", "TERMINATED"}
                or command_state not in {None, "TERMINATED"}
                for _, state, owner_state, command_state in pending
            ):
                raise StateUnsafe("Storage operation containment or ownership is unresolved.")
            for operation_id, _, _, _ in pending:
                database.execute(
                    "UPDATE storage_operations SET state = 'TERMINATED' WHERE operation_id = ?",
                    (operation_id,),
                )
                event(database, operation_id, "storage_reconciled")
            # Do not allow a recovered, orphaned mount to be released based
            # solely on a receipt from its dead lifecycle owner.
            if pending:
                database.execute(
                    """UPDATE storage_ownership SET owned_by_manager = 0
                       WHERE resource_id IN (SELECT resource_id FROM storage_resources WHERE coordination_id = ?)""",
                    (coordination,),
                )
        return {"resource_id": resource_id, "mounted": inspection.mounted, "reconciled": len(pending)}


    def reserve_repositories(self, operation_id, requests, leases, *, deadline,
                             compatible_pairs=frozenset(), cancelled=lambda: False):
        """Revalidate storage before atomically taking the ordered repository set."""
        if not leases:
            raise StateUnsafe("Repository work requires at least one verified storage claim.")
        while monotonic_ns() < deadline and not cancelled():
            observations = [
                (lease, *self.observe(lease.resource_id, deadline)) for lease in leases
            ]
            wait = max(0, min(5, (deadline - monotonic_ns()) / 1_000_000_000))
            with state_write(self.path, self.anchor, wait=wait) as database:
                for lease, physical, identity in observations:
                    if physical.state is not StorageState.AVAILABLE:
                        raise StorageUnavailable("Repository storage disconnected.")
                    settings = self.resources[lease.resource_id]
                    resource = reconcile_identity(
                        database, lease.resource_id, identity, settings.get("coordination_id"),
                    )
                    claim = database.execute(
                        """SELECT 1 FROM storage_claims WHERE claim_id = ? AND owner_id = ?
                           AND resource_id = ? AND generation = ? AND state = 'ACTIVE'""",
                        (lease.claim_id, self.owner, lease.resource_id, resource.generation),
                    ).fetchone()
                    if claim is None or resource.coordination_id != lease.coordination_id:
                        raise StateUnsafe("Repository storage claim has been fenced.")
                result = admit_repository_claims(
                    database, operation_id, self.owner, requests, compatible_pairs,
                )
            if result is not None:
                return result
            sleep(min(0.05, max(0, (deadline - monotonic_ns()) / 1_000_000_000)))
        raise StorageUnavailable("Repository claims were cancelled or timed out.")

    def release_repositories(self, operation_id):
        with self.write() as database:
            return relinquish_repository_claims(database, operation_id, self.owner)
