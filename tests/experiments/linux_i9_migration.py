"""Record a failed migration, preserved backup, and refused runtime admission."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch

from acceptance_trace import record
from backup_manager.state import MIGRATIONS, StateUnsafe, check, connect, initialize, migration, upgrade_state


def main():
    with TemporaryDirectory(prefix="bm-i9-migration-") as temporary:
        root = Path(temporary)
        db, anchor = root / "state" / "db", root / "witness" / "identity"
        db.parent.mkdir(mode=0o700)
        anchor.parent.mkdir(mode=0o700)
        with patch("backup_manager.state.MIGRATIONS", MIGRATIONS[:-1]):
            old = initialize(db, anchor)

        def broken_migration(name):
            if name == MIGRATIONS[-1]:
                return "CREATE TABLE interrupted(x); INVALID SQL;", "broken"
            return migration(name)

        backup = root / "before-migration.db"
        with patch("backup_manager.state.migration", side_effect=broken_migration):
            try:
                upgrade_state(db, anchor, backup)
            except StateUnsafe:
                pass
            else:
                raise AssertionError("Injected migration was reported successful.")
        try:
            check(db, anchor)
        except StateUnsafe:
            pass
        else:
            raise AssertionError("State with a failed migration admitted work.")

        with closing(connect(db, "ro&immutable=1")) as live, closing(sqlite3.connect(backup)) as previous:
            version = live.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
            assert version == previous.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
            assert version == len(MIGRATIONS) - 1
            assert live.execute("SELECT name FROM sqlite_master WHERE name = 'interrupted'").fetchone() is None
            manifest = json.loads(backup.with_name(backup.name + ".manifest.json").read_text())
            assert manifest["sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest()
            assert manifest["generation"] == old["generation"]
            record(live, "AT-038", {"preserved_schema": version, "backup_manifest": manifest, "admission": "unsafe"})
    print("Injected migration rolled back and preserved its validated pre-upgrade backup.")


if __name__ == "__main__":
    main()
