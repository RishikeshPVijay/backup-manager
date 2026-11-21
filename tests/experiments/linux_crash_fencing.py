"""Kill a disposable owner with an active command and verify recovery fencing."""

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep

from backup_manager.claims import RepositoryClaim, admit_repository_claims
from backup_manager.crash_recovery import reconcile_crashed_owners
from backup_manager.launch_journal import register_owner
from backup_manager.process_identity import ProcessIdentity
from backup_manager.state import initialize, state_read, state_write
from backup_manager.supervisor import run_journaled


def fixture_owner(db, anchor):
    with state_write(db, anchor, wait=5) as database:
        owner = register_owner(database, ProcessIdentity.capture(os.getpid()), "crash-fixture")
        database.execute("INSERT INTO repository_registrations VALUES ('repo', 'job', 'primary', 'fixture-id', 1, 'now')")
        admit_repository_claims(database, "crash-operation", owner, [RepositoryClaim("repo", 1, "COPY_DESTINATION")])
    run_journaled(
        db, anchor, "crash-operation", owner, (sys.executable, "-c", "import time; time.sleep(45)"),
        deadline=monotonic() + 50,
    )


def wait_for_launch(db, anchor):
    end = monotonic() + 10
    while monotonic() < end:
        with state_read(db, anchor, wait=5) as database:
            row = database.execute(
                "SELECT owner_id, process_identity_json FROM command_executions WHERE state = 'LAUNCHED'",
            ).fetchone()
        if row:
            return row[0], Path(json.loads(row[1])["containment_id"]).name
        sleep(0.05)
    raise AssertionError("Fixture failed to launch.")


def main():
    with TemporaryDirectory(prefix="bm-crash-fencing-") as temporary:
        root = Path(temporary)
        db, anchor = root / "state" / "db", root / "witness" / "anchor"
        db.parent.mkdir(mode=0o700)
        anchor.parent.mkdir(mode=0o700)
        initialize(db, anchor)
        unit = None
        with subprocess.Popen([sys.executable, __file__, str(db), str(anchor)]) as child:
            try:
                owner, unit = wait_for_launch(db, anchor)
                child.kill()
                assert child.wait(timeout=5) < 0
                assert (owner, False) in reconcile_crashed_owners(db, anchor)
                with state_write(db, anchor, wait=5) as database:
                    contender = register_owner(database, ProcessIdentity.capture(os.getpid()), "fixture-recovery")
                    requests = [RepositoryClaim("repo", 1, "COPY_DESTINATION")]
                    assert admit_repository_claims(database, "replacement", contender, requests) is None
                subprocess.run(["systemctl", "--user", "stop", unit], check=True, timeout=10)
                until = monotonic() + 10
                while (owner, True) not in reconcile_crashed_owners(db, anchor):
                    if monotonic() >= until:
                        raise AssertionError("Scope exit was not proved.")
                    sleep(0.05)
                with state_read(db, anchor, wait=5) as database:
                    state, evidence = database.execute(
                        "SELECT state, termination_evidence FROM command_executions",
                    ).fetchone()
                    assert state == "TERMINATED"
                    assert json.loads(evidence) == {"interrupted": True, "observation": "empty_recorded_cgroup"}
                    assert database.execute("SELECT state FROM repository_claim_sets").fetchone() == ("RELEASED",)
            finally:
                if child.poll() is None:
                    child.kill()
                if unit:
                    subprocess.run(["systemctl", "--user", "stop", unit], check=False, timeout=10,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("SIGKILL kept claims fenced while the scoped child was alive and released them after verified scope exit.")


if __name__ == "__main__":
    fixture_owner(Path(sys.argv[1]), Path(sys.argv[2])) if len(sys.argv) == 3 else main()
