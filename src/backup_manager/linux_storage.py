"""Linux filesystem inspection and dedicated mountpoint lifecycle."""

import os
from pathlib import Path
import stat
from time import monotonic_ns

from .plugin_api import (
    API_VERSION, LifecycleResult, StorageIdentity, StorageInspection, StorageState,
)


class LifecycleUncertain(Exception):
    """The worker cannot confirm the physical result or mount ownership."""


def mountinfo_path(path):
    escaped = os.fsencode(path)
    for char, sequence in ((b"\\", b"\\134"), (b" ", b"\\040"),
                           (b"\t", b"\\011"), (b"\n", b"\\012")):
        escaped = escaped.replace(char, sequence)
    return escaped


class LinuxFilesystem:
    provider_id = "linux_filesystem"
    api_version = API_VERSION
    capabilities = ("inspect", "resolve_identity", "acquire", "release")
    config_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["filesystem_uuid", "mountpoint"],
        "properties": {
            "filesystem_uuid": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]*$"},
            "mountpoint": {"type": "string", "pattern": "^/"},
        },
    }

    async def inspect(self, config, context):
        mountpoint = Path(config["mountpoint"])
        uuid = config["filesystem_uuid"]
        coordination_id = "filesystem:" + uuid.lower()
        unknown = StorageIdentity(coordination_id, None, {}, False)
        if not mountpoint.is_absolute() or mountpoint == Path("/") or ".." in mountpoint.parts:
            return StorageInspection(StorageState.UNCERTAIN, False, None, unknown, None)
        fd = None
        try:
            if mountpoint.resolve(strict=True) != mountpoint:
                return StorageInspection(StorageState.UNCERTAIN, False, None, unknown, None)
            fd = os.open(mountpoint, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            mount = Path(f"/proc/self/fdinfo/{fd}").read_text().splitlines()
            mount_id = next(line.split()[1] for line in mount if line.startswith("mnt_id:"))
            rows = Path("/proc/self/mountinfo").read_bytes().splitlines()
            row = next(row.split() for row in rows if row.split()[0] == mount_id.encode())
            escaped = mountinfo_path(mountpoint)
            conflicting_mounts = sum(
                entry.split()[4] == escaped or entry.split()[4].startswith(escaped + b"/")
                for entry in rows
            )
            details = {"mount_count": str(conflicting_mounts)}
            if row[4] != escaped:
                return StorageInspection(StorageState.UNAVAILABLE, False, str(mountpoint), unknown, None, details)
            try:
                device = (Path("/dev/disk/by-uuid") / uuid).stat()
            except OSError:
                return StorageInspection(StorageState.UNCERTAIN, True, str(mountpoint), unknown, None, details)
            filesystem = os.fstat(fd)
            if not stat.S_ISBLK(device.st_mode) or filesystem.st_dev != device.st_rdev:
                return StorageInspection(StorageState.UNCERTAIN, True, str(mountpoint), unknown, None, details)
            evidence = {
                "mount_id": mount_id,
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "mount_namespace": os.readlink("/proc/self/ns/mnt"),
                "mountpoint": str(mountpoint),
                "physical_identity": coordination_id,
                "device": f"{os.major(filesystem.st_dev)}:{os.minor(filesystem.st_dev)}",
            }
            identity = StorageIdentity(coordination_id, coordination_id, evidence, True)
            return StorageInspection(StorageState.AVAILABLE, True, str(mountpoint), identity, None, details)
        except FileNotFoundError:
            return StorageInspection(StorageState.UNAVAILABLE, False, str(mountpoint), unknown, None)
        except (OSError, ValueError, StopIteration):
            return StorageInspection(StorageState.UNCERTAIN, False, str(mountpoint), unknown, None)
        finally:
            if fd is not None:
                os.close(fd)

    async def resolve_identity(self, config, context):
        return (await self.inspect(config, context)).identity

    async def acquire(self, config, context, commands):
        before = await self.inspect(config, context)
        if before.state is StorageState.AVAILABLE:
            return LifecycleResult(before, False, {})
        if (
            before.state is not StorageState.UNAVAILABLE or before.mounted
            or before.details.get("mount_count") != "0"
        ):
            raise LifecycleUncertain("Mountpoint is unavailable for acquisition.")
        mountpoint = before.mountpoint
        device = Path("/dev/disk/by-uuid") / config["filesystem_uuid"]
        try:
            if not stat.S_ISBLK(device.stat().st_mode):
                raise LifecycleUncertain("Configured filesystem is not a block device.")
        except OSError:
            raise LifecycleUncertain("Configured filesystem is unavailable.") from None
        await self._run(commands, context, ("/usr/bin/mount", "--", str(device), mountpoint))
        after = await self.inspect(config, context)
        if after.state is not StorageState.AVAILABLE or after.details.get("mount_count") != "1":
            raise LifecycleUncertain("Cannot verify the acquired filesystem.")
        return LifecycleResult(after, True, after.identity.evidence)

    async def release(self, config, context, commands, ownership_evidence):
        before = await self.inspect(config, context)
        if (
            before.state is not StorageState.AVAILABLE
            or before.details.get("mount_count") != "1"
            or not ownership_evidence
            or ownership_evidence != before.identity.evidence
        ):
            raise LifecycleUncertain("Cannot verify a dedicated manager-owned mount.")
        await self._run(commands, context, ("/usr/bin/umount", "--", before.mountpoint))
        after = await self.inspect(config, context)
        if (
            after.state is not StorageState.UNAVAILABLE or after.mounted
            or after.details.get("mount_count") != "0"
        ):
            raise LifecycleUncertain("Cannot confirm removal of the manager-owned mount.")
        return LifecycleResult(after, True, {})

    @staticmethod
    async def _run(commands, context, argv):
        if monotonic_ns() >= context.deadline_monotonic_ns:
            raise LifecycleUncertain("Storage operation deadline has expired.")
        result = await commands.run(argv, context=context)
        if result.exit_code != 0 or not result.termination_confirmed:
            raise LifecycleUncertain("Storage command did not complete with confirmed success.")
