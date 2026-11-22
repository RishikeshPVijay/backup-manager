from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic_ns
import unittest
from unittest.mock import AsyncMock, patch

from backup_manager.claims import RepositoryClaim
from backup_manager.plugin_api import StorageIdentity, StorageInspection, StorageState
from backup_manager.state import StateUnsafe, initialize, state_read, state_write
from backup_manager.storage_coordinator import StorageCoordinator, StorageUnavailable


class StorageCoordinatorTests(unittest.TestCase):
    def test_repository_work_follows_verified_storage_and_releases_first(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            db, anchor = root / "db" / "state", root / "witness" / "identity"
            db.parent.mkdir(mode=0o700)
            anchor.parent.mkdir(mode=0o700)
            initialize(db, anchor)
            provider = AsyncMock()
            provider.inspect.return_value = StorageInspection(
                StorageState.AVAILABLE, True, str(root),
                StorageIdentity("fixture", "fixture", {}, True), True,
            )
            config = {"disk": {"provider": "fixture", "lifecycle": "always_on", "config": {}}}
            with patch("backup_manager.storage_coordinator.discover", return_value={"fixture": provider}):
                coordinator = StorageCoordinator(db, anchor, config, acquire_seconds=10, release_seconds=10)
                competitor = StorageCoordinator(db, anchor, config, acquire_seconds=10, release_seconds=10)
            deadline = monotonic_ns() + 5_000_000_000
            lease = coordinator.acquire("disk", deadline=deadline)
            with state_write(db, anchor) as database:
                database.execute("INSERT INTO repository_registrations (repository_key, job_id, destination_id, restic_repository_id, repo_generation, approved_at) VALUES ('repo', 'job', 'primary', 'repo-id', 1, 'now')")
            request = [RepositoryClaim("repo", 1, "COPY_DESTINATION")]
            self.assertIsNotNone(coordinator.reserve_repositories("op", request, [lease], deadline=deadline))
            competing_lease = competitor.acquire("disk", deadline=deadline)
            for cancelled in (True, False):
                with self.subTest(cancelled=cancelled):
                    # End the wait after its first admission attempt without real sleeps.
                    now = monotonic_ns()
                    end = now + 1_000_000_000
                    clock = [now]

                    def finish_wait(_seconds):
                        clock[0] = end - 1 if cancelled else end

                    with (
                        patch("backup_manager.storage_coordinator.monotonic_ns", side_effect=lambda: clock[0]),
                        patch("backup_manager.storage_coordinator.sleep", side_effect=finish_wait) as wait,
                        self.assertRaisesRegex(StorageUnavailable, "cancelled or timed out"),
                    ):
                        competitor.reserve_repositories(
                            "competing-op", request, [competing_lease], deadline=end,
                            cancelled=lambda: cancelled and clock[0] != now,
                        )
                    wait.assert_called_once()
                    with state_read(db, anchor) as database:
                        self.assertEqual(database.execute(
                            "SELECT operation_id FROM repository_claim_sets WHERE state = 'ACTIVE'",
                        ).fetchall(), [("op",)])
            with self.assertRaisesRegex(StateUnsafe, "Acquire storage"):
                coordinator.acquire("disk", deadline=deadline)
            with self.assertRaisesRegex(StateUnsafe, "Release repository claims"):
                coordinator.release(lease)
            coordinator.release_repositories("op")
            self.assertIsNotNone(competitor.reserve_repositories(
                "competing-op", request, [competing_lease], deadline=deadline,
            ))
            competitor.release_repositories("competing-op")
            competitor.release(competing_lease)
            coordinator.release(lease)
            with state_read(db, anchor) as database:
                self.assertEqual(database.execute("SELECT state FROM storage_claims").fetchall(),
                                 [("RELEASED",), ("RELEASED",)])

    def test_recovery_preserves_mounted_storage_and_drops_orphan_release_authority(self):
        import os
        from backup_manager.launch_journal import register_owner
        from backup_manager.process_identity import ProcessIdentity
        from backup_manager.storage_claims import reconcile_identity

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            db, anchor = root / "db" / "state", root / "witness" / "identity"
            db.parent.mkdir(mode=0o700)
            anchor.parent.mkdir(mode=0o700)
            initialize(db, anchor)
            provider = AsyncMock()
            identity = StorageIdentity("fixture", "fixture", {"mark": "owned"}, True)
            provider.inspect.return_value = StorageInspection(
                StorageState.AVAILABLE, True, str(root), identity, None,
            )
            config = {"disk": {"provider": "fixture", "lifecycle": "on_demand", "config": {}}}
            with patch("backup_manager.storage_coordinator.discover", return_value={"fixture": provider}):
                coordinator = StorageCoordinator(db, anchor, config, acquire_seconds=10, release_seconds=10)
            with state_write(db, anchor) as database:
                reconcile_identity(database, "disk", identity, None)
                orphan = register_owner(database, ProcessIdentity.capture(os.getpid()), "fixture-orphan")
                database.execute("UPDATE process_owners SET state = 'INTERRUPTED' WHERE owner_id = ?", (orphan,))
                database.execute(
                    """INSERT INTO storage_operations (operation_id, resource_id, kind, worker_identity, state)
                       VALUES ('old-release', 'disk', 'release', ?, 'RECOVERY_REQUIRED')""",
                    (orphan,),
                )
                database.execute("""INSERT INTO storage_ownership VALUES ('disk', 1, 1, '{"mark":"owned"}')""")
            result = coordinator.recover_storage("disk", deadline=monotonic_ns() + 5_000_000_000)
            self.assertEqual(result, {"resource_id": "disk", "mounted": True, "reconciled": 1})
            provider.release.assert_not_awaited()
            with state_read(db, anchor) as database:
                self.assertEqual(database.execute("SELECT owned_by_manager FROM storage_ownership").fetchone(), (0,))
