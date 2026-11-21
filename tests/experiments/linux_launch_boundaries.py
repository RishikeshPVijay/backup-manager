"""Inject real process exits around the persisted launch handoff."""

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest.mock import patch

from backup_manager.crash_recovery import reconcile_crashed_owners
from backup_manager.launch_journal import register_owner
from backup_manager.process_identity import ProcessIdentity
from backup_manager.state import initialize, state_read, state_write
from backup_manager.supervisor import run_journaled
import backup_manager.supervisor as supervisor


def fault(db, anchor, marker, boundary):
    with state_write(db, anchor, wait=5) as database:
        owner = register_owner(database, ProcessIdentity.capture(os.getpid()), "boundary")
    real = supervisor.execute

    def interrupted(*args, **kwargs):
        if boundary == "intent":
            os._exit(33)
        callback = "on_scope_ready" if boundary == "prepared" else "on_launch"
        original = kwargs[callback]

        def crash(receipt):
            if boundary == "prepared":
                original(receipt)
            os._exit(33)

        kwargs[callback] = crash
        return real(*args, **kwargs)

    script = "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()"
    with patch.object(supervisor, "execute", side_effect=interrupted):
        run_journaled(
            db, anchor, "boundary", owner, (sys.executable, "-c", script, str(marker)),
            deadline=monotonic() + 10,
        )


def scenario(boundary):
    with TemporaryDirectory(prefix="bm-launch-boundary-") as temporary:
        root = Path(temporary)
        db, anchor, marker = root / "db" / "state", root / "witness" / "anchor", root / "started"
        db.parent.mkdir(mode=0o700)
        anchor.parent.mkdir(mode=0o700)
        initialize(db, anchor)
        child = subprocess.run([sys.executable, __file__, str(db), str(anchor), str(marker), boundary],
                               check=False, timeout=15)
        assert child.returncode == 33
        with state_read(db, anchor, wait=5) as database:
            states = database.execute("SELECT type FROM events ORDER BY rowid").fetchall()
            assert states == ([("launch_intent",)] if boundary == "intent" else [
                ("launch_intent",), ("scope_ready",),
            ])
        until = monotonic() + 10
        while not any(recovered for _, recovered in reconcile_crashed_owners(db, anchor)):
            if monotonic() >= until:
                raise AssertionError("Could not verify interrupted scope exit.")
            sleep(0.05)
        assert marker.exists() == (boundary == "exec")
        with state_read(db, anchor, wait=5) as database:
            assert database.execute("SELECT state, exit_code FROM command_executions").fetchall() == [
                ("TERMINATED", None),
            ]


if __name__ == "__main__":
    if len(sys.argv) == 5:
        fault(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
    else:
        for boundary in ("intent", "prepared", "exec"):
            scenario(boundary)
        print("Crash after intent, prepared scope, and exec before launch receipt never recorded success.")
