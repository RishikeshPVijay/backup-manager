"""Exercise journaled process supervision using harmless user-scope fixtures."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch

from acceptance_trace import record
from backup_manager.claims import RepositoryClaim, admit_repository_claims

from backup_manager.launch_journal import register_owner
from backup_manager.process_identity import Presence, ProcessIdentity
from backup_manager.state import initialize, state_read, state_write
from backup_manager.supervisor import SupervisionUncertain, run_journaled


def uncertain_cancellation(db, anchor, owner):
    claim = RepositoryClaim("fixture-repo", 1, "COPY_DESTINATION")
    with state_write(db, anchor, wait=5) as database:
        database.execute(
            """INSERT INTO repository_registrations
               (repository_key, job_id, destination_id, restic_repository_id, repo_generation, approved_at)
               VALUES ('fixture-repo', 'job', 'primary', 'restic-id', 1, 'now')"""
        )
        assert admit_repository_claims(database, "uncertain", owner, [claim])
    try:
        with patch("backup_manager.supervisor.empty_cgroup", return_value=False):
            run_journaled(
                db, anchor, "uncertain", owner, (sys.executable, "-c", "import time; time.sleep(45)"),
                deadline=monotonic() + 10, cancelled=lambda: True,
            )
    except SupervisionUncertain:
        pass
    else:
        raise AssertionError("Unproved termination returned successful cancellation.")
    with state_write(db, anchor, wait=5) as database:
        execution = database.execute(
            """SELECT state, launched_at, termination_evidence FROM command_executions
               WHERE operation_id = 'uncertain'"""
        ).fetchone()
        assert execution[0] == "LAUNCH_UNCERTAIN" and execution[1] and execution[2] is None
        contender = register_owner(database, ProcessIdentity.capture(os.getpid()), "contender")
        assert admit_repository_claims(database, "competing", contender, [claim]) is None
        record(database, "AT-028", {"termination_evidence": None, "competing_claim": "blocked", "execution": execution[0]})


def main():
    with TemporaryDirectory(prefix="bm-supervisor-") as directory:
        root = Path(directory)
        db = root / "state" / "manager.sqlite3"
        anchor = root / "witness" / "state.identity"
        db.parent.mkdir(mode=0o700)
        anchor.parent.mkdir(mode=0o700)
        initialize(db, anchor)
        with state_write(db, anchor) as database:
            owner = register_owner(database, ProcessIdentity.capture(os.getpid()), "fixture-supervisor")
        received = run_journaled(
            db, anchor, "stdin", owner,
            (sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"),
            deadline=monotonic() + 10, stdin_payload=b"disposable worker payload",
        )
        assert received.exit_code == 0 and received.stdout == b"disposable worker payload"
        large = run_journaled(
            db, anchor, "large", owner,
            (sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read() * 20)"),
            deadline=monotonic() + 10, stdin_payload=b"x" * 8192,
        )
        assert large.stdout == b"x" * 163840 and not large.truncated
        try:
            run_journaled(
                db, anchor, "stdin", owner, (sys.executable, "-c", "raise AssertionError"),
                deadline=monotonic() + 10,
            )
        except SupervisionUncertain:
            pass
        else:
            raise AssertionError("A recorded operation was authorized for a second launch.")
        marker = root / "child.identity"
        script = """
from dataclasses import asdict
import json, pathlib, subprocess, sys
from backup_manager.process_identity import ProcessIdentity
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(45)"],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
pathlib.Path(sys.argv[1]).write_text(json.dumps(asdict(ProcessIdentity.capture(child.pid))))
print("fixture output")
"""
        result = run_journaled(
            db, anchor, "success", owner, (sys.executable, "-c", script, str(marker)),
            deadline=monotonic() + 10,
        )
        assert result.exit_code == 0 and result.stdout == b"fixture output\n"
        child = ProcessIdentity(**json.loads(marker.read_text()))
        assert child.presence() is Presence.GONE
        event = root / "cancel"
        trigger = "import pathlib,sys,time; time.sleep(0.4); pathlib.Path(sys.argv[1]).touch()"
        with subprocess.Popen([sys.executable, "-c", trigger, str(event)]) as sibling:
            def cancel_requested():
                with state_read(db, anchor):
                    return event.exists()

            stopped = run_journaled(
                db, anchor, "cancel", owner, (sys.executable, "-c", "import time; time.sleep(45)"),
                deadline=monotonic() + 10, cancelled=cancel_requested,
            )
            assert stopped.exit_code is None
            assert sibling.wait(timeout=5) == 0
        timed_out = run_journaled(
            db, anchor, "timeout", owner, (sys.executable, "-c", "import time; time.sleep(45)"),
            deadline=monotonic() + 2,
        )
        assert timed_out.exit_code is None and timed_out.stop_reason == "timeout"
        with closing(sqlite3.connect(db)) as database:
            rows = database.execute(
                "SELECT operation_id, state, exit_code FROM command_executions ORDER BY operation_id",
            ).fetchall()
        assert rows == [
            ("cancel", "TERMINATED", None), ("large", "TERMINATED", 0), ("stdin", "TERMINATED", 0),
            ("success", "TERMINATED", 0), ("timeout", "TERMINATED", None),
        ]
        uncertain_cancellation(db, anchor, owner)
    print("Installed-wheel scope, detached descendant, durable receipt, cross-process cancellation and timeout passed.")


if __name__ == "__main__":
    main()
