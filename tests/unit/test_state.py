from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from backup_manager.state import StateUnsafe, anchor_path, check, initialize


class StateTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "manager.sqlite3"

    def test_missing_state_does_not_create_database_or_anchor(self):
        with self.assertRaises(StateUnsafe):
            check(self.path)
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_initialize_migrates_once_and_check_is_read_only(self):
        record = initialize(self.path)
        before = {p.name: p.read_bytes() for p in self.path.parent.iterdir()}
        self.assertEqual(check(self.path), record)
        with self.assertRaises(StateUnsafe):
            initialize(self.path)
        self.assertEqual(
            {p.name: p.read_bytes() for p in self.path.parent.iterdir()}, before,
        )
        with closing(sqlite3.connect(self.path)) as database:
            self.assertEqual(database.execute("PRAGMA journal_mode").fetchone(), ("wal",))
            self.assertEqual(database.execute("SELECT version FROM schema_migrations").fetchall(), [(1,)])

    def test_identity_and_generation_mismatch_is_unsafe(self):
        record = initialize(self.path)
        record["generation"] = 2
        anchor_path(self.path).write_text(json.dumps(record))
        with self.assertRaises(StateUnsafe):
            check(self.path)

    def test_corruption_is_unsafe(self):
        initialize(self.path)
        self.path.write_bytes(b"unreadable database")
        with self.assertRaises(StateUnsafe):
            check(self.path)

    def test_migration_history_mismatch_is_unsafe(self):
        initialize(self.path)
        with closing(sqlite3.connect(self.path)) as database, database:
            database.execute("UPDATE schema_migrations SET checksum = 'wrong'")
        with self.assertRaises(StateUnsafe):
            check(self.path)

    def test_orphaned_wal_is_unsafe(self):
        initialize(self.path)
        wal = self.path.with_name(self.path.name + "-wal")
        wal.write_bytes(b"unreconciled")
        with self.assertRaises(StateUnsafe):
            check(self.path)
        self.assertEqual(wal.read_bytes(), b"unreconciled")
