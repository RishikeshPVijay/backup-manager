"""Exercise the installed certified adapter and private Restic secret transport."""

import os
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
from time import monotonic

from backup_manager.launch_journal import register_owner
from backup_manager.process_identity import ProcessIdentity
from backup_manager.restic import Binary, Repository, Restic, confirm_backup, parse_snapshots, run_invocation
from backup_manager.state import initialize, state_write


def main():
    binary = Binary.identify("restic")
    with TemporaryDirectory(prefix="bm-scoped-restic-") as directory:
        root = Path(directory)
        db, anchor = root / "state" / "db", root / "witness" / "identity"
        db.parent.mkdir(mode=0o700)
        anchor.parent.mkdir(mode=0o700)
        initialize(db, anchor)
        with state_write(db, anchor) as database:
            owner = register_owner(database, ProcessIdentity.capture(os.getpid()), "scoped-restic-fixture")

        def run(name, invocation):
            result = run_invocation(db, anchor, name, owner, invocation, deadline=monotonic() + 20)
            assert result.exit_code == 0 and not result.truncated
            return result

        adapter = Restic.certified(binary, run("version", Restic.version(binary)))
        source, target = (
            Repository(root / name, secrets.token_hex(24).encode()) for name in ("source", "target")
        )
        run("init-source", adapter.explicit_init(source, 2))
        run("init-target", adapter.explicit_init(target, 2))
        source_identity = adapter.repository_identity(run("source-identity", adapter.identity(source)))
        target_identity = adapter.repository_identity(run("target-identity", adapter.identity(target)))
        assert source_identity.id != target_identity.id
        payload = root / "payload"
        payload.write_text("disposable Restic fixture")
        result = run("backup", adapter.backup(source, (payload,)))
        inventory = parse_snapshots(run("source-inventory", adapter.snapshots(source)))
        snapshot = confirm_backup(result, (), inventory, expected_paths=(payload,))
        run("copy", adapter.copy(source, target, (snapshot,)))
        copied, = parse_snapshots(run("target-inventory", adapter.snapshots(target)))
        assert copied.original == snapshot and copied.tree == inventory[0].tree
        for secret in (source.password, target.password):
            assert secret not in db.read_bytes() and secret not in anchor.read_bytes()
    print("Scoped exact-profile backup/copy confirmed and disposable repositories cleaned up.")


if __name__ == "__main__":
    main()
