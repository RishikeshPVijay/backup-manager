"""Read-only filesystem identity checks in the current Linux mount namespace."""

import os
from pathlib import Path
import stat

from .plugin_api import API_VERSION, StorageIdentity, StorageInspection, StorageState


class LinuxFilesystem:
    provider_id = "linux_filesystem"
    api_version = API_VERSION
    capabilities = ("inspect", "resolve_identity")
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
        fd = None
        try:
            if mountpoint.resolve(strict=True) != mountpoint:
                return StorageInspection(StorageState.UNCERTAIN, False, None, unknown, None)
            fd = os.open(mountpoint, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            mount = Path(f"/proc/self/fdinfo/{fd}").read_text().splitlines()
            mount_id = next(line.split()[1] for line in mount if line.startswith("mnt_id:"))
            rows = Path("/proc/self/mountinfo").read_bytes().splitlines()
            row = next(row.split() for row in rows if row.split()[0] == mount_id.encode())
            escaped = os.fsencode(mountpoint)
            for char, sequence in ((b"\\", b"\\134"), (b" ", b"\\040"),
                                   (b"\t", b"\\011"), (b"\n", b"\\012")):
                escaped = escaped.replace(char, sequence)
            if row[4] != escaped:
                return StorageInspection(StorageState.UNAVAILABLE, False, str(mountpoint), unknown, None)
            device = (Path("/dev/disk/by-uuid") / uuid).stat()
            filesystem = os.fstat(fd)
            if not stat.S_ISBLK(device.st_mode) or filesystem.st_dev != device.st_rdev:
                return StorageInspection(StorageState.UNCERTAIN, True, str(mountpoint), unknown, None)
            identity = StorageIdentity(coordination_id, coordination_id, {"mount_id": mount_id}, True)
            return StorageInspection(StorageState.AVAILABLE, True, str(mountpoint), identity, None)
        except FileNotFoundError:
            return StorageInspection(StorageState.UNAVAILABLE, False, str(mountpoint), unknown, None)
        except (OSError, ValueError, StopIteration):
            return StorageInspection(StorageState.UNCERTAIN, False, str(mountpoint), unknown, None)
        finally:
            if fd is not None:
                os.close(fd)

    async def resolve_identity(self, config, context):
        return (await self.inspect(config, context)).identity
