"""Fail a live Restic enumeration after a previous complete inventory."""

from dataclasses import replace
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from acceptance_trace import record
from backup_manager.cli import main as cli_main
from backup_manager.jobs import JobRunner
from backup_manager.replication import Replication
from backup_manager.state import state_read
from linux_i5_job import Fixture


def injected_run(args):
    active = False
    injected = 0
    enumerate_repository = Replication.enumerate
    original = JobRunner.run_command

    def enumerate_with_loss(replication, name):
        nonlocal active
        active = name == "primary"
        try:
            return enumerate_repository(replication, name)
        finally:
            active = False

    def run(runner, invocation, **kwargs):
        nonlocal injected
        if active and "snapshots" in invocation.argv:
            injected += 1
            invocation = replace(invocation, env={**invocation.env, "RESTIC_PASSWORD": "rejected-fixture-password"})
        return original(runner, invocation, **kwargs)

    with patch.object(Replication, "enumerate", enumerate_with_loss), patch.object(JobRunner, "run_command", run):
        status = cli_main(args)
    assert injected == 2
    return status


def provenance(database):
    return database.execute(
        """SELECT snapshot_id, logical_identity FROM snapshot_provenance
           JOIN repository_registrations USING(repository_key)
           WHERE destination_id = 'primary' ORDER BY snapshot_id"""
    ).fetchall()


def main():
    with TemporaryDirectory(prefix="bm-i9-incomplete-inventory-") as temporary:
        fixture = Fixture(Path(temporary))
        fixture.job(secondary=True)
        fixture.cli("job", "run", "fixture")
        with state_read(fixture.db, fixture.anchor, wait=5) as database:
            before = provenance(database)
            assert before
        fixture.source.write_text("new fixture data")
        result = subprocess.run(
            [sys.executable, __file__, "--inject", *fixture.args("job", "run", "fixture")[3:]],
            env=fixture.env, capture_output=True, text=True, check=False, timeout=80,
        )
        payload = fixture.parse(result, 2)
        assert payload["data"]["backup"] == "succeeded"
        codes = {row["error_code"] for row in payload["data"]["phases"]}
        assert {"SOURCE_ENUMERATION_FAILED", "INVENTORY_ENUMERATION_FAILED"} <= codes
        with state_read(fixture.db, fixture.anchor, wait=5) as database:
            assert provenance(database) == before
            assert database.execute(
                "SELECT count(*) FROM copy_attempts WHERE run_id = ?", (payload["operation_id"],),
            ).fetchone() == (0,)
            record(database, "AT-011", {"previous_complete_provenance": before, "copy_attempts": 0})
    print("Failed native enumerations preserved the previous complete provenance and scheduled no copies.")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--inject"]:
        sys.exit(injected_run(sys.argv[2:]))
    main()
