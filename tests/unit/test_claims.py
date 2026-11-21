from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backup_manager.claims import RepositoryClaim, admit_repository_claims
from backup_manager.state import initialize, state_write


class RepositoryClaimTests(unittest.TestCase):
    def test_conflict_does_not_admit_part_of_claim_set(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = root / "database" / "state"
            anchor = root / "witness" / "state"
            db.parent.mkdir(mode=0o700)
            anchor.parent.mkdir(mode=0o700)
            initialize(db, anchor)
            with state_write(db, anchor) as database:
                database.execute("INSERT INTO process_owners (owner_id, boot_id, state) VALUES ('worker', 'boot', 'ACTIVE')")
                database.executemany(
                    "INSERT INTO repository_registrations VALUES (?, 'job', ?, ?, 1, 'time')",
                    [("a", "a", "id-a"), ("b", "b", "id-b")],
                )
                first = admit_repository_claims(database, "op1", "worker", [
                    RepositoryClaim("b", 1, "COPY_DESTINATION"),
                ])
                self.assertIsNotNone(first)
                second = admit_repository_claims(database, "op2", "worker", [
                    RepositoryClaim("a", 1, "COPY_SOURCE"),
                    RepositoryClaim("b", 1, "COPY_DESTINATION"),
                ], {frozenset(("COPY_DESTINATION",))})
                self.assertIsNone(second)
                rows = database.execute("SELECT repository_key FROM repository_claims").fetchall()
                self.assertEqual(rows, [("b",)])

    def test_repository_claims_cannot_relinquish_until_execution_terminates(self):
        import os
        from backup_manager.claims import relinquish_repository_claims
        from backup_manager.launch_journal import LaunchReceipt, record_intent, record_launched, record_terminated, register_owner
        from backup_manager.process_identity import ProcessIdentity
        from backup_manager.state import StateUnsafe
        from backup_manager.supervisor import ScopedResult

        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, anchor = root / "db" / "state", root / "witness" / "state"
            db.parent.mkdir(mode=0o700)
            anchor.parent.mkdir(mode=0o700)
            initialize(db, anchor)
            with state_write(db, anchor) as database:
                identity = ProcessIdentity.capture(os.getpid())
                owner = register_owner(database, identity, "fixture")
                database.execute("INSERT INTO repository_registrations VALUES ('a', 'job', 'a', 'id-a', 1, 'time')")
                admit_repository_claims(database, "op", owner, [RepositoryClaim("a", 1, "COPY_DESTINATION")])
                intent = record_intent(database, "op", owner)
                with self.assertRaisesRegex(StateUnsafe, "unconfirmed"):
                    relinquish_repository_claims(database, "op", owner)
                record_launched(database, intent, LaunchReceipt("fixture", "scope", identity))
                record_terminated(database, intent, ScopedResult(0, b"", b"", False, "scope"))
                self.assertTrue(relinquish_repository_claims(database, "op", owner))
