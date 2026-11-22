"""Verify cross-process copy exclusion and parallelism with active Restic processes."""

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from acceptance_trace import record
from backup_manager.claims import RepositoryClaim
from backup_manager.config import load_global, load_job
from backup_manager.jobs import JobRunner, destinations
from backup_manager.process_identity import Presence, ProcessIdentity
from backup_manager.state import state_read
import backup_manager.storage_coordinator as coordination
import backup_manager.supervisor as supervisor
from linux_i5_job import Fixture, await_condition


def child(root, name, target, snapshot):
    document = load_global(root / "config.toml")
    job = load_job(root / "jobs" / "fixture.toml", document)
    assert not document.errors and not job.errors
    runner = JobRunner(document.data, job.data)
    adapter = runner.load_restic()
    configured = dict(destinations(job.data))
    source = runner.get_destination(adapter, "primary", configured["primary"], access_mode="READ")
    runner.storage.release_repositories(source.claim_id)
    destination = runner.get_destination(adapter, target, configured[target], access_mode="READ")
    runner.storage.release_repositories(destination.claim_id)
    (root / f"{name}.ready").touch()
    await_condition((root / f"{name}.go").exists)
    operation = f"copy-{name}"
    requests = [
        RepositoryClaim(source.registration.key, source.registration.generation, "COPY_SOURCE"),
        RepositoryClaim(destination.registration.key, destination.registration.generation, "COPY_DESTINATION"),
    ]
    admit = coordination.admit_repository_claims

    def observe_conflict(*args, **kwargs):
        claim = admit(*args, **kwargs)
        if claim is None:
            (root / f"{name}.blocked").touch()
        return claim

    with patch.object(coordination, "admit_repository_claims", side_effect=observe_conflict):
        runner.storage.reserve_repositories(
            operation, requests, [source.lease, destination.lease],
            deadline=runner.operation_deadline(), compatible_pairs=adapter.profile.native_overlap,
        )

    actual_execute = supervisor.execute

    def pause_at_launch(intent, argv, **options):
        original = options["on_launch"]

        def launch(receipt):
            original(receipt)
            process = receipt.process
            assert signal_fixture(process, signal.SIGSTOP)
            receipt_path = root / f"{name}.launching"
            receipt_path.write_text(json.dumps(asdict(process)))
            receipt_path.rename(root / f"{name}.launched")

        options["on_launch"] = launch
        return actual_execute(intent, argv, **options)

    try:
        with patch.object(supervisor, "execute", side_effect=pause_at_launch):
            result = runner.run_command(
                adapter.copy(source.repository, destination.repository, (snapshot,)),
                deadline=runner.operation_deadline(), selection=replace(destination, claim_id=operation),
                source_repository=source.directory, operation_id=operation,
            )
        assert result.exit_code == 0
    finally:
        runner.storage.release_repositories(operation)
        runner.storage.release(destination.lease)
        runner.storage.release(source.lease)


def running_identity(root, name):
    marker = root / f"{name}.launched"
    await_condition(marker.exists)
    process = ProcessIdentity(**json.loads(marker.read_text()))
    assert process.presence() is Presence.SAME
    return process


def signal_fixture(process, number):
    try:
        descriptor = os.pidfd_open(process.pid)
    except ProcessLookupError:
        return False
    try:
        if process.presence() is not Presence.SAME:
            return False
        signal.pidfd_send_signal(descriptor, number)
        return True
    except ProcessLookupError:
        return False
    finally:
        os.close(descriptor)


def resume(process):
    if process is not None:
        signal_fixture(process, signal.SIGCONT)


def scenario(fixture, shared_target):
    prefix = "same" if shared_target else "different"
    names = (prefix + "-a", prefix + "-b")
    targets = ("secondary", "secondary" if shared_target else "third")
    fixture.source.write_text(prefix)
    fixture.restic("primary", "backup", str(fixture.source))
    snapshot = json.loads(fixture.restic("primary", "snapshots", "--latest", "1", "--json"))[0]["id"]
    root = fixture.root

    def command(name, target):
        return [sys.executable, __file__, str(root), name, target, snapshot]

    identities = []
    with (subprocess.Popen(command(names[0], targets[0]), env=fixture.env) as first,
          subprocess.Popen(command(names[1], targets[1]), env=fixture.env) as second):
        try:
            for name in names:
                await_condition((root / f"{name}.ready").exists)
            (root / f"{names[0]}.go").touch()
            identities.append(running_identity(root, names[0]))
            (root / f"{names[1]}.go").touch()
            if shared_target:
                await_condition((root / f"{names[1]}.blocked").exists)
                assert not (root / f"{names[1]}.launched").exists()
                resume(identities[0])
                assert first.wait(timeout=15) == 0
            identities.append(running_identity(root, names[1]))
            if not shared_target:
                assert all(process.presence() is Presence.SAME for process in identities)
            for process in identities:
                resume(process)
            assert first.wait(timeout=15) == second.wait(timeout=15) == 0
        finally:
            for process in identities:
                resume(process)
    with state_read(fixture.db, fixture.anchor, wait=5) as database:
        receipts = database.execute(
            """SELECT operation_id, state, exit_code FROM command_executions
               WHERE operation_id IN (?, ?) ORDER BY operation_id""", tuple(f"copy-{name}" for name in names),
        ).fetchall()
        assert [(row[1], row[2]) for row in receipts] == [("TERMINATED", 0)] * 2
        record(database, "AT-005" if shared_target else "AT-006",
               {"repository_targets": targets, "overlap": not shared_target, "receipts": receipts})


def main():
    with TemporaryDirectory(prefix="bm-i9-copy-claims-") as temporary:
        fixture = Fixture(Path(temporary))
        fixture.config.write_text(fixture.config.read_text().replace("operation_timeout_seconds = 15",
                                                                     "operation_timeout_seconds = 40").replace(
                                                                         "job_timeout_seconds = 50",
                                                                         "job_timeout_seconds = 120"))
        scenario(fixture, True)
        scenario(fixture, False)
    print("Independent processes serialized at a shared copy destination and overlapped for distinct destinations.")


if __name__ == "__main__":
    if len(sys.argv) == 5:
        child(Path(sys.argv[1]), *sys.argv[2:])
    else:
        main()
