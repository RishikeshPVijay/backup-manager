from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backup_manager.plugin_api import StorageIdentity
from backup_manager.state import StateUnsafe, initialize, state_write
from backup_manager.storage_claims import admit_storage_claim, reconcile_identity


class StorageClaimsTests(unittest.TestCase):
    def test_verified_alias_and_changed_identity_require_recovery(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "db" / "state"
            witness = root / "witness" / "state"
            db.parent.mkdir(mode=0o700)
            witness.parent.mkdir(mode=0o700)
            initialize(db, witness)
            with state_write(db, witness) as database:
                identity = StorageIdentity("uuid1", "uuid1", {}, True)
                registered = reconcile_identity(database, "disk", identity, None)
                self.assertEqual(reconcile_identity(database, "disk", identity, None), registered)
                with self.assertRaises(StateUnsafe):
                    reconcile_identity(database, "alias", identity, None)
                changed = StorageIdentity("uuid2", "uuid2", {}, True)
                with self.assertRaises(StateUnsafe):
                    reconcile_identity(database, "disk", changed, None)

    def test_equal_explicit_identities_share_release_fence(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "db" / "state"
            witness = root / "witness" / "state"
            db.parent.mkdir(mode=0o700)
            witness.parent.mkdir(mode=0o700)
            initialize(db, witness)
            with state_write(db, witness) as database:
                unverified = StorageIdentity("unverified", None, {}, False)
                with self.assertRaises(StateUnsafe):
                    reconcile_identity(database, "a", unverified, None)
                a = reconcile_identity(database, "a", unverified, "shared")
                b = reconcile_identity(database, "b", unverified, "shared")
                database.execute("INSERT INTO process_owners (owner_id, boot_id, state) VALUES ('owner', 'boot', 'ACTIVE')")
                database.execute(
                    "INSERT INTO storage_operations (operation_id, resource_id, kind, state) VALUES ('op', 'a', 'release', 'RELEASING')",
                )
                self.assertIsNone(admit_storage_claim(database, b, "owner"))
                database.execute("UPDATE storage_operations SET state = 'TERMINATED' WHERE operation_id = 'op'")
                self.assertIsNotNone(admit_storage_claim(database, a, "owner"))
                self.assertIsNotNone(admit_storage_claim(database, b, "owner"))
