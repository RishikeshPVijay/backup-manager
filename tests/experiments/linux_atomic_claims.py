"""Exercise reverse-ordered repository claim sets in independent processes."""

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep

from backup_manager.claims import RepositoryClaim, admit_repository_claims
from backup_manager.launch_journal import register_owner
from backup_manager.process_identity import ProcessIdentity
from backup_manager.state import initialize, state_read, state_write


def contend(db, anchor, gate, names):
    until = monotonic() + 5
    while not gate.exists():
        if monotonic() >= until:
            raise TimeoutError
        sleep(0.01)
    with state_write(db, anchor, wait=5) as database:
        owner = register_owner(database, ProcessIdentity.capture(os.getpid()), "fixture")
        claim = admit_repository_claims(database, "request-" + names, owner, [
            RepositoryClaim(name, 1, "COPY_DESTINATION") for name in names
        ])
    return 0 if claim else 5


def main():
    with TemporaryDirectory(prefix="bm-atomic-claims-") as temporary:
        root = Path(temporary)
        db, anchor = root / "db" / "state", root / "witness" / "identity"
        db.parent.mkdir(mode=0o700)
        anchor.parent.mkdir(mode=0o700)
        initialize(db, anchor)
        with state_write(db, anchor) as database:
            database.executemany(
                "INSERT INTO repository_registrations (repository_key, job_id, destination_id, restic_repository_id, repo_generation, approved_at) VALUES (?, 'job', ?, ?, 1, 'time')",
                [(name, name, f"id-{name}") for name in "ab"],
            )
        gate = root / "gate"
        command = [sys.executable, __file__, str(db), str(anchor), str(gate)]
        with subprocess.Popen([*command, "ab"]) as first, subprocess.Popen([*command, "ba"]) as second:
            gate.touch()
            assert sorted((first.wait(timeout=10), second.wait(timeout=10))) == [0, 5]
        with state_read(db, anchor) as database:
            rows = database.execute("SELECT claim_set_id, repository_key FROM repository_claims ORDER BY repository_key").fetchall()
            assert [row[1] for row in rows] == ["a", "b"]
            assert rows[0][0] == rows[1][0]
    print("Reverse-ordered requests produced one complete claim set and one busy result.")


if __name__ == "__main__":
    if len(sys.argv) == 5:
        sys.exit(contend(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]))
    main()
