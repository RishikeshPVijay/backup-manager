from threading import Barrier, Lock
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from backup_manager.convergence import NativeSnapshot
from backup_manager.replication import Replication
from backup_manager.restic import ResticEvidenceError


class ReplicationRuntimeTests(TestCase):
    def setUp(self):
        self.snapshot = NativeSnapshot("1" * 64, None, "2" * 64, "2025-01-01T00:00:00+00:00", "host", ("/data",), ())
        self.runner = Mock()
        self.runner.selected.destination = "a"
        self.runner.snapshot = self.snapshot.id
        adapter = SimpleNamespace(profile=SimpleNamespace(binary_sha256="profile"))
        self.replication = Replication(self.runner, adapter)
        self.replication.repos.update({
            key: SimpleNamespace(registration=SimpleNamespace(key=key, generation=1)) for key in "bc"
        })

    def test_distinct_targets_start_concurrently_with_the_same_frozen_baseline(self):
        barrier = Barrier(2)
        guard = Lock()
        started = []

        def copy(phase, plan):
            with guard:
                started.append((phase, plan.source, plan.destination, plan.snapshots))
            barrier.wait(timeout=5)
            return "SUCCEEDED", {}

        with (
            patch.object(self.replication, "enumerate", return_value=(self.snapshot,)),
            patch.object(self.replication, "copy", side_effect=copy),
        ):
            self.assertTrue(self.replication.normal())
        self.assertEqual(set(started), {
            ("replication", "a", "b", (self.snapshot,)),
            ("replication", "a", "c", (self.snapshot,)),
        })

    def test_failed_required_inventory_does_not_schedule_convergence(self):
        with (
            patch.object(self.replication, "enumerate", side_effect=[(self.snapshot,), ResticEvidenceError()]),
            patch.object(self.replication, "copy") as copy,
        ):
            self.assertFalse(self.replication.convergence())
        copy.assert_not_called()
        self.runner.record.assert_called_once_with(
            "convergence", "incomplete", target="b", code="INVENTORY_ENUMERATION_FAILED",
        )

    def test_fixed_source_does_not_fall_back_on_failed_or_reduced_baseline(self):
        for outcome, expected in ((ResticEvidenceError(), "SOURCE_ENUMERATION_FAILED"), ((), "SOURCE_SNAPSHOT_MISSING")):
            with self.subTest(code=expected):
                self.runner.record.reset_mock()
                with (
                    patch.object(self.replication, "enumerate", side_effect=[outcome]) as enumerate_source,
                    patch.object(self.replication, "copy") as copy,
                ):
                    self.assertFalse(self.replication.normal())
                enumerate_source.assert_called_once_with("a")
                copy.assert_not_called()
                self.runner.record.assert_called_once_with("replication", "incomplete", target="a", code=expected)

    def test_known_disconnect_removes_the_stale_view_and_allows_other_work(self):
        from backup_manager.jobs import DestinationRejected

        old = {"a": (self.snapshot,), "b": (self.snapshot,)}
        with patch.object(self.replication, "enumerate", side_effect=DestinationRejected("REPOSITORY_DISCONNECTED")):
            self.assertTrue(self.replication.refresh("b", old, failure_code="INVENTORY_REFRESH_FAILED"))
        self.assertEqual(old, {"a": (self.snapshot,)})
        self.assertEqual(self.replication.unavailable, {"b"})
