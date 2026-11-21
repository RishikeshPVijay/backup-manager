from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from backup_manager.state import StateUnsafe, check, initialize, state_write


class StateTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.path = root / "database" / "manager.sqlite3"
        self.anchor = root / "witness" / "manager.identity"
        self.path.parent.mkdir(mode=0o700)
        self.anchor.parent.mkdir(mode=0o700)

    def test_missing_state_does_not_create_database_or_anchor(self):
        with self.assertRaises(StateUnsafe):
            check(self.path, self.anchor)
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_initialize_migrates_once_and_check_is_read_only(self):
        record = initialize(self.path, self.anchor)
        before = {p.name: p.read_bytes() for p in self.path.parent.iterdir()}
        self.assertEqual(check(self.path, self.anchor), record)
        with self.assertRaises(StateUnsafe):
            initialize(self.path, self.anchor)
        self.assertEqual(
            {p.name: p.read_bytes() for p in self.path.parent.iterdir()}, before,
        )
        with closing(sqlite3.connect(self.path)) as database:
            self.assertEqual(database.execute("PRAGMA journal_mode").fetchone(), ("wal",))
            self.assertEqual(database.execute("SELECT version FROM schema_migrations").fetchall(), [(1,), (2,), (3,)])

    def test_identity_and_generation_mismatch_is_unsafe(self):
        record = initialize(self.path, self.anchor)
        record["generation"] = 2
        self.anchor.write_text(json.dumps(record))
        with self.assertRaises(StateUnsafe):
            check(self.path, self.anchor)

    def test_corruption_is_unsafe(self):
        initialize(self.path, self.anchor)
        self.path.write_bytes(b"unreadable database")
        with self.assertRaises(StateUnsafe):
            check(self.path, self.anchor)

    def test_migration_history_mismatch_is_unsafe(self):
        initialize(self.path, self.anchor)
        with closing(sqlite3.connect(self.path)) as database, database:
            database.execute("UPDATE schema_migrations SET checksum = 'wrong'")
        with self.assertRaises(StateUnsafe):
            check(self.path, self.anchor)

    def test_orphaned_wal_is_unsafe(self):
        initialize(self.path, self.anchor)
        wal = self.path.with_name(self.path.name + "-wal")
        wal.write_bytes(b"unreconciled")
        with self.assertRaises(StateUnsafe):
            check(self.path, self.anchor)
        self.assertEqual(wal.read_bytes(), b"unreconciled")

    def test_restore_database_directory_does_not_restore_witness(self):
        old = initialize(self.path, self.anchor)
        previous = self.path.read_bytes()
        with state_write(self.path, self.anchor):
            pass
        self.assertEqual(check(self.path, self.anchor)["generation"], old["generation"] + 1)
        self.path.write_bytes(previous)
        with self.assertRaisesRegex(StateUnsafe, "generation"):
            check(self.path, self.anchor)

    def test_interrupted_write_reservation_blocks_state(self):
        initialize(self.path, self.anchor)
        with self.assertRaises(RuntimeError):
            with state_write(self.path, self.anchor):
                raise RuntimeError("injected transaction failure")
        with self.assertRaisesRegex(StateUnsafe, "generation"):
            check(self.path, self.anchor)

    def test_refuses_witness_in_database_tree(self):
        nested = self.path.parent / "inner"
        nested.mkdir(mode=0o700)
        with self.assertRaisesRegex(StateUnsafe, "separate directory trees"):
            initialize(self.path, nested / "manager.identity")
        self.assertFalse(self.path.exists())

    def test_process_death_after_generation_reservation_blocks_state(self):
        initialize(self.path, self.anchor)
        script = """
import os
from pathlib import Path
import sys
from backup_manager.state import state_write
with state_write(*(Path(item) for item in sys.argv[1:])):
    os._exit(31)
"""
        process = subprocess.run(
            [sys.executable, "-c", script, str(self.path), str(self.anchor)],
            check=False, timeout=10,
        )
        self.assertEqual(process.returncode, 31)
        self.assertEqual(json.loads(self.anchor.read_text())["generation"], 2)
        with self.assertRaises(StateUnsafe):
            check(self.path, self.anchor)

    def test_failed_initial_migration_leaves_no_admissible_instance(self):
        from unittest.mock import patch

        with patch("backup_manager.state.migration", return_value=("CREATE TABLE incomplete(x); INVALID SQL;", "broken")):
            with self.assertRaises(StateUnsafe):
                initialize(self.path, self.anchor)
        self.assertFalse(self.anchor.exists())
        with closing(sqlite3.connect(self.path)) as database:
            self.assertEqual(database.execute("SELECT name FROM sqlite_master").fetchall(), [])
        with self.assertRaises(StateUnsafe):
            check(self.path, self.anchor)

    def test_online_backup_contains_matching_identity_and_integrity_manifest(self):
        import hashlib
        from backup_manager.state import backup_state

        record = initialize(self.path, self.anchor)
        destination = self.path.parent / "copy.db"
        manifest = backup_state(self.path, self.anchor, destination)
        self.assertEqual(manifest["instance_id"], record["instance_id"])
        self.assertEqual(manifest["generation"], record["generation"])
        self.assertEqual(manifest["sha256"], hashlib.sha256(destination.read_bytes()).hexdigest())
        self.assertEqual(json.loads(destination.with_name("copy.db.manifest.json").read_text()), manifest)
        with closing(sqlite3.connect(destination)) as backup:
            self.assertEqual(backup.execute("SELECT generation FROM instance_state").fetchone(), (1,))
        with self.assertRaises(StateUnsafe):
            backup_state(self.path, self.anchor, destination)

    def test_upgrade_backs_up_old_schema_before_transaction(self):
        from unittest.mock import patch
        from backup_manager.state import MIGRATIONS, upgrade_state

        with patch("backup_manager.state.MIGRATIONS", MIGRATIONS[:1]):
            old = initialize(self.path, self.anchor)
        with self.assertRaisesRegex(StateUnsafe, "explicit upgrade"):
            check(self.path, self.anchor)
        destination = self.path.parent / "before.db"
        upgraded = upgrade_state(self.path, self.anchor, destination)
        self.assertEqual(upgraded["generation"], old["generation"] + 1)
        self.assertEqual(check(self.path, self.anchor), upgraded)
        with closing(sqlite3.connect(destination)) as previous:
            self.assertEqual(previous.execute("SELECT version FROM schema_migrations").fetchall(), [(1,)])

    def test_failed_upgrade_rolls_back_schema_and_preserves_backup(self):
        from unittest.mock import patch
        from backup_manager.state import MIGRATIONS, migration, upgrade_state

        with patch("backup_manager.state.MIGRATIONS", MIGRATIONS[:1]):
            initialize(self.path, self.anchor)
        destination = self.path.parent / "before.db"

        def broken(name):
            if name == MIGRATIONS[-1]:
                return "CREATE TABLE interrupted(x); INVALID SQL;", "broken"
            return migration(name)

        with patch("backup_manager.state.migration", side_effect=broken):
            with self.assertRaisesRegex(StateUnsafe, "upgrade failed"):
                upgrade_state(self.path, self.anchor, destination)
        with closing(sqlite3.connect(self.path)) as database:
            self.assertEqual(database.execute("SELECT version FROM schema_migrations").fetchall(), [(1,)])
            self.assertIsNone(database.execute("SELECT name FROM sqlite_master WHERE name = 'interrupted'").fetchone())
        self.assertTrue(destination.with_name("before.db.manifest.json").exists())
        with self.assertRaises(StateUnsafe):
            check(self.path, self.anchor)
