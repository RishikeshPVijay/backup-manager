"""Exclusive repository maintenance and verified read-only inspection."""

from dataclasses import replace
from uuid import uuid4

from .claims import RepositoryClaim
from .errors import Diagnostic, Result, Status
from .job_hooks import redact_output
from .jobs import DestinationRejected, JobRunner, JobStopped, destinations
from .launch_journal import event
from .restic import ResticUnsupported
from .state import StateUnsafe
from .storage_coordinator import StorageUnavailable
from .supervisor import SupervisionUncertain


def command_result(runner, adapter, selected, action):
    operations = (
        (("forget", adapter.forget(selected.repository, runner.job["retention"])),)
        if action == "retention" else ((action, {
            "verify": adapter.check,
            "unlock": adapter.explicit_unlock,
        }[action](selected.repository)),)
    )
    if action == "retention" and runner.job["retention"].get("prune", False):
        operations += (("prune", adapter.prune(selected.repository)),)
    rows = []
    for name, invocation in operations:
        runner.verify_identity(adapter, selected)
        result = runner.run_command(invocation, deadline=runner.operation_deadline(), selection=selected)
        rows.append({
            "operation": name, "status": "succeeded" if result.exit_code == 0 else "failed",
            **redact_output(result, runner.known),
        })
        with runner.storage.write() as database:
            event(database, selected.registration.key, f"{name}_{rows[-1]['status']}")
        if result.stop_reason == "cancelled":
            raise JobStopped
        if result.exit_code != 0:
            break
    return rows


def integrated_retention(runner, adapter, repos):
    runner.move("RETENTION")
    for name, _ in destinations(runner.job):
        runner.check_running()
        repo = repos.get(name)
        if repo is None:
            runner.record("retention", "unavailable", target=name, code="DESTINATION_UNAVAILABLE")
            continue
        claim = str(uuid4())
        reserved = False
        try:
            runner.storage.reserve_repositories(
                claim, [RepositoryClaim(repo.registration.key, repo.registration.generation, "RETENTION")],
                [repo.lease], deadline=runner.operation_deadline(), cancelled=runner.cancelled,
            )
            reserved = True
            rows = command_result(runner, adapter, replace(repo, claim_id=claim), "retention")
            for row in rows:
                runner.record("retention", row["status"], target=name,
                              code="RETENTION_FAILED" if row["status"] == "failed" else None, data=row)
        except (DestinationRejected, StorageUnavailable, TimeoutError):
            runner.record("retention", "failed", target=name, code="RETENTION_UNAVAILABLE")
        except SupervisionUncertain:
            runner.fenced = True
            raise
        finally:
            if reserved and not runner.fenced:
                runner.storage.release_repositories(claim)


def run_maintenance(config, job, action, target, *, timeout=None, interrupted=lambda: False):
    runner = JobRunner(config, job, timeout=timeout, interrupted=interrupted)
    rows = []
    status = Status.FULL
    try:
        adapter = runner.load_restic()
        for name, settings in destinations(job):
            if target is not None and target != name:
                continue
            selected = None
            try:
                selected = runner.get_destination(adapter, name, settings, access_mode=action.upper())
                results = (
                    [{"operation": "preflight", "status": "succeeded", "repository_id": selected.registration.restic_id}]
                    if action == "preflight" else command_result(runner, adapter, selected, action)
                )
                rows.append({"destination_id": name, "operations": results})
                if any(item["status"] == "failed" for item in results):
                    status = Status.FAILED
            except (DestinationRejected, StorageUnavailable, TimeoutError):
                rows.append({"destination_id": name, "status": "unavailable"})
                status = Status.FAILED
            except SupervisionUncertain:
                runner.fenced = True
                raise
            finally:
                if selected is not None and not runner.fenced:
                    runner.storage.release_repositories(selected.claim_id)
                    runner.storage.release(selected.lease)
    except ResticUnsupported:
        status = Status.UNSUPPORTED
    except JobStopped:
        status = Status.CANCELLED if runner.cancelled() else Status.FAILED
    except (SupervisionUncertain, StateUnsafe, OSError):
        runner.fenced = True
        status = Status.UNSAFE
    finally:
        if not runner.fenced:
            with runner.storage.write() as database:
                database.execute("UPDATE process_owners SET state = 'TERMINATED' WHERE owner_id = ?", (runner.storage.owner,))
    error = () if status is Status.FULL else (Diagnostic("MAINTENANCE_INCOMPLETE", "Repository maintenance did not fully complete."),)
    return Result(status, {"destinations": rows}, error)
