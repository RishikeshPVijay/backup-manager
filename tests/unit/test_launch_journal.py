import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from backup_manager.claims import RepositoryClaim, admit_repository_claims
from backup_manager.launch_journal import LaunchReceipt, fence_uncertain, record_intent, record_launched, record_terminated, register_owner
from backup_manager.process_identity import ProcessIdentity
from backup_manager.supervisor import ScopedResult
from backup_manager.state import StateUnsafe, initialize, state_write


class LaunchJournalTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.db = root / "database" / "state.sqlite3"
        self.anchor = root / "witness" / "identity"
        self.db.parent.mkdir(mode=0o700)
        self.anchor.parent.mkdir(mode=0o700)
        initialize(self.db, self.anchor)
        self.identity = ProcessIdentity.capture(os.getpid())

    def write(self):
        return state_write(self.db, self.anchor)

    def test_durable_intent_survives_owner_death_without_receipt(self):
        script = """
import os
from pathlib import Path
import sys
from backup_manager.launch_journal import record_intent, register_owner
from backup_manager.process_identity import ProcessIdentity
from backup_manager.supervisor import ScopedResult
from backup_manager.state import state_write
with state_write(*(Path(item) for item in sys.argv[1:])) as database:
    owner = register_owner(database, ProcessIdentity.capture(os.getpid()), "fixture-supervisor")
    record_intent(database, "fixture-op", owner)
os._exit(32)
"""
        child = subprocess.run([sys.executable, "-c", script, str(self.db), str(self.anchor)],
                               check=False, timeout=10)
        self.assertEqual(child.returncode, 32)
        with self.write() as database:
            rows = database.execute("SELECT state, launched_at FROM command_executions").fetchall()
            self.assertEqual(rows, [("LAUNCH_INTENT", None)])

    def test_receipt_requires_matching_supervisor(self):
        with self.write() as database:
            owner = register_owner(database, self.identity, "supervisor")
            intent = record_intent(database, "op", owner)
        with self.write() as database:
            with self.assertRaises(StateUnsafe):
                record_launched(database, intent, LaunchReceipt("other", "fixture-scope", self.identity))
            record_launched(database, intent, LaunchReceipt("supervisor", "fixture-scope", self.identity))
            self.assertEqual(database.execute("SELECT state FROM command_executions").fetchone(), ("LAUNCHED",))
            self.assertEqual(database.execute("SELECT type FROM events ORDER BY rowid").fetchall(),
                             [("launch_intent",), ("launched",)])

    def test_uncertain_execution_keeps_repository_and_storage_claims(self):
        with self.write() as database:
            owner = register_owner(database, self.identity, "supervisor")
            other = register_owner(database, self.identity, "other")
            database.execute("INSERT INTO storage_resources (resource_id, coordination_id) VALUES ('disk', 'disk')")
            database.execute("INSERT INTO storage_claims VALUES ('claim', 'disk', ?, 1, 'ACTIVE')", (owner,))
            database.execute("INSERT INTO repository_registrations (repository_key, job_id, destination_id, restic_repository_id, repo_generation, approved_at) VALUES ('repo', 'job', 'primary', 'id', 1, 'now')")
            request = [RepositoryClaim("repo", 1, "COPY_DESTINATION")]
            self.assertIsNotNone(admit_repository_claims(database, "copy", owner, request))
            intent = record_intent(database, "copy", owner)
            fence_uncertain(database, intent)
            fence_uncertain(database, intent)
            self.assertIsNone(admit_repository_claims(database, "new-copy", other, request))
            self.assertEqual(database.execute("SELECT state FROM storage_claims").fetchone(),
                             ("RECOVERY_REQUIRED",))
            self.assertEqual(database.execute("SELECT state FROM command_executions").fetchone(),
                             ("LAUNCH_UNCERTAIN",))
            self.assertEqual(database.execute("SELECT count(*) FROM events WHERE type = 'launch_uncertain'").fetchone(),
                             (1,))

    def test_missing_receipt_blocks_otherwise_compatible_repository_claim(self):
        with self.write() as database:
            owner = register_owner(database, self.identity, "supervisor")
            other = register_owner(database, self.identity, "other")
            database.execute("INSERT INTO repository_registrations (repository_key, job_id, destination_id, restic_repository_id, repo_generation, approved_at) VALUES ('repo', 'job', 'primary', 'id', 1, 'now')")
            request = [RepositoryClaim("repo", 1, "COPY_SOURCE")]
            compatible = {frozenset(("COPY_SOURCE",))}
            self.assertIsNotNone(admit_repository_claims(database, "op", owner, request, compatible))
            intent = record_intent(database, "op", owner)
            self.assertIsNone(admit_repository_claims(database, "op2", other, request, compatible))
            record_launched(database, intent, LaunchReceipt("supervisor", "fixture-scope", self.identity))
            self.assertIsNotNone(admit_repository_claims(database, "op2", other, request, compatible))

    def test_termination_must_match_scope_and_cannot_be_rewritten(self):
        with self.write() as database:
            owner = register_owner(database, self.identity, "supervisor")
            intent = record_intent(database, "op", owner)
            record_launched(database, intent, LaunchReceipt("supervisor", "fixture-scope", self.identity))
            with self.assertRaises(StateUnsafe):
                record_terminated(database, intent, ScopedResult(0, b"", b"", False, "other-scope"))
            result = ScopedResult(1, b"", b"", False, "fixture-scope")
            record_terminated(database, intent, result)
            with self.assertRaises(StateUnsafe):
                record_terminated(database, intent, result)
            self.assertEqual(
                database.execute("SELECT state, exit_code FROM command_executions").fetchone(),
                ("TERMINATED", 1),
            )
