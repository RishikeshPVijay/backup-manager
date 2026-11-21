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

    def test_participants_share_acquisition_and_cancel_only_after_last_withdrawal(self):
        from backup_manager.storage_claims import drop_claim, lifecycle_view, plan_acquisition

        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "db" / "state"
            witness = root / "witness" / "state"
            db.parent.mkdir(mode=0o700)
            witness.parent.mkdir(mode=0o700)
            initialize(db, witness)
            with state_write(db, witness) as database:
                database.executemany(
                    "INSERT INTO process_owners (owner_id, boot_id, state) VALUES (?, 'boot', 'ACTIVE')",
                    [("first",), ("second",)],
                )
                resource = reconcile_identity(database, "disk", StorageIdentity("uuid", "uuid", {}, True), None)
                empty = lifecycle_view(database, resource.coordination_id)
                first = plan_acquisition(database, resource, "first", empty, available=False, on_demand=True)
                self.assertTrue(first.leader)
                self.assertIsNone(plan_acquisition(database, resource, "second", empty,
                                                   available=False, on_demand=True))
                observed = lifecycle_view(database, resource.coordination_id)
                second = plan_acquisition(database, resource, "second", observed,
                                          available=False, on_demand=True)
                self.assertFalse(second.leader)
                self.assertEqual(first.operation_id, second.operation_id)
                self.assertIsNone(drop_claim(database, first.claim_id, "first"))
                cancel = drop_claim(database, second.claim_id, "second")
                self.assertEqual((cancel.operation_id, cancel.kind), (first.operation_id, "cancel"))
                self.assertIsNone(plan_acquisition(database, resource, "second",
                                                   lifecycle_view(database, resource.coordination_id),
                                                   available=False, on_demand=True))

    def test_release_waits_for_final_claim_and_keeps_failed_evidence(self):
        from backup_manager.launch_journal import LaunchReceipt, record_intent, record_launched, record_terminated, register_owner
        from backup_manager.process_identity import ProcessIdentity
        from backup_manager.storage_claims import complete_release, drop_claim, lifecycle_view, plan_acquisition, plan_release
        from backup_manager.supervisor import ScopedResult
        import os

        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "db" / "state"
            witness = root / "witness" / "state"
            db.parent.mkdir(mode=0o700)
            witness.parent.mkdir(mode=0o700)
            initialize(db, witness)
            with state_write(db, witness) as database:
                disk = reconcile_identity(database, "disk", StorageIdentity("uuid", "uuid", {}, True), None)
                identity = ProcessIdentity.capture(os.getpid())
                first = register_owner(database, identity, "fixture")
                second = register_owner(database, identity, "fixture")
                database.execute("""INSERT INTO storage_ownership VALUES ('disk', 1, 1, '{"mount":"fixture"}')""")
                one = admit_storage_claim(database, disk, first)
                two = admit_storage_claim(database, disk, second)
                drop_claim(database, one, first)
                self.assertIsNone(plan_release(database, "uuid", first))
                drop_claim(database, two, second)
                plan, evidence = plan_release(database, "uuid", second)
                self.assertEqual(evidence, {"mount": "fixture"})
                self.assertIsNone(plan_acquisition(database, disk, first, lifecycle_view(database, "uuid"),
                                                   available=True, on_demand=True))
                intent = record_intent(database, plan.operation_id, second)
                record_launched(database, intent, LaunchReceipt("fixture", "scope", identity))
                record_terminated(database, intent, ScopedResult(1, b"", b"", False, "scope"))
                self.assertFalse(complete_release(database, plan.operation_id, physical_release_confirmed=False))
                self.assertEqual(database.execute("SELECT owned_by_manager FROM storage_ownership").fetchone(), (1,))
                self.assertEqual(lifecycle_view(database, "uuid")[0][2], "RECOVERY_REQUIRED")

    def test_active_repository_claim_prevents_storage_relinquishment(self):
        from backup_manager.storage_claims import drop_claim

        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "db" / "state"
            witness = root / "witness" / "state"
            db.parent.mkdir(mode=0o700)
            witness.parent.mkdir(mode=0o700)
            initialize(db, witness)
            with state_write(db, witness) as database:
                disk = reconcile_identity(database, "disk", StorageIdentity("uuid", "uuid", {}, True), None)
                database.execute("INSERT INTO process_owners (owner_id, boot_id, state) VALUES ('owner', 'boot', 'ACTIVE')")
                claim = admit_storage_claim(database, disk, "owner")
                database.execute("INSERT INTO repository_claim_sets VALUES ('set', 'op', 'owner', 'ACTIVE')")
                with self.assertRaisesRegex(StateUnsafe, "Release repository claims"):
                    drop_claim(database, claim, "owner")
                self.assertEqual(database.execute("SELECT state FROM storage_claims").fetchone(), ("ACTIVE",))
