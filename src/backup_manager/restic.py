"""Typed Restic calls and strict parsing for repository evidence."""

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import shutil
import stat

HEX_ID = re.compile(r"[0-9a-f]{64}")
CHUNKER = re.compile(r"[0-9a-f]{1,16}")
KEEP = ("last", "hourly", "daily", "weekly", "monthly", "yearly")


class ResticUnsupported(Exception):
    """The executable or repository format is outside the certified profile."""


class ResticEvidenceError(Exception):
    """Restic did not supply complete usable evidence."""


def snapshot_id(value):
    if not isinstance(value, str) or not HEX_ID.fullmatch(value):
        raise ResticEvidenceError("Expected a complete Restic object ID.")
    return value


@dataclass(frozen=True)
class Binary:
    path: Path
    sha256: str

    @classmethod
    def identify(cls, executable):
        candidate = shutil.which(executable)
        if candidate is None:
            raise ResticUnsupported("The configured Restic executable is unavailable.")
        try:
            path = Path(candidate).resolve(strict=True)
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError("Expected a regular executable file.")
                return cls(path, hashlib.file_digest(stream, "sha256").hexdigest())
        except (OSError, ValueError):
            raise ResticUnsupported("The configured Restic executable could not be fingerprinted.") from None


@dataclass(frozen=True)
class CompatibilityProfile:
    binary_sha256: str
    version_line: str
    repository_formats: frozenset[int]
    native_overlap: frozenset[frozenset[str]] = frozenset()


def installed_profile(binary):
    source = files("backup_manager.schemas").joinpath("restic-profiles.json")
    profiles = json.loads(source.read_text(encoding="utf-8"))
    if profiles["schema_version"] != 1:
        raise ResticUnsupported("The installed certification profile format is unsupported.")
    matches = [item for item in profiles["profiles"] if item["binary_sha256"] == binary.sha256]
    if len(matches) != 1:
        raise ResticUnsupported("No certification profile matches the configured Restic binary fingerprint.")
    profile = matches[0]
    return CompatibilityProfile(
        profile["binary_sha256"], profile["version_line"], frozenset(profile["repository_formats"]),
        frozenset(frozenset(pair) for pair in profile["native_overlap"]),
    )


@dataclass(frozen=True)
class Repository:
    path: Path
    password: bytes = field(repr=False)

    def __post_init__(self):
        if not self.path.is_absolute() or ".." in self.path.parts:
            raise ValueError("Restic repositories must be absolute local paths.")
        if not self.password.strip() or b"\0" in self.password:
            raise ValueError("Restic password material is missing or invalid.")


@dataclass(frozen=True)
class Invocation:
    argv: tuple[str, ...]
    env: dict[str, str] = field(repr=False)
    executable_sha256: str | None = None

    def redacted_diagnostics(self, result):
        """Format diagnostic output without exposing known credentials."""
        output = result.stderr
        credentials = (
            os.fsencode(value) for name, value in self.env.items()
            if name in {"RESTIC_PASSWORD", "RESTIC_FROM_PASSWORD"} and value
        )
        if result.truncated and credentials:
            return "[OUTPUT OMITTED: CAPTURE TRUNCATED]" if output else ""
        for credential in sorted(credentials, key=len, reverse=True):
            output = output.replace(credential, b"[REDACTED]")
        return output.decode(errors="replace")


def run_invocation(path, anchor, operation_id, owner_id, invocation, *, deadline, authorize=None, cancelled=None):
    from .supervisor import run_journaled

    return run_journaled(
        path, anchor, operation_id, owner_id, invocation.argv,
        deadline=deadline, env=invocation.env, authorize=authorize, cancelled=cancelled,
        executable_sha256=invocation.executable_sha256,
    )


@dataclass(frozen=True)
class RepositoryIdentity:
    id: str
    version: int
    chunker_polynomial: str


@dataclass(frozen=True)
class Snapshot:
    id: str
    original: str | None
    tree: str
    time: str
    hostname: str
    paths: tuple[str, ...]
    tags: tuple[str, ...]


def successful_output(result):
    if result.exit_code != 0 or result.truncated:
        raise ResticEvidenceError("Restic returned a failed or incomplete result.")
    return result.stdout


def parse_version(result):
    output = successful_output(result).decode("ascii", errors="replace").rstrip("\n")
    if not re.fullmatch(r"restic \d+\.\d+\.\d+[^\r\n]*", output):
        raise ResticEvidenceError("Invalid Restic version response.")
    return output


def parse_repository(result):
    try:
        data = json.loads(successful_output(result))
        identity = RepositoryIdentity(snapshot_id(data["id"]), data["version"], data["chunker_polynomial"])
        if type(identity.version) is not int or not CHUNKER.fullmatch(identity.chunker_polynomial):
            raise ValueError
        return identity
    except (ValueError, TypeError, KeyError):
        raise ResticEvidenceError("Invalid Restic repository identity response.") from None


def string_array(value):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError
    return tuple(value)


