"""Exercise the installed certified adapter and private Restic secret transport."""

import os
from pathlib import Path
import secrets
import shutil
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch

from acceptance_trace import record
import backup_manager.supervisor as supervisor

from backup_manager.launch_journal import register_owner
from backup_manager.process_identity import ProcessIdentity
from backup_manager.restic import Binary, Repository, Restic, confirm_backup, parse_snapshots, run_invocation
from backup_manager.state import initialize, state_read, state_write


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

        copied_binary = root / "restic"
        shutil.copy2(binary.path, copied_binary)
        pending = Restic.version(Binary.identify(str(copied_binary)))
        replacement = root / "replacement"
        shutil.copy2("/usr/bin/true", replacement)
        replacement.replace(copied_binary)
        rejected = run_invocation(db, anchor, "replaced-binary", owner, pending, deadline=monotonic() + 20)
        assert rejected.stop_reason == "not_started"
        with state_read(db, anchor) as database:
            assert database.execute(
                "SELECT state, launched_at FROM command_executions WHERE operation_id = 'replaced-binary'"
            ).fetchone() == ("TERMINATED", None)
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
        observed_argv = []
        actual_execute = supervisor.execute

        def observe_launch(intent, argv, **options):
            original = options["on_launch"]

            def launch(receipt):
                command_line = Path(f"/proc/{receipt.process.pid}/cmdline").read_bytes()
                assert b"backup" in command_line.split(b"\0")
                assert all(secret not in command_line for secret in (source.password, target.password))
                observed_argv.append(True)
                original(receipt)

            options["on_launch"] = launch
            return actual_execute(intent, argv, **options)

        with patch.object(supervisor, "execute", side_effect=observe_launch):
            result = run("backup", adapter.backup(source, (payload,)))
        assert observed_argv == [True]
        inventory = parse_snapshots(run("source-inventory", adapter.snapshots(source)))
        snapshot = confirm_backup(result, (), inventory, expected_paths=(payload,))
        run("copy", adapter.copy(source, target, (snapshot,)))
        copied, = parse_snapshots(run("target-inventory", adapter.snapshots(target)))
        assert copied.original == snapshot and copied.tree == inventory[0].tree
        for secret in (source.password, target.password):
            assert secret not in db.read_bytes() and secret not in anchor.read_bytes()
        with state_read(db, anchor, wait=5) as database:
            record(database, "AT-025", {"live_restic_argv": "sentinels absent", "persisted": "sentinels absent"})
    print("Scoped exact-profile backup/copy confirmed and disposable repositories cleaned up.")


if __name__ == "__main__":
    main()
