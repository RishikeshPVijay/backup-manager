"""Test the Linux provider on a disposable loop filesystem in a private container.

The runner needs mount and loop-device access. It creates only its own image and
device nodes, and verifies the backing file before detaching the loop device.
"""

import asyncio
import fcntl
import os
from pathlib import Path
import stat
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic_ns
from uuid import uuid4

from backup_manager.linux_storage import LifecycleUncertain, LinuxFilesystem
from backup_manager.plugin_api import CommandResult, ExecutionContext, StorageState

LOOP_CTL_GET_FREE = 0x4C82


class FixtureCommands:
    async def run(self, argv, *, context, env=None, cwd=None):
        result = subprocess.run(argv, capture_output=True, env=env, cwd=cwd, timeout=10)
        if result.returncode:
            print(result.stderr.decode(errors="replace"), end="", flush=True)
        return CommandResult(result.returncode, result.stdout, result.stderr, True)


def run(*argv):
    return subprocess.run(argv, check=True, text=True, capture_output=True, timeout=20).stdout.strip()


async def probe():
    if os.environ.get("BACKUP_MANAGER_DISPOSABLE_LOOP_FIXTURE") != "1":
        raise RuntimeError("Use a disposable container with private mount propagation.")
    provider = LinuxFilesystem()
    commands = FixtureCommands()
    context = ExecutionContext("loop-fixture", monotonic_ns() + 60_000_000_000, "fixture", "fixture")
    filesystem_uuid = str(uuid4())
    uuid_dir = Path("/dev/disk/by-uuid")
    uuid_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="bm-loop-") as temporary:
        root = Path(temporary)
        image = root / "image"
        with image.open("xb") as stream:
            stream.truncate(64 * 1024 * 1024)
        run("mke2fs", "-q", "-t", "ext4", "-F", "-U", filesystem_uuid, image)
        with open("/dev/loop-control", "rb", buffering=0) as loop_control:
            minor = fcntl.ioctl(loop_control, LOOP_CTL_GET_FREE)
        device = Path(f"/dev/loop{minor}")
        os.mknod(device, stat.S_IFBLK | 0o600, os.makedev(7, minor))
        mountpoint = root / "mount"
        mountpoint.mkdir()
        reference = uuid_dir / filesystem_uuid
        config = {"filesystem_uuid": filesystem_uuid, "mountpoint": str(mountpoint)}
        run("losetup", device, image)
        try:
            reference.symlink_to(device)
            acquired = await provider.acquire(config, context, commands)
            assert acquired.changed_physical_state and acquired.inspection.identity.verified_physical
            assert acquired.inspection.state is StorageState.AVAILABLE
            reused = await provider.acquire(config, context, commands)
            assert not reused.changed_physical_state and reused.ownership_evidence == {}
            try:
                await provider.release(config, context, commands, {})
            except LifecycleUncertain:
                pass
            else:
                raise AssertionError("Release accepted empty ownership evidence.")
            assert (await provider.inspect(config, context)).mounted
            released = await provider.release(config, context, commands, acquired.ownership_evidence)
            assert released.changed_physical_state and not released.inspection.mounted
            run("mount", "--", device, mountpoint)
            external = await provider.acquire(config, context, commands)
            assert not external.changed_physical_state and external.ownership_evidence == {}
            assert (await provider.inspect(config, context)).mounted
            print("Acquisition, ownership, release and external reuse assertions passed.")
        finally:
            if subprocess.run(["mountpoint", "-q", mountpoint], check=False).returncode == 0:
                run("umount", "--", mountpoint)
            reference.unlink(missing_ok=True)
            backing = run("losetup", "--noheadings", "--output", "BACK-FILE", device)
            if Path(backing) != image:
                raise RuntimeError("Loop device backing file changed. Refusing detach.")
            run("losetup", "--detach", device)
            device.unlink()
    print("Disposable mount, loop device and image cleaned up.")


if __name__ == "__main__":
    asyncio.run(probe())
