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
