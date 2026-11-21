import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

from backup_manager.convergence import transfer
from backup_manager.job_history import admit
from backup_manager.launch_journal import LaunchReceipt, fence_uncertain, record_intent, record_launched, register_owner
from backup_manager.process_identity import ProcessIdentity
from backup_manager.convergence import NativeSnapshot
from backup_manager.state import initialize, state_write
from backup_manager.transfer_state import blocked_requirements, finish_attempt, plan_attempt, record_inventory


class TransferStateTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.path = root / "state" / "db"
        self.anchor = root / "anchor" / "identity"
        self.path.parent.mkdir(mode=0o700)
        self.anchor.parent.mkdir(mode=0o700)
        initialize(self.path, self.anchor)
        self.snapshot = NativeSnapshot("1" * 64, None, "2" * 64, "2025-01-01T00:00:00+00:00", "host", ("/data",), ())
        self.a = SimpleNamespace(key="a", generation=1)
        self.b = SimpleNamespace(key="b", generation=1)

    def test_partial_enumeration_preserves_provenance_and_started_failure_blocks(self):
        with state_write(self.path, self.anchor) as database:
            identity = ProcessIdentity.capture(os.getpid())
            owner = register_owner(database, identity, "profile-test")
            run, _ = admit(database, owner, "job", {}, 1)
            database.executemany(
                """INSERT INTO repository_registrations
                   (repository_key, job_id, destination_id, restic_repository_id, repo_generation, approved_at)
                   VALUES (?, 'job', ?, ?, 1, 'time')""",
                [(name, name, f"repo-{name}") for name in "ab"],
            )
            record_inventory(database, self.a, "profile", (self.snapshot,))
            record_inventory(database, self.a, "profile", None)
            self.assertEqual(database.execute("SELECT count(*) FROM snapshot_provenance").fetchone(), (1,))
            plan = transfer("a", "b", self.b, (self.snapshot,))
            first = plan_attempt(database, run, "replication", plan, self.a, self.b)
            finish_attempt(database, first, "PRELAUNCH_FAILED", code="FIXTURE")
            self.assertEqual(blocked_requirements(database, run), set())
            second = plan_attempt(database, run, "convergence", plan, self.a, self.b)
            intent = record_intent(database, second, owner)
            record_launched(database, intent, LaunchReceipt("profile-test", "fixture-scope", identity))
            fence_uncertain(database, intent)
            self.assertEqual(blocked_requirements(database, run), {plan.requirements[0].id})
            self.assertEqual(database.execute(
                "SELECT state, launched FROM copy_attempts ORDER BY rowid"
            ).fetchall(), [("PRELAUNCH_FAILED", 0), ("LAUNCH_UNCERTAIN", 1)])
