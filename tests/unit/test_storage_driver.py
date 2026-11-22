from dataclasses import asdict
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from backup_manager.launch_journal import LaunchReceipt, record_intent, record_launched, record_terminated, register_owner
from backup_manager.plugin_api import StorageIdentity, StorageInspection, StorageState
from backup_manager.process_identity import ProcessIdentity
from backup_manager.state import StateUnsafe, initialize, state_read, state_write
from backup_manager.storage_claims import drop_claim, lifecycle_view, plan_acquisition, plan_release, reconcile_identity
from backup_manager.storage_driver import drive_lifecycle
from backup_manager.supervisor import ScopedResult, SupervisionUncertain


class StorageDriverTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.path = root / "state" / "db"
        self.anchor = root / "witness" / "identity"
        self.path.parent.mkdir(mode=0o700)
        self.anchor.parent.mkdir(mode=0o700)
        initialize(self.path, self.anchor)
        self.process = ProcessIdentity.capture(os.getpid())
        self.identity = StorageIdentity("disk-uuid", "disk-uuid", {"mount_id": "123"}, True)
        self.mounted = StorageInspection(StorageState.AVAILABLE, True, "/fixture", self.identity, None, {"mount_count": "1"})
        self.absent = StorageInspection(StorageState.UNAVAILABLE, False, "/fixture", self.identity, None, {"mount_count": "0"})
        self.provider = AsyncMock()
        self.provider.provider_id = "linux_filesystem"
        with state_write(self.path, self.anchor) as database:
            self.owner = register_owner(database, self.process, "supervisor")
            disk = reconcile_identity(database, "disk", self.identity, None)
            self.plan = plan_acquisition(database, disk, self.owner, (), available=False, on_demand=True)

    def drive(self, result, observation, *, cancel=False):
        with state_write(self.path, self.anchor) as database:
            if cancel:
                drop_claim(database, self.plan.claim_id, self.owner)
            intent = record_intent(database, self.plan.operation_id, self.owner)
            record_launched(database, intent, LaunchReceipt("supervisor", "scope", self.process))
            record_terminated(database, intent, result)
        self.provider.inspect.return_value = observation
        with patch("backup_manager.storage_driver.execute_lifecycle", return_value=result):
            return drive_lifecycle(self.path, self.anchor, self.plan, self.owner, self.provider, {}, None)

    def result(self):
        output = json.dumps({
            "changed_physical_state": True,
            "ownership_evidence": self.identity.evidence,
            "inspection": asdict(self.mounted),
        }).encode()
        return ScopedResult(0, output, b"", False, "scope")

    def test_verified_acquisition_persists_generation_and_ownership_receipt(self):
        self.assertEqual(self.drive(self.result(), self.mounted), (True, None))
        with state_read(self.path, self.anchor) as database:
            generation, owned, evidence = database.execute(
                "SELECT generation, owned_by_manager, evidence_json FROM storage_ownership",
            ).fetchone()
            self.assertEqual((generation, owned, json.loads(evidence)), (1, 1, self.identity.evidence))
            self.assertEqual(database.execute("SELECT state FROM storage_claims").fetchone(), ("ACTIVE",))

    def test_unconfirmed_lifecycle_fences_another_acquisition(self):
        from backup_manager.storage_claims import admit_storage_claim

        with patch("backup_manager.storage_driver.execute_lifecycle", side_effect=SupervisionUncertain):
            with self.assertRaises(SupervisionUncertain):
                drive_lifecycle(self.path, self.anchor, self.plan, self.owner, self.provider, {}, None)
        self.provider.inspect.assert_not_awaited()
        with state_write(self.path, self.anchor) as database:
            disk = reconcile_identity(database, "disk", self.identity, None)
            competitor = register_owner(database, self.process, "competitor")
            self.assertEqual(lifecycle_view(database, "disk-uuid")[0][2], "RECOVERY_REQUIRED")
            self.assertIsNone(admit_storage_claim(database, disk, competitor))

    def test_fresh_physical_inspection_is_required(self):
        with self.assertRaisesRegex(StateUnsafe, "fresh physical evidence"):
            self.drive(self.result(), self.absent)
        with state_read(self.path, self.anchor) as database:
            self.assertEqual(lifecycle_view(database, "disk-uuid")[0][2], "RECOVERY_REQUIRED")
            self.assertEqual(database.execute("SELECT count(*) FROM storage_ownership").fetchone(), (0,))

    def reserve_release(self):
        self.assertEqual(self.drive(self.result(), self.mounted), (True, None))
        with state_write(self.path, self.anchor) as database:
            drop_claim(database, self.plan.claim_id, self.owner)
            return plan_release(database, "disk-uuid", self.owner)

    def drive_release(self, release, evidence, result, observation):
        with state_write(self.path, self.anchor) as database:
            intent = record_intent(database, release.operation_id, self.owner)
            record_launched(database, intent, LaunchReceipt("supervisor", "release-scope", self.process))
            record_terminated(database, intent, result)
        self.provider.inspect.reset_mock()
        self.provider.inspect.return_value = observation
        with patch("backup_manager.storage_driver.execute_lifecycle", return_value=result) as worker:
            outcome = drive_lifecycle(
                self.path, self.anchor, release, self.owner, self.provider, {}, None,
                ownership_evidence=evidence,
            )
            worker.assert_called_once()
        self.provider.inspect.assert_awaited_once_with({}, None)
        return outcome

    def test_release_timeout_keeps_ownership_and_refuses_another_release(self):
        release, evidence = self.reserve_release()
        result = ScopedResult(None, b"", b"", False, "release-scope", stop_reason="timeout")
        self.assertEqual(self.drive_release(release, evidence, result, self.absent), (False, None))
        with state_write(self.path, self.anchor) as database:
            self.assertEqual(lifecycle_view(database, "disk-uuid")[-1][2], "RECOVERY_REQUIRED")
            self.assertEqual(database.execute("SELECT owned_by_manager FROM storage_ownership").fetchone(), (1,))
            self.assertIsNone(plan_release(database, "disk-uuid", self.owner))

    def test_acquisition_after_release_requires_fresh_lifecycle_observation(self):
        release, evidence = self.reserve_release()
        with state_write(self.path, self.anchor) as database:
            disk = reconcile_identity(database, "disk", self.identity, None)
            competing_owner = register_owner(database, self.process, "competitor")
            stale = lifecycle_view(database, "disk-uuid")
            self.assertIsNone(plan_acquisition(
                database, disk, competing_owner, stale, available=True, on_demand=True,
            ))

        output = json.dumps({
            "changed_physical_state": True,
            "ownership_evidence": {},
            "inspection": asdict(self.absent),
        }).encode()
        result = ScopedResult(0, output, b"", False, "release-scope")
        self.assertEqual(self.drive_release(release, evidence, result, self.absent), (True, None))
        with state_write(self.path, self.anchor) as database:
            self.assertIsNone(plan_acquisition(
                database, disk, competing_owner, stale, available=True, on_demand=True,
            ))
            fresh = lifecycle_view(database, "disk-uuid")
            plan = plan_acquisition(database, disk, competing_owner, fresh, available=False, on_demand=True)
            self.assertEqual((plan.kind, plan.leader), ("acquire", True))
            self.assertNotEqual(plan.operation_id, release.operation_id)

    def test_canceled_acquisition_with_empty_scope_and_no_mount_clears_fence(self):
        self.assertEqual(self.drive(ScopedResult(None, b"", b"", False, "scope"), self.absent, cancel=True),
                         (False, None))
        with state_read(self.path, self.anchor) as database:
            self.assertEqual(lifecycle_view(database, "disk-uuid")[0][2], "TERMINATED")
            self.assertEqual(database.execute("SELECT state FROM storage_claims").fetchone(), ("RELEASED",))

    def test_canceled_acquisition_that_mounted_reserves_cleanup(self):
        acquired, cleanup = self.drive(self.result(), self.mounted, cancel=True)
        self.assertFalse(acquired)
        self.assertEqual(cleanup[0].kind, "release")
        self.assertEqual(cleanup[1], self.identity.evidence)
        with state_read(self.path, self.anchor) as database:
            self.assertEqual(lifecycle_view(database, "disk-uuid")[-1][2], "RELEASING")
