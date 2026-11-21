"""Explicit SQLite state initialization and verification."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import sqlite3
import stat
from tempfile import NamedTemporaryFile
from uuid import uuid4


class StateUnsafe(Exception):
    """Database state cannot be safely used."""


LOCAL_FILESYSTEMS = {
    b"btrfs", b"ext2", b"ext3", b"ext4", b"f2fs", b"tmpfs", b"xfs", b"zfs",
}


def require_local_directory(path):
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise StateUnsafe("State directory must be an existing canonical path.")
    fd = os.open(path, os.O_DIRECTORY | os.O_PATH | os.O_NOFOLLOW)
    try:
        fields = Path(f"/proc/self/fdinfo/{fd}").read_bytes().splitlines()
        mount_id = next(line.split()[1] for line in fields if line.startswith(b"mnt_id:"))
        mounts = Path("/proc/self/mountinfo").read_bytes().splitlines()
        mount = next(line for line in mounts if line.split()[0] == mount_id)
        filesystem = mount.split(b" - ", 1)[1].split()[0]
        if filesystem not in LOCAL_FILESYSTEMS:
            raise StateUnsafe("State directory is not on a supported local filesystem.")
        metadata = os.fstat(fd)
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
            raise StateUnsafe("State directory must be owned by the current user and protected from other writers.")
    finally:
        os.close(fd)


def private_file(path):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise StateUnsafe("State files must be private regular files.")


@contextmanager
def state_lock(path, anchor, *, exclusive):
    require_local_directory(path.parent)
    require_local_directory(anchor.parent)
    if path.parent == anchor.parent or path.parent in anchor.parents or anchor.parent in path.parents:
        raise StateUnsafe("Database and witness must use separate directory trees.")
    fd = os.open(anchor.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        yield
    except BlockingIOError as error:
        raise StateUnsafe("State witness directory is busy.") from error
    finally:
        os.close(fd)


def sync_directory(path):
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def migration():
    contents = files("backup_manager.migrations").joinpath("001_initial.sql").read_bytes()
    return contents.decode(), hashlib.sha256(contents).hexdigest()


def connect(path, mode):
    connection = sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True, timeout=5)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(path, anchor):
    """Create a new instance only upon explicit administrative authorization."""
    try:
        with state_lock(path, anchor, exclusive=True):
            if any(os.path.lexists(item) for item in (
                path, anchor, path.with_name(path.name + ".identity"), path.with_name(path.name + "-wal"),
                path.with_name(path.name + "-shm"), path.with_name(path.name + "-journal"),
            )):
                raise StateUnsafe("State or recovery evidence already exists.")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            script, checksum = migration()
            instance_id = str(uuid4())
            created = datetime.now(timezone.utc).isoformat()
            with closing(connect(path, "rw")) as database:
                if database.execute("PRAGMA journal_mode = WAL").fetchone() != ("wal",):
                    raise StateUnsafe("Could not enable SQLite WAL.")
                database.execute("PRAGMA synchronous = FULL")
                database.executescript("BEGIN IMMEDIATE;\n" + script)
                database.execute("INSERT INTO schema_migrations VALUES (1, ?, ?)", (checksum, created))
                database.execute("INSERT INTO instance_state VALUES (1, ?, 1, ?)", (instance_id, created))
                database.commit()
            record = {"instance_id": instance_id, "generation": 1}
            fd = os.open(anchor, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream)
                stream.flush()
                os.fsync(stream.fileno())
            sync_directory(path.parent)
            sync_directory(anchor.parent)
            return record
    except (OSError, sqlite3.Error, StopIteration, ValueError) as error:
        raise StateUnsafe("State initialization failed. Inspect any partial files before recovery.") from error


def checked_record(path, anchor):
    private_file(path)
    private_file(anchor)
    record = json.loads(anchor.read_text(encoding="utf-8"))
    # Immutable SQLite reads ignore WAL. An existing active or interrupted
    # WAL must be reconciled by the supervisor before this check is safe.
    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.lexists(path.with_name(path.name + suffix)):
            raise StateUnsafe("State has a SQLite sidecar requiring reconciliation.")
    with closing(connect(path, "ro&immutable=1")) as database:
        if database.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise StateUnsafe("Database integrity check failed.")
        if database.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise StateUnsafe("Database foreign key check failed.")
        row = database.execute("SELECT instance_id, generation FROM instance_state").fetchone()
        _, checksum = migration()
        if database.execute("SELECT version, checksum FROM schema_migrations").fetchall() != [(1, checksum)]:
            raise StateUnsafe("Database schema migration does not match this package.")
    if row is None or record != {"instance_id": row[0], "generation": row[1]}:
        raise StateUnsafe("Database identity or generation differs from its anchor.")
    return record


def check(path, anchor):
    """Read state without creating SQLite sidecar files or authorizing work."""
    try:
        with state_lock(path, anchor, exclusive=False):
            return checked_record(path, anchor)
    except (OSError, sqlite3.Error, StopIteration, ValueError) as error:
        raise StateUnsafe("Missing, unreadable, or corrupt state. Explicit recovery is required.") from error


def advance_witness(anchor, record):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=anchor.parent, delete=False) as stream:
        staged = Path(stream.name)
        try:
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(staged, anchor)
            sync_directory(anchor.parent)
        finally:
            staged.unlink(missing_ok=True)


@contextmanager
def state_write(path, anchor):
    """Reserve the next generation before any transaction that changes state."""
    try:
        with state_lock(path, anchor, exclusive=True):
            record = checked_record(path, anchor)
            current = record["generation"]
            record["generation"] = current + 1
            # If a crash occurs after reservation, the mismatch is intentional.
            # No runtime reader can treat the older database as safe.
            advance_witness(anchor, record)
            with closing(connect(path, "rw")) as database:
                database.execute("PRAGMA synchronous = FULL")
                with database:
                    database.execute("BEGIN IMMEDIATE")
                    updated = database.execute(
                        "UPDATE instance_state SET generation = ? WHERE singleton = 1 AND generation = ?",
                        (current + 1, current),
                    )
                    if updated.rowcount != 1:
                        raise StateUnsafe("State generation changed during admission.")
                    yield database
    except (OSError, sqlite3.Error, StopIteration, ValueError) as error:
        raise StateUnsafe("State write failed. Explicit recovery may be required.") from error
