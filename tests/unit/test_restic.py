import json
from pathlib import Path
import unittest

from backup_manager.restic import (
    Binary, CompatibilityProfile, Repository, Restic, ResticEvidenceError,
    ResticUnsupported, confirm_backup, parse_repository, parse_snapshots,
)
from backup_manager.supervisor import ScopedResult

ID = "a" * 64
TREE = "b" * 64


def result(data, *, exit_code=0, truncated=False):
    output = data if isinstance(data, bytes) else json.dumps(data).encode()
    return ScopedResult(exit_code, output, b"", truncated, "fixture")


class ResticTests(unittest.TestCase):
    def setUp(self):
        self.binary = Binary(Path("/fixture/restic"), "candidate")
        self.profile = CompatibilityProfile("candidate", "restic 0.18.0 fixture", frozenset((2,)))
        self.version = result(b"restic 0.18.0 fixture\n")
        self.restic = Restic(self.binary, self.profile, self.version)
        self.source = Repository(Path("/source"), b"source-private")
        self.target = Repository(Path("/target"), b"target-private")
        self.snapshot = {
            "id": ID, "tree": TREE, "time": "2025-01-01T00:00:00+00:00",
            "hostname": "fixture", "paths": ["/source/data"],
        }

    def test_changed_binary_or_version_is_not_certified(self):
        with self.assertRaises(ResticUnsupported):
            Restic(Binary(self.binary.path, "other"), self.profile, self.version)
        with self.assertRaises(ResticUnsupported):
            Restic(self.binary, self.profile, result(b"restic 0.18.1 fixture\n"))
        with self.assertRaises(ResticUnsupported):
            Restic.certified(self.binary, self.version)

    def test_packaged_arm64_profile_requires_exact_binary_and_version(self):
        binary = Binary(self.binary.path, "a4aa97c7d651e87eb5ade27e32ae0f457887ee16b86958845e781aa18e524d03")
        version = result(b"restic 0.18.0 compiled with go1.24.1 on linux/arm64\n")
        adapter = Restic.certified(binary, version)
        self.assertEqual(adapter.profile.repository_formats, {1, 2})
        with self.assertRaises(ResticUnsupported):
            Restic.certified(binary, result(b"restic 0.18.0 compiled with go1.24.4 on linux/amd64\n"))

    def test_copy_requires_explicit_ids_and_separate_secret_channels(self):
        for ids in ((), (ID, ID), (ID[:8],)):
            with self.assertRaises((ValueError, ResticEvidenceError)):
                self.restic.copy(self.source, self.target, ids)
        invocation = self.restic.copy(self.source, self.target, (ID,))
        self.assertEqual(invocation.argv[-4:], ("--from-repo", "/source", "--", ID))
        self.assertEqual(invocation.env["RESTIC_PASSWORD"], "target-private")
        self.assertEqual(invocation.env["RESTIC_FROM_PASSWORD"], "source-private")
        self.assertNotIn("private", repr(invocation))
        self.assertNotIn("private", repr(self.source))

    def test_repository_identity_and_format_are_strict(self):
        data = {"version": 2, "id": ID, "chunker_polynomial": "0123456789abcdef"}
        self.assertEqual(parse_repository(result(data)).id, ID)
        with self.assertRaises(ResticEvidenceError):
            parse_repository(result(dict(data, version=True)))
        with self.assertRaises(ResticUnsupported):
            self.restic.repository_identity(result(dict(data, version=1)))

    def test_failed_truncated_or_duplicate_inventory_rejected(self):
        self.assertEqual(parse_snapshots(result([dict(self.snapshot, extension=True)]))[0].id, ID)
        for invalid in (
            result([self.snapshot, self.snapshot]), result([self.snapshot], exit_code=1),
            result([self.snapshot], truncated=True), result([dict(self.snapshot, time="wrong")]),
        ):
            with self.assertRaises(ResticEvidenceError):
                parse_snapshots(invalid)

    def test_backup_requires_clean_exit_and_new_snapshot_confirmation(self):
        summary = {"message_type": "summary", "snapshot_id": ID}
        after = parse_snapshots(result([self.snapshot]))
        self.assertEqual(confirm_backup(result(summary), (), after, expected_paths=("/source/data",)), ID)
        for before, fresh, outcome in (
            (after, after, result(summary)), ((), (), result(summary)),
            ((), after, result(summary, exit_code=3)), ((), after, result(summary, truncated=True)),
        ):
            with self.assertRaises(ResticEvidenceError):
                confirm_backup(outcome, before, fresh, expected_paths=("/source/data",))

    def test_diagnostics_redact_both_credentials(self):
        invocation = self.restic.copy(self.source, self.target, (ID,))
        observed = ScopedResult(12, b"source-private", b"target-private and source-private", False, "fixture")
        self.assertEqual(invocation.redacted_diagnostics(observed), "[REDACTED] and [REDACTED]")
        self.assertNotIn("private", repr(observed))
