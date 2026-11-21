from dataclasses import asdict
import json
import subprocess
import sys
from time import monotonic_ns
import unittest

from backup_manager.lifecycle_worker import OUTPUT_LIMIT, WorkerCommands
from backup_manager.plugin_api import ExecutionContext


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_executor_bounds_both_output_streams(self):
        context = ExecutionContext("test", monotonic_ns() + 5_000_000_000, "test", "test")
        command = (sys.executable, "-c", "import os; os.write(1, b'x' * 200000); os.write(2, b'y' * 200000)")
        result = await WorkerCommands().run(command, context=context)
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.termination_confirmed)
        self.assertEqual(result.stdout, b"x" * OUTPUT_LIMIT)
        self.assertEqual(result.stderr, b"y" * OUTPUT_LIMIT)

    def test_worker_rejects_unsafe_request_without_output(self):
        context = ExecutionContext("test", monotonic_ns() + 5_000_000_000, "test", "test")
        request = {
            "context": asdict(context),
            "provider": "linux_filesystem",
            "operation": "release",
            "config": {"mountpoint": "/somewhere", "filesystem_uuid": "fixture"},
            "ownership_evidence": {},
        }
        process = subprocess.run(
            [sys.executable, "-m", "backup_manager.lifecycle_worker"],
            input=json.dumps(request), text=True, capture_output=True, timeout=10,
            start_new_session=True, check=False,
        )
        self.assertEqual((process.returncode, process.stdout, process.stderr), (1, "", ""))
