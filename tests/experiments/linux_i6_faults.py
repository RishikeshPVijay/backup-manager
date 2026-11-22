"""Inject failures into disposable I6 jobs while running real Restic copies."""

from dataclasses import replace
import json
import shutil
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backup_manager.cli import main as cli_main
from backup_manager.convergence import logical_identity
from backup_manager.jobs import DestinationRejected, JobRunner
from backup_manager.replication import Replication
from backup_manager.state import state_read

from linux_i5_job import Fixture
from linux_i6_replication import identities, inventory


def inject(mode, root, command):
    original = JobRunner.run_command
    copy = Replication.copy
    injected = False

    def run(self, invocation, **kwargs):
        nonlocal injected
        selected = kwargs.get("selection")
        if selected is None or selected.destination != "secondary" or "copy" not in invocation.argv:
            return original(self, invocation, **kwargs)
        if mode == "started-failure" and not injected:
            injected = True
            wrong = replace(invocation, env={**invocation.env, "RESTIC_PASSWORD": "fixture-wrong-destination"})
            return original(self, wrong, **kwargs)
        result = original(self, invocation, **kwargs)
        if mode == "source-disappears" and not injected and result.exit_code == 0:
            injected = True
            snapshot_id = invocation.argv[-1]
            path = root / "storage" / "primary" / "snapshots" / snapshot_id
            path.rename(root / "removed-fixture-snapshot")
        return result

    def copy_with_prelaunch_loss(self, phase, plan):
        nonlocal injected
        if mode != "replan" or injected or (phase, plan.source, plan.destination) != (
            "convergence", "secondary", "primary",
        ):
            return copy(self, phase, plan)
        inventory_method = self.inventory

        def fail_origin(repo):
            nonlocal injected
            if repo.destination == "secondary" and not injected:
                injected = True
                raise DestinationRejected("INJECTED_PRELAUNCH_LOSS")
            return inventory_method(repo)

        with patch.object(self, "inventory", side_effect=fail_origin):
            return copy(self, phase, plan)

    with patch.object(JobRunner, "run_command", run), patch.object(Replication, "copy", copy_with_prelaunch_loss):
        code = cli_main(command)
    assert injected, f"Fault {mode} was never reached."
    return code


def command(fixture, mode, expected=2):
    result = subprocess.run(
        [sys.executable, __file__, mode, str(fixture.root), *fixture.args("job", "run", "fixture")[3:]],
        env=fixture.env, capture_output=True, text=True, check=False, timeout=170,
    )
    return fixture.parse(result, expected)


def attempts(database, run_id):
    return database.execute(
        """SELECT attempt.attempt_id, attempt.phase, source.destination_id, target.destination_id,
                  attempt.state, attempt.launched, attempt.error_code, attempt.snapshot_ids_json
           FROM copy_attempts AS attempt
           JOIN repository_registrations AS source ON source.repository_key = attempt.source_key
           JOIN repository_registrations AS target ON target.repository_key = attempt.destination_key
           WHERE attempt.run_id = ? ORDER BY attempt.rowid""",
        (run_id,),
    ).fetchall()


def fixture_root(directory):
    fixture = Fixture(Path(directory))
    fixture.config.write_text(fixture.config.read_text().replace("job_timeout_seconds = 50", "job_timeout_seconds = 150"))
    fixture.job(secondary=True)
    return fixture


def started_failure():
    with TemporaryDirectory(prefix="bm-i6-failed-") as directory:
        fixture = fixture_root(directory)
        independent = fixture.root / "only-secondary"
        independent.write_text("independent secondary history")
        fixture.restic("secondary", "backup", str(independent))
        secondary_history, = inventory(fixture, "secondary")
        result = command(fixture, "started-failure")
        assert result["data"]["backup"] == "succeeded"
        primary = next(item for item in inventory(fixture, "primary") if item.id == result["data"]["snapshot_id"])
        logical = logical_identity(primary)
        assert logical not in identities(fixture, "secondary")
        assert logical in identities(fixture, "third")
        assert logical_identity(secondary_history) in identities(fixture, "primary")
        with state_read(fixture.db, fixture.anchor, wait=5) as database:
            rows = attempts(database, result["operation_id"])
            in_secondary = [row for row in rows if row[3] == "secondary"]
            assert len(in_secondary) == 1 and in_secondary[0][1:7] == (
                "replication", "primary", "secondary", "FAILED", 1, "COPY_EXECUTION_FAILED",
            )
            assert database.execute(
                "SELECT count(*) FROM transfer_requirements WHERE run_id = ? AND state = 'BLOCKED'",
                (result["operation_id"],),
            ).fetchone() == (1,)


def vanished_source():
    with TemporaryDirectory(prefix="bm-i6-source-loss-") as directory:
        fixture = fixture_root(directory)
        result = command(fixture, "source-disappears")
        assert result["data"]["backup"] == "succeeded"
        assert (fixture.root / "removed-fixture-snapshot").exists()
        assert any(item.original == result["data"]["snapshot_id"] for item in inventory(fixture, "secondary"))
        with state_read(fixture.db, fixture.anchor, wait=5) as database:
            rows = attempts(database, result["operation_id"])
            affected = [row for row in rows if row[1] == "replication" and row[3] == "secondary"]
            assert len(affected) == 1 and affected[0][4:7] == ("UNVERIFIED", 1, "COPY_REPRESENTATION_UNVERIFIED")
            assert database.execute(
                "SELECT count(*) FROM copy_evidence WHERE attempt_id = ?", (affected[0][0],),
            ).fetchone() == (0,)
            assert all(row[2] == "primary" for row in rows if row[1] == "replication")


def prelaunch_replan():
    with TemporaryDirectory(prefix="bm-i6-replan-") as directory:
        fixture = fixture_root(directory)
        independent = fixture.root / "secondary-history"
        independent.write_text("available from two peer repositories")
        fixture.restic("secondary", "backup", str(independent))
        original, = inventory(fixture, "secondary")
        subprocess.run(
            [shutil.which("restic"), "--no-cache", "--repo", str(fixture.storage / "third"), "copy",
             "--from-repo", str(fixture.storage / "secondary"), "--", original.id],
            env={
                "PATH": "/usr/bin:/bin", "RESTIC_PASSWORD": fixture.env["THIRD_SECRET"],
                "RESTIC_FROM_PASSWORD": fixture.env["SECONDARY_SECRET"],
            },
            check=True, capture_output=True, timeout=20,
        )
        result = command(fixture, "replan", expected=0)
        assert logical_identity(original) in identities(fixture, "primary")
        with state_read(fixture.db, fixture.anchor, wait=5) as database:
            to_primary = [row for row in attempts(database, result["operation_id"]) if row[3] == "primary"]
            assert [row[2:6] for row in to_primary] == [
                ("secondary", "primary", "PRELAUNCH_FAILED", 0),
                ("third", "primary", "SUCCEEDED", 1),
            ]


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(inject(sys.argv[1], Path(sys.argv[2]), sys.argv[3:]))
    started_failure()
    vanished_source()
    prelaunch_replan()
    print("Started failure isolation, fresh source-loss verification and prelaunch alternate-source replanning passed.")
