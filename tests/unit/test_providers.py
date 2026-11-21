import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backup_manager.plugin_api import SecretRef, StorageState
from backup_manager.providers import ProviderError, discover
from backup_manager.secrets import SecretUnavailable


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_secret_file_permissions_symlink_and_fifo(self):
        provider = discover("secrets")["file"]
        with TemporaryDirectory() as root:
            target = Path(root) / "secret"
            target.touch(mode=0o600)
            target.write_bytes(b"fixture-secret")
            reference = SecretRef("file", {"path": str(target)})
            self.assertEqual(await provider.resolve(reference, None), b"fixture-secret")
            link = Path(root) / "link"
            link.symlink_to(target)
            with self.assertRaises(SecretUnavailable):
                await provider.resolve(SecretRef("file", {"path": str(link)}), None)
            target.chmod(0o644)
            with self.assertRaises(SecretUnavailable):
                await provider.resolve(reference, None)
            fifo = Path(root) / "fifo"
            os.mkfifo(fifo, mode=0o600)
            with self.assertRaises(SecretUnavailable):
                await provider.resolve(SecretRef("file", {"path": str(fifo)}), None)

    async def test_environment_secret_unavailable(self):
        provider = discover("secrets")["environment"]
        reference = SecretRef("environment", {"variable": "BM_TEST_SENTINEL"})
        with patch.dict("os.environ", clear=True):
            with self.assertRaises(SecretUnavailable):
                await provider.resolve(reference, None)
            with patch.dict("os.environ", {"BM_TEST_SENTINEL": "fixture-secret"}):
                self.assertEqual(await provider.resolve(reference, None), b"fixture-secret")

    async def test_linux_filesystem_rejects_unmounted_directory(self):
        provider = discover("storage")["linux_filesystem"]
        with TemporaryDirectory() as root:
            result = await provider.inspect({"mountpoint": root, "filesystem_uuid": "missing"}, None)
            self.assertEqual(result.state, StorageState.UNAVAILABLE)
            self.assertFalse(result.identity.verified_physical)

    def test_duplicate_provider_rejected(self):
        provider = type(discover("secrets")["file"])
        with self.assertRaises(ProviderError):
            discover("secrets", (provider,))
