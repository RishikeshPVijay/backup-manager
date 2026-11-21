from pathlib import Path
import stat
from time import monotonic_ns
import unittest
from unittest.mock import AsyncMock, patch

from backup_manager.linux_storage import LifecycleUncertain, LinuxFilesystem
from backup_manager.plugin_api import CommandResult, ExecutionContext, StorageIdentity, StorageInspection, StorageState


class LinuxLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = LinuxFilesystem()
        self.config = {"filesystem_uuid": "fixture-device", "mountpoint": "/disposable/fixture"}
        self.context = ExecutionContext("test", monotonic_ns() + 5_000_000_000, "test", "test")
        self.commands = AsyncMock()
        self.commands.run.return_value = CommandResult(0, b"", b"", True)
        self.identity = StorageIdentity("fixture", "fixture", {"mount_id": "20"}, True)

    def observation(self, mounted, count):
        return StorageInspection(
            StorageState.AVAILABLE if mounted else StorageState.UNAVAILABLE,
            mounted, self.config["mountpoint"], self.identity, None,
            {"mount_count": str(count)},
        )

    async def test_existing_mount_reused_without_ownership(self):
        with patch.object(self.provider, "inspect", return_value=self.observation(True, 1)):
            result = await self.provider.acquire(self.config, self.context, self.commands)
        self.assertEqual(result.ownership_evidence, {})
        self.assertFalse(result.changed_physical_state)
        self.commands.run.assert_not_called()

    async def test_acquire_and_release_verify_physical_state(self):
        unmounted = self.observation(False, 0)
        mounted = self.observation(True, 1)
        with (
            patch.object(self.provider, "inspect", side_effect=[unmounted, mounted, mounted, unmounted]),
            patch.object(Path, "stat") as device_stat,
        ):
            device_stat.return_value.st_mode = stat.S_IFBLK
            acquired = await self.provider.acquire(self.config, self.context, self.commands)
            released = await self.provider.release(
                self.config, self.context, self.commands, acquired.ownership_evidence,
            )
        self.assertEqual(acquired.ownership_evidence, self.identity.evidence)
        self.assertFalse(released.inspection.mounted)
        self.assertEqual([call.args[0][0] for call in self.commands.run.call_args_list],
                         ["/usr/bin/mount", "/usr/bin/umount"])

    async def test_release_rejects_nested_mount_and_stale_ownership(self):
        for count, evidence in ((2, self.identity.evidence), (1, {}), (1, {"mount_id": "old"})):
            with (
                self.subTest(count=count, evidence=evidence),
                patch.object(self.provider, "inspect", return_value=self.observation(True, count)),
                self.assertRaises(LifecycleUncertain),
            ):
                await self.provider.release(self.config, self.context, self.commands, evidence)
        self.commands.run.assert_not_called()

    async def test_unconfirmed_termination_does_not_report_release(self):
        self.commands.run.return_value = CommandResult(0, b"", b"", False)
        with (
            patch.object(self.provider, "inspect", return_value=self.observation(True, 1)) as inspect,
            self.assertRaises(LifecycleUncertain),
        ):
            await self.provider.release(
                self.config, self.context, self.commands, self.identity.evidence,
            )
        inspect.assert_awaited_once()
        self.commands.run.assert_awaited_once()