def parse_snapshots(result):
    try:
        data = json.loads(successful_output(result))
        if not isinstance(data, list):
            raise ValueError
        snapshots = []
        ids = set()
        for row in data:
            item_id = snapshot_id(row["id"])
            if item_id in ids:
                raise ValueError
            ids.add(item_id)
            timestamp = row["time"]
            if datetime.fromisoformat(timestamp).tzinfo is None or not isinstance(row["hostname"], str):
                raise ValueError
            original = row.get("original")
            snapshots.append(Snapshot(
                item_id, snapshot_id(original) if original is not None else None,
                snapshot_id(row["tree"]), timestamp, row["hostname"],
                string_array(row["paths"]), string_array(row.get("tags", [])),
            ))
        return tuple(sorted(snapshots, key=lambda item: item.id))
    except (ValueError, TypeError, KeyError):
        raise ResticEvidenceError("Invalid or incomplete Restic snapshot inventory.") from None


def parse_backup(result):
    try:
        rows = [json.loads(line) for line in successful_output(result).splitlines() if line]
        summary = [row for row in rows if row["message_type"] == "summary"]
        if len(summary) != 1 or rows[-1] is not summary[0]:
            raise ValueError
        return snapshot_id(summary[0]["snapshot_id"])
    except (ValueError, TypeError, KeyError, IndexError):
        raise ResticEvidenceError("Restic did not report a single completed backup snapshot.") from None


def confirm_backup(result, before, after, *, expected_paths):
    created = parse_backup(result)
    snapshot = next((item for item in after if item.id == created), None)
    if snapshot is None or any(item.id == created for item in before):
        raise ResticEvidenceError("The expected new backup snapshot was not confirmed.")
    required = {os.path.normpath(os.fspath(path)) for path in expected_paths}
    if not required or set(snapshot.paths) != required:
        raise ResticEvidenceError("The new snapshot does not cover the expected source paths.")
    return created


class Restic:
    """Build invocations after exact binary and repository-format checks."""

    def __init__(self, binary, profile, version_result):
        if binary.sha256 != profile.binary_sha256 or parse_version(version_result) != profile.version_line:
            raise ResticUnsupported("The Restic binary does not match an installed certification profile.")
        self.binary = binary
        self.profile = profile

    @classmethod
    def certified(cls, binary, version_result):
        return cls(binary, installed_profile(binary), version_result)

    @staticmethod
    def version(binary):
        return Invocation((str(binary.path), "version"), {"PATH": os.defpath, "LC_ALL": "C"}, binary.sha256)

    def repository_identity(self, result):
        identity = parse_repository(result)
        if identity.version not in self.profile.repository_formats:
            raise ResticUnsupported("The Restic repository format is not certified.")
        return identity

    def _command(self, repo, *args, source=None):
        env = {"PATH": os.defpath, "LC_ALL": "C", "RESTIC_PASSWORD": os.fsdecode(repo.password)}
        if source is not None:
            env["RESTIC_FROM_PASSWORD"] = os.fsdecode(source.password)
        return Invocation((str(self.binary.path), "--no-cache", "--repo", str(repo.path), *args), env, self.binary.sha256)

    def identity(self, repo):
        return self._command(repo, "cat", "config")

    def snapshots(self, repo):
        return self._command(repo, "snapshots", "--json")

    def backup(self, repo, sources, *, excludes=(), tags=()):
        sources = tuple(sources)
        if not sources or any(not Path(path).is_absolute() for path in sources):
            raise ValueError("Backup requires at least one absolute source.")
        flags = tuple(flag for exclude in excludes for flag in ("--exclude", exclude))
        labels = tuple(flag for tag in tags for flag in ("--tag", tag))
        return self._command(repo, "backup", "--json", *flags, *labels, "--", *map(str, sources))

    def copy(self, source, destination, expected_ids):
        ids = tuple(snapshot_id(item) for item in expected_ids)
        if not ids or len(set(ids)) != len(ids) or source.path == destination.path:
            raise ValueError("Copy requires distinct repositories and an explicit unique snapshot set.")
        return self._command(destination, "copy", "--from-repo", str(source.path), "--", *ids, source=source)

    def forget(self, repo, selectors, *, preview=False):
        flags = []
        for key, count in selectors.items():
            if key == "prune":
                continue
            if not key.startswith("keep_") or key[5:] not in KEEP or type(count) is not int or count < 1:
                raise ValueError("Unsupported or invalid retention selector.")
            flags.extend((f"--keep-{key[5:]}", str(count)))
        if not flags:
            raise ValueError("At least one retention selector is required.")
        return self._command(repo, "forget", "--json", *flags, *(("--dry-run",) if preview else ()))

    def prune(self, repo):
        return self._command(repo, "prune")

    def check(self, repo):
        return self._command(repo, "check")

    def explicit_init(self, repo, version):
        if version not in self.profile.repository_formats:
            raise ResticUnsupported("The requested initialization format is not certified.")
        return self._command(repo, "init", "--repository-version", str(version))

    def explicit_unlock(self, repo):
        return self._command(repo, "unlock")
