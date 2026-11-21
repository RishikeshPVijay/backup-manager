"""Run the storage coordinator across independent CLI-like fixture processes."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, monotonic_ns, sleep

from backup_manager.state import initialize, state_read
from backup_manager.storage_coordinator import StorageCoordinator, StorageUnavailable


def poll(predicate, timeout=12):
    end = monotonic() + timeout
    while monotonic() < end:
        if predicate():
            return
        sleep(0.05)
    raise AssertionError("Storage fixture condition timed out.")


def paths(root):
    return root / "db" / "state", root / "witness" / "identity"


def client(root, mode):
    db, anchor = paths(root)
    coordinator = StorageCoordinator(db, anchor, {
        "disk": {"provider": "fixture_storage", "lifecycle": "on_demand", "config": {"root": str(root)}},
    }, acquire_seconds=6, release_seconds=6)
    try:
        lease = coordinator.acquire(
            "disk", deadline=monotonic_ns() + 8_000_000_000,
            cancelled=lambda: mode == "withdraw" and (root / "withdraw").exists(),
        )
    except StorageUnavailable:
        if mode != "withdraw":
            raise
        (root / "withdrawn").touch()
        return
    if mode == "withdraw":
        raise AssertionError("Waiting participant did not withdraw.")
    (root / "acquired").touch()
    poll(lambda: (root / "relinquish").exists())
    coordinator.release(lease)


def all_terminal(db, anchor):
    with state_read(db, anchor, wait=2) as database:
        return database.execute(
            "SELECT count(*) FROM storage_operations WHERE state != 'TERMINATED'",
        ).fetchone() == (0,)


def main():
    with TemporaryDirectory(prefix="bm-shared-storage-") as directory:
        root = Path(directory)
        db, anchor = paths(root)
        db.parent.mkdir(mode=0o700)
        anchor.parent.mkdir(mode=0o700)
        initialize(db, anchor)
        entry = root / "fixture_storage-1.0.dist-info"
        entry.mkdir()
        (entry / "entry_points.txt").write_text(
            "[backup_manager.storage]\nfixture_storage = lifecycle_provider:FixtureProvider\n",
        )
        shutil.copyfile(Path(__file__).parent / "fixtures" / "lifecycle_provider.py", root / "lifecycle_provider.py")
        env = {**os.environ, "PYTHONPATH": str(root)}

        def waiters():
            with state_read(db, anchor, wait=2) as database:
                return database.execute("SELECT count(*) FROM storage_claims WHERE state = 'WAITING'").fetchone()[0]

        with subprocess.Popen([sys.executable, __file__, str(root), "withdraw"], env=env) as first:
            try:
                poll(lambda: (root / "actions").exists())
                with subprocess.Popen([sys.executable, __file__, str(root), "stay"], env=env) as second:
                    try:
                        poll(lambda: waiters() == 2)
                        (root / "withdraw").touch()
                        poll(lambda: (root / "withdrawn").exists())
                        poll(lambda: (root / "acquired").exists())
                        assert (root / "mounted").exists()
                        assert (root / "actions").read_text().splitlines() == ["acquire"]
                        (root / "relinquish").touch()
                        assert second.wait(timeout=10) == 0
                        def released():
                            with state_read(db, anchor, wait=2) as database:
                                return database.execute(
                                    "SELECT state FROM storage_operations WHERE kind = 'release'",
                                ).fetchone() == ("TERMINATED",)
                        poll(released)
                    finally:
                        if second.poll() is None:
                            second.kill()
                assert first.wait(timeout=10) == 0
            finally:
                if first.poll() is None:
                    first.kill()
        assert not (root / "mounted").exists()
        assert (root / "actions").read_text().splitlines() == ["acquire", "release"]
        # An externally created mount is reused and preserved.
        (root / "mounted").touch()
        (root / "acquired").unlink()
        with subprocess.Popen([sys.executable, __file__, str(root), "stay"], env=env) as external:
            assert external.wait(timeout=10) == 0
        assert (root / "mounted").exists()
        assert (root / "actions").read_text().splitlines() == ["acquire", "release"]
        (root / "mounted").unlink()
        (root / "withdraw").unlink()
        (root / "withdrawn").unlink()

        # The last waiter withdraws while the worker is active. The next
        # independent acquisition needs no cooldown or operator intervention.
        with subprocess.Popen([sys.executable, __file__, str(root), "withdraw"], env=env) as final:
            poll(lambda: (root / "actions").read_text().splitlines() == ["acquire", "release", "acquire"])
            (root / "withdraw").touch()
            assert final.wait(timeout=10) == 0

        def cancelled_cleanly():
            with state_read(db, anchor, wait=2) as database:
                return database.execute(
                    "SELECT state FROM storage_operations WHERE kind = 'acquire' ORDER BY rowid DESC LIMIT 1",
                ).fetchone() == ("TERMINATED",)

        poll(cancelled_cleanly)
        assert not (root / "mounted").exists()
        with subprocess.Popen([sys.executable, __file__, str(root), "stay"], env=env) as retry:
            assert retry.wait(timeout=10) == 0
        poll(lambda: (root / "actions").read_text().splitlines() == [
            "acquire", "release", "acquire", "acquire", "release",
        ] and not (root / "mounted").exists())
        poll(lambda: all_terminal(db, anchor))
    print("Verified shared acquisition, independent release, external preservation, final-waiter cancellation and fresh retry.")


if __name__ == "__main__":
    client(Path(sys.argv[1]), sys.argv[2]) if len(sys.argv) == 3 else main()
