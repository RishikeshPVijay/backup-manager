from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from backup_manager.crash_recovery import observe_owner, reconcile_owner
from backup_manager.launch_journal import record_intent, register_owner
from backup_manager.process_identity import Presence, ProcessIdentity
from backup_manager.state import initialize, state_write


class CrashRecoveryTests(unittest.TestCase):
    def test_dead_owner_with_unresolved_intent_still_holds_claim(self):
        self.check_dead_owner(intent=True, expected="RECOVERY_REQUIRED")

    def test_dead_owner_without_launch_intent_can_relinquish_claim(self):
        self.check_dead_owner(intent=False, expected="RELEASED")

    def check_dead_owner(self, *, intent, expected):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "db" / "state"
            anchor = root / "anchor" / "state"
            db.parent.mkdir(mode=0o700)
            anchor.parent.mkdir(mode=0o700)
            initialize(db, anchor)
            with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"]) as child:
                with state_write(db, anchor) as database:
                    owner = register_owner(database, ProcessIdentity.capture(child.pid), "fixture")
                    database.execute("INSERT INTO storage_resources (resource_id, coordination_id) VALUES ('disk', 'disk')")
                    database.execute("INSERT INTO storage_claims VALUES ('claim', 'disk', ?, 1, 'ACTIVE')", (owner,))
                    database.execute("INSERT INTO storage_ownership VALUES ('disk', 1, 1, '{}')")
                    if intent:
                        record_intent(database, "operation", owner)
                    stored = database.execute("SELECT process_start_identity FROM process_owners").fetchone()[0]
                child.kill()
                child.wait(timeout=5)
                observation = observe_owner(owner, stored)
                self.assertIs(observation.presence, Presence.GONE)
            with state_write(db, anchor) as database:
                self.assertEqual(reconcile_owner(database, observation), not intent)
                self.assertEqual(database.execute("SELECT state FROM storage_claims").fetchone(), (expected,))
                self.assertEqual(database.execute("SELECT owned_by_manager FROM storage_ownership").fetchone(), (1,))
