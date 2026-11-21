"""Exercise real kernel mount topology checks with disposable tmpfs mounts."""

import asyncio
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from backup_manager.linux_storage import LinuxFilesystem
from backup_manager.plugin_api import StorageState


async def probe():
    provider = LinuxFilesystem()
    with TemporaryDirectory(prefix="bm-mountinfo-") as temporary:
        target = Path(temporary) / "mount point"
        target.mkdir()
        config = {"mountpoint": str(target), "filesystem_uuid": "absent-test-fixture"}
        before = await provider.inspect(config, None)
        assert before.state is StorageState.UNAVAILABLE
        assert before.details["mount_count"] == "0"
        subprocess.run(["mount", "-t", "tmpfs", "manager-fixture", target], check=True)
        try:
            mounted = await provider.inspect(config, None)
            assert mounted.state is StorageState.UNCERTAIN
            assert mounted.mounted and mounted.details["mount_count"] == "1"
            child = target / "child"
            child.mkdir()
            subprocess.run(["mount", "-t", "tmpfs", "external-child", child], check=True)
            try:
                nested = await provider.inspect(config, None)
                assert nested.mounted and nested.details["mount_count"] == "2"
                print("Before, mounted, and nested topology checks passed.")
            finally:
                subprocess.run(["umount", child], check=True)
        finally:
            subprocess.run(["umount", target], check=True)
        after = await provider.inspect(config, None)
        assert after.state is StorageState.UNAVAILABLE
        assert after.details["mount_count"] == "0"
    print("Disposable mounts removed.")


def main():
    namespace = os.readlink("/proc/self/ns/mnt")
    if len(sys.argv) == 1:
        return subprocess.call([
            "unshare", "--user", "--map-root-user", "--mount", "--propagation", "private",
            sys.executable, __file__, namespace,
        ])
    if namespace == sys.argv[1]:
        raise RuntimeError("Expected a disposable mount namespace.")
    asyncio.run(probe())


if __name__ == "__main__":
    sys.exit(main())
