"""Verify manager convergence preserves independently created identical metadata."""

import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from acceptance_trace import record
from backup_manager.convergence import logical_identity
from backup_manager.state import state_read
from linux_i5_job import Fixture
from linux_i6_replication import inventory


def main():
    with TemporaryDirectory(prefix="bm-i9-identical-") as temporary:
        fixture = Fixture(Path(temporary))
        fixture.job(secondary=True)
        originals = []
        for name in ("primary", "secondary"):
            subprocess.run(
                [shutil.which("restic"), "--no-cache", "--repo", str(fixture.storage / name),
                 "backup", "--time", "2025-01-01 00:00:00", "--host", "identical-fixture",
                 "--tag", "same", "--stdin", "--stdin-filename", "fixture"],
                env={"PATH": os.defpath, "RESTIC_PASSWORD": fixture.env[name.upper() + "_SECRET"]},
                input=b"same original content", capture_output=True, check=True, timeout=20,
            )
            original, = inventory(fixture, name)
            assert original.original is None
            originals.append(original)
        first, second = originals
        assert first.id != second.id
        fields = ("tree", "time", "hostname", "paths", "tags")
        assert all(getattr(first, field) == getattr(second, field) for field in fields)
        expected = {logical_identity(first), logical_identity(second)}
        assert len(expected) == 2
        job = fixture.cli("job", "run", "fixture")
        for name in ("primary", "secondary", "third"):
            assert expected <= {logical_identity(item) for item in inventory(fixture, name)}
        with state_read(fixture.db, fixture.anchor, wait=5) as database:
            observations = database.execute(
                """SELECT logical_identity, count(DISTINCT repository_key) FROM snapshot_provenance
                   WHERE logical_identity IN (?, ?) GROUP BY logical_identity""", tuple(sorted(expected)),
            ).fetchall()
            assert set(observations) == {(identity, 3) for identity in expected}
            record(database, "AT-031", {"run_id": job["operation_id"], "distinct_provenance": observations})
    print("Identical native metadata with independent origins converged as two distinct histories.")


if __name__ == "__main__":
    main()
