"""Verifier tests with fabricated inputs, independent of certification evidence."""

import hashlib
import io
import json
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from tools import verify_arm64_report as verifier


class Arm64ReportTests(unittest.TestCase):
    def setUp(self):
        self.report = {
            "schema_version": 1, "passed": True, "unchanged_inputs": True,
            "git_commit": verifier.RUN_COMMIT, "runner_sha256": verifier.RUNNER_SHA256,
            "adapter_sha256": verifier.ADAPTER_SHA256, "restic_binary_sha256": verifier.BINARY_SHA256,
            "restic_version": "restic 0.18.0 compiled with go1.24.1 on linux/arm64",
            "environment": "Linux-6.1-aarch64-with-glibc2.39",
            "cases": [
                {"test_id": name, "repository_format": fmt, "status": "pass",
                 "fixture_removed": True, "commands": [{"argv": ["fixture"]}]}
                for name, fmt in sorted(verifier.REQUIRED_CASES)
            ],
        }

    def check_archive(self, *, name=verifier.REPORT_NAME):
        stream = io.BytesIO()
        with ZipFile(stream, "w") as archive:
            archive.writestr(name, json.dumps(self.report))
        data = stream.getvalue()
        with patch.object(verifier, "ARCHIVE_SHA256", hashlib.sha256(data).hexdigest()):
            return verifier.verify(data)

    def test_outputs_candidate_with_exact_report_digest(self):
        candidate, report = self.check_archive()
        self.assertEqual(candidate["binary_sha256"], verifier.BINARY_SHA256)
        self.assertEqual(candidate["report_sha256"], hashlib.sha256(report).hexdigest())

    def test_refuses_wrong_archive_digest_or_unexpected_member(self):
        with self.assertRaisesRegex(ValueError, "Archive SHA-256"):
            verifier.verify(b"not the original artifact")
        with self.assertRaisesRegex(ValueError, "archive contents"):
            self.check_archive(name="../" + verifier.REPORT_NAME)

    def test_refuses_wrong_provenance_duplicate_case_or_unclean_fixture(self):
        for change, error in (
            ({"git_commit": "0" * 40}, "provenance"),
            ({"cases": [self.report["cases"][0]] * len(verifier.REQUIRED_CASES)}, "required cases"),
            ({"cases": [dict(case, fixture_removed=False) for case in self.report["cases"]]}, "cleanup"),
        ):
            with self.subTest(change=tuple(change)), patch.dict(self.report, change):
                with self.assertRaisesRegex(ValueError, error):
                    self.check_archive()
