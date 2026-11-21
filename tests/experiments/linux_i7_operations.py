"""Disposable end-to-end verification of I7 administrative and job operations."""

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backup_manager.cli import main as cli_main
from backup_manager.jobs import JobRunner
from backup_manager.state import state_read
from linux_i5_job import Fixture, quote


def inject_retention_failure(command):
    original = JobRunner.run_command

    def run(runner, invocation, **kwargs):
        selected = kwargs.get("selection")
        if selected is not None and selected.destination == "primary" and "forget" in invocation.argv:
            invocation = replace(invocation, env={**invocation.env, "RESTIC_PASSWORD": "fixture-wrong-password"})
        return original(runner, invocation, **kwargs)

    with patch.object(JobRunner, "run_command", run):
        return cli_main(command)


def main():
    with TemporaryDirectory(prefix="bm-i7-operations-") as temporary:
        fixture = Fixture(Path(temporary))
        root = fixture.root
        notice = root / "job-notice"
        notification = "import pathlib,os,sys;pathlib.Path(sys.argv[1]).write_text(os.environ['BACKUP_MANAGER_EVENT_JSON']);sys.exit(3)"
        fixture.config.write_text(fixture.config.read_text() + f"""
[[notifications.job]]
executable = {quote(sys.executable)}
args = {json.dumps(["-c", notification, str(notice)])}
""")
        fixture.job(secondary=True, keep_last=2)
        initial = fixture.db.read_bytes(), fixture.anchor.read_bytes()
        assert fixture.cli("job", "run", "fixture", "--dry-run")["data"]["mode"] == "dry-run"
        assert fixture.cli("doctor")["status"] == "full"
        fixture.cli("repository", "inspect", "fixture", "primary")
        assert initial == (fixture.db.read_bytes(), fixture.anchor.read_bytes())
        stale_backup = root / "stale-backup"
        fixture.cli("state", "backup", str(stale_backup))
        fixture.cli("job", "run", "fixture", "--preflight")
        assert not json.loads(fixture.restic("primary", "snapshots", "--json"))

        for revision in range(3):
            fixture.source.write_text(f"revision {revision}")
            fixture.restic("primary", "backup", str(fixture.source))
        fixture.source.write_text("new managed revision")
        outcome = fixture.cli("job", "run", "fixture")
        phases = outcome["data"]["phases"]
        assert max(i for i, row in enumerate(phases) if row["phase"] == "convergence") < min(
            i for i, row in enumerate(phases) if row["phase"] == "retention"
        )
        assert len([row for row in phases if row["phase"] == "retention" and row["status"] == "succeeded"]) == 3
        listed = fixture.cli("operation", "list")["data"]["operations"]
        assert {"job", "command", "copy"} <= {row["kind"] for row in listed}
        command = next(row for row in listed if row["kind"] == "command")
        assert fixture.cli("operation", "inspect", command["id"])["data"]["operations"] == [command]
        assert not fixture.cli("operation", "list", "--active-only")["data"]["operations"]
        for repo in ("primary", "secondary", "third"):
            assert len(json.loads(fixture.restic(repo, "snapshots", "--json"))) == 2
        assert json.loads(notice.read_text())["status"] == "full"
        with state_read(fixture.db, fixture.anchor, wait=5) as database:
            assert database.execute("SELECT status FROM notification_attempts").fetchall() == [("failed",)]
            copies = database.execute(
                "SELECT snapshot_ids_json FROM copy_attempts WHERE phase = 'replication' AND state = 'SUCCEEDED'"
            ).fetchall()
            assert len(copies) == 2 and all(len(json.loads(row[0])) == 4 for row in copies)

        fault = subprocess.run(
            [sys.executable, __file__, "retention-failure", *fixture.args("job", "run", "fixture")[3:]],
            env=fixture.env, capture_output=True, text=True, check=False, timeout=120,
        )
        failed_retention = fixture.parse(fault, 0)
        assert failed_retention["data"]["backup"] == "succeeded"
        assert [(row["target_id"], row["status"]) for row in failed_retention["data"]["phases"]
                if row["phase"] == "retention"] == [
            ("primary", "failed"), ("secondary", "succeeded"), ("third", "succeeded"),
        ]

        before = fixture.db.read_bytes(), fixture.anchor.read_bytes()
        fixture.cli("retention", "preview", "fixture", "--destination", "primary")
        assert before == (fixture.db.read_bytes(), fixture.anchor.read_bytes())
        job_path = fixture.config.parent / "jobs" / "fixture.toml"
        job_path.write_text(job_path.read_text().replace("keep_last = 2", "keep_last = 2\nprune = true"))
        standalone = fixture.cli("retention", "run", "fixture", "--destination", "primary")
        assert [(row["operation"], row["status"]) for row in standalone["data"]["destinations"][0]["operations"]] == [
            ("forget", "succeeded"), ("prune", "succeeded"),
        ]
        fixture.cli("verify", "run", "fixture", "--destination", "primary")
        fixture.cli("repository", "unlock", "fixture", "primary", "--yes")

        repo = fixture.storage / "primary"
        archived = root / "old-primary"
        repo.rename(archived)
        fixture.restic("primary", "init", "--repository-version", "2")
        new_id = json.loads(fixture.restic("primary", "cat", "config"))["id"]
        fixture.cli("repository", "approve-replacement", "fixture", "primary",
                    "--expected-id", "f" * 64, "--yes", expected=8)
        fixture.cli("repository", "approve-replacement", "fixture", "primary", "--expected-id", new_id, "--yes")

        fixture.job(job_id="new", primary_repo="peer")
        nonempty = fixture.storage / "peer"
        fixture.cli("repository", "init", "new", "primary", "--yes", expected=3)
        nonempty.rename(root / "old-peer")
        fixture.cli("repository", "init", "new", "primary", "--yes")
        fixture.cli("repository", "inspect", "new", "primary")

        backup = root / "verified-backup"
        fixture.cli("state", "backup", str(backup))
        fixture.db.rename(root / "unavailable-live-state")
        fixture.cli("state", "check", expected=8)
        witness = fixture.anchor.read_bytes()
        fixture.cli("state", "recover", str(stale_backup), "--yes", expected=8)
        assert witness == fixture.anchor.read_bytes() and not fixture.db.exists()
        recovered = fixture.cli("state", "recover", str(backup), "--yes")
        assert recovered["data"]["verified_repositories"] == 4
        fixture.cli("state", "check")
        assert (fixture.storage / "mounted").exists()
    print("I7 scoped retention, dry-run/preflight, verification, notifications, init, replacement and recovery passed.")


if __name__ == "__main__":
    sys.exit(inject_retention_failure(sys.argv[2:])) if len(sys.argv) > 1 else main()
