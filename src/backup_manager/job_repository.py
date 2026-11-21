"""Repository registration binding and per-command directory identity."""

from dataclasses import dataclass
import os
from pathlib import Path

from .plugin_api import StorageState
from .scope_worker import directory_identity
from .state import StateUnsafe


@dataclass(frozen=True)
class Registration:
    key: str
    restic_id: str
    generation: int
    storage_id: str
    path: Path


def registration(database, job_id, destination_id):
    row = database.execute(
        """SELECT repository_key, restic_repository_id, repo_generation, storage_id, repository_path
           FROM repository_registrations WHERE job_id = ? AND destination_id = ?""",
        (job_id, destination_id),
    ).fetchone()
    if row is None or row[3] is None or row[4] is None:
        return None
    key, restic_id, generation, resource, path = row
    return Registration(key, restic_id, generation, resource, Path(path))


def inspect_repository(coordinator, lease, path, deadline):
    physical, identity = coordinator.observe(lease.resource_id, deadline)
    if physical.state is not StorageState.AVAILABLE or not physical.mounted or not physical.mountpoint:
        raise StateUnsafe("Repository storage is no longer mounted and validated.")
    if identity.verified_physical and identity.coordination_id != lease.coordination_id:
        raise StateUnsafe("Repository storage identity changed.")
    mount = Path(physical.mountpoint)
    try:
        if (
            not path.is_absolute() or not mount.is_absolute()
            or not path.is_relative_to(mount)
            or mount.resolve(strict=True) != mount or path.resolve(strict=True) != path
        ):
            raise StateUnsafe("Repository path does not stay within its validated mount.")
        flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        mount_fd = os.open(mount, flags)
        try:
            repo_fd = os.open(path, flags)
            try:
                mount_identity = directory_identity(mount_fd)
                repo_identity = directory_identity(repo_fd)
            finally:
                os.close(repo_fd)
        finally:
            os.close(mount_fd)
        if (
            repo_identity["mount_id"] != mount_identity["mount_id"]
            or (identity.evidence.get("mount_id") and identity.evidence["mount_id"] != mount_identity["mount_id"])
        ):
            raise StateUnsafe("Repository path crosses another mount.")
        return {"path": str(path), "identity": repo_identity}
    except (OSError, ValueError, StopIteration):
        raise StateUnsafe("The reported repository directory cannot be verified.") from None
