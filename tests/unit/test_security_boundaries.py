"""Regression tests for executable binding and bounded credential output."""

import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic
import unittest
from unittest.mock import MagicMock, Mock, patch

from backup_manager.inspection import read_command
from backup_manager.job_hooks import redact_output
from backup_manager.restic import Binary, Invocation, ResticUnsupported
from backup_manager.scope_worker import check_executable
from backup_manager.supervisor import ScopedResult


class SecurityBoundaryTests(unittest.TestCase):
    def test_exec_uses_open_verified_inode_after_path_replacement(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "native"
            shutil.copy2("/usr/bin/true", path)
            binary = Binary.identify(str(path))
            replacement = path.with_name("replacement")
            shutil.copy2("/usr/bin/false", replacement)
            with check_executable(path, binary.sha256) as launch:
                replacement.replace(path)
                self.assertEqual(subprocess.run([str(path)], **launch, timeout=5).returncode, 0)
            with self.assertRaisesRegex(ValueError, "changed before launch"):
                with check_executable(path, binary.sha256):
                    self.fail("Changed executable was authorized.")

    def test_executable_identification_rejects_nonregular_files(self):
        with TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "executable-fifo"
            os.mkfifo(candidate, 0o700)
            with self.assertRaises(ResticUnsupported):
                Binary.identify(str(candidate))

    def test_truncated_capture_cannot_persist_partial_known_credential(self):
        captured = ScopedResult(0, b"visible secret-pre", b"other", True, "fixture")
        output = redact_output(captured, [b"secret-prefix-complete"])
        self.assertTrue(output["truncated"])
        self.assertEqual(output["stdout"], "[OUTPUT OMITTED: CAPTURE TRUNCATED]")
        self.assertNotIn("secret-pre", str(output))

    def test_read_timeout_never_waits_unboundedly_for_a_stuck_process(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "pipe"
            path.touch()
            with path.open("rb") as stdout, path.open("rb") as stderr:
                process = Mock(pid=123, stdout=stdout, stderr=stderr)
                process.poll.return_value = None
                process.wait.side_effect = subprocess.TimeoutExpired("fixture", 5)
                selector = MagicMock()
                selector.__enter__.return_value.get_map.return_value = {1: None}
                with (
                    patch("backup_manager.inspection.subprocess.Popen", return_value=process),
                    patch("backup_manager.inspection.selectors.DefaultSelector", return_value=selector),
                    patch("backup_manager.inspection.os.killpg") as kill,
                    self.assertRaises(subprocess.TimeoutExpired),
                ):
                    read_command(Invocation(("fixture",), {}), monotonic() - 1)
                process.wait.assert_called_once_with(timeout=5)
                kill.assert_called_once()
                self.assertTrue(stdout.closed and stderr.closed)
