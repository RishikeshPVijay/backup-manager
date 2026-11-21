"""Exercise journaled process supervision using harmless user-scope fixtures."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from time import monotonic

from backup_manager.launch_journal import register_owner
from backup_manager.process_identity import Presence, ProcessIdentity
from backup_manager.state import initialize, state_write
from backup_manager.supervisor import SupervisionUncertain, run_journaled


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
        try:
            run_journaled(
                db, anchor, "timeout", owner, (sys.executable, "-c", "import time; time.sleep(45)"),
                deadline=monotonic() + 1,
            )
        except SupervisionUncertain:
            pass
        else:
            raise AssertionError("Deadline unexpectedly produced a successful result.")
        with closing(sqlite3.connect(db)) as database:
            rows = database.execute(
                "SELECT operation_id, state, exit_code FROM command_executions ORDER BY operation_id",
            ).fetchall()
        assert rows == [("success", "TERMINATED", 0), ("timeout", "LAUNCH_UNCERTAIN", None)]
    print("Installed-wheel scope, detached descendant, durable receipt and timeout passed.")


if __name__ == "__main__":
    main()
