import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from backup_manager.job_repository import inspect_repository
from backup_manager.plugin_api import StorageIdentity, StorageInspection, StorageState
from backup_manager.scope_worker import repository_argv
from backup_manager.state import StateUnsafe
from backup_manager.storage_coordinator import StorageLease


class RepositoryBoundaryTests(unittest.TestCase):
    def test_path_escape_and_replacement_are_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            mount = root / "mount"
            mount.mkdir()
            repo = mount / "repo"
            repo.mkdir()
            outside = root / "outside"
            outside.mkdir()
            link = mount / "link"
            link.symlink_to(outside, target_is_directory=True)
            identity = StorageIdentity("disk", "disk", {}, True)
            coordinator = Mock()
            coordinator.observe.return_value = (
                StorageInspection(StorageState.AVAILABLE, True, str(mount), identity, True), identity,
            )
            lease = StorageLease("claim", "disk", "disk")
            with self.assertRaises(StateUnsafe):
                inspect_repository(coordinator, lease, link, None)
            binding = inspect_repository(coordinator, lease, repo, None)
            argv = ("/usr/bin/restic", "--repo", str(repo))
            with repository_argv(argv, binding) as (prepared, descriptors):
                self.assertEqual(os.fstat(descriptors[0]).st_ino, repo.stat().st_ino)
                self.assertEqual(prepared[-1], f"/proc/self/fd/{descriptors[0]}")
            repo.rename(mount / "original")
            repo.mkdir()
            with self.assertRaisesRegex(ValueError, "changed before launch"):
                with repository_argv(argv, binding):
                    self.fail("Replaced repository reached process launch.")
