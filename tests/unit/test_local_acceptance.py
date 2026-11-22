"""Acceptance assertions that do not require a systemd scope."""

import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from backup_manager.cli import execute_batch
from backup_manager.errors import Result, Status
from backup_manager.job_hooks import repository_password
from backup_manager.job_repository import Registration
from backup_manager.jobs import DestinationRejected, JobRunner, JobStopped, NANOSECONDS
from backup_manager.maintenance import run_maintenance
from backup_manager.plugin_api import API_VERSION, StorageIdentity, StorageInspection, StorageState
from backup_manager.providers import ProviderError, discover
from backup_manager.replication import Replication
from backup_manager.restic import Binary, Repository, Restic
from backup_manager.scope_worker import credentials
from backup_manager.state import initialize, state_read
from backup_manager.storage_claims import admit_storage_claim, lifecycle_view, plan_acquisition, reconcile_identity
from backup_manager.storage_coordinator import StorageLease, StorageUnavailable
from backup_manager.supervisor import ScopedResult


class InspectOnly:
    provider_id = "fixture_storage"
    api_version = API_VERSION
    config_schema = {"type": "object"}
    capabilities = {"inspect", "resolve_identity"}

    async def inspect(self, config, context):
        return None

    async def resolve_identity(self, config, context):
        return None


class FixtureSecret:
    provider_id = "fixture_secret"
    api_version = API_VERSION
    config_schema = {"type": "object"}

    async def resolve(self, reference, context):
        return reference.config["key"].encode()


class LocalAcceptanceTests(unittest.TestCase):
    def test_batch_submits_in_input_order_after_each_admission(self):
        first_entered, advance, second_entered, finish = (Event() for _ in range(4))
        completed = []
        started = []

        def run(config, job, *, wait, timeout, interrupted, admission_done):
            started.append((job, wait))
            if job == "z":
                first_entered.set()
                advance.wait(5)
                admission_done()
                finish.wait(5)
            else:
                admission_done()
                second_entered.set()
            return Result(Status.FULL)

        jobs = [("z", "z"), ("a", "a")]
        with patch("backup_manager.cli.execute_job", side_effect=run):
            batch = Thread(target=lambda: completed.append(execute_batch(
                {"execution": {"max_jobs": 2}}, jobs, timeout=None, interrupted=lambda: False,
            )))
            batch.start()
            try:
                self.assertTrue(first_entered.wait(5))
                self.assertFalse(second_entered.wait(0.2))
                advance.set()
                self.assertTrue(second_entered.wait(5))
                self.assertEqual(started, [("z", True), ("a", True)])
            finally:
                advance.set()
                finish.set()
                batch.join(10)
        self.assertFalse(batch.is_alive())
        self.assertEqual([row["job_id"] for row in completed[0].data["jobs"]], ["z", "a"])

    def test_storage_and_secret_entry_points_and_job_local_references(self):
        with TemporaryDirectory() as folder:
            metadata = Path(folder) / "local_test_providers-1.0.dist-info"
            metadata.mkdir()
            (metadata / "entry_points.txt").write_text(
                f"[backup_manager.storage]\nfixture_storage = {__name__}:InspectOnly\n"
                f"[backup_manager.secrets]\nfixture_secret = {__name__}:FixtureSecret\n",
            )
            with patch.object(sys, "path", [folder, *sys.path]):
                storage = discover("storage")
                secrets = discover("secrets")
        self.assertIsInstance(storage["fixture_storage"], InspectOnly)
        self.assertIn("linux_filesystem", storage)
        refs = {
            "primary": {"provider": "fixture_secret", "key": "first"},
            "secondary": {"provider": "fixture_secret", "key": "second"},
        }
        self.assertEqual(
            [repository_password(secrets, ref, NANOSECONDS) for ref in refs.values()], [b"first", b"second"],
        )

    def test_storage_provider_requires_inspection_and_identity_with_optional_actions(self):
        valid = discover("storage", [InspectOnly])["fixture_storage"]
        self.assertEqual(valid.capabilities, {"inspect", "resolve_identity"})
        for method in ("inspect", "resolve_identity"):
            with self.subTest(method=method), patch.object(InspectOnly, method, None):
                with self.assertRaisesRegex(ProviderError, "capabilities"):
                    discover("storage", [InspectOnly])
        with patch.object(InspectOnly, "capabilities", {"inspect", "resolve_identity", "release"}):
            with self.assertRaisesRegex(ProviderError, "capabilities"):
                discover("storage", [InspectOnly])

    def make_runner(self, **job):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        path, anchor = root / "db" / "state", root / "witness" / "identity"
        path.parent.mkdir(mode=0o700)
        anchor.parent.mkdir(mode=0o700)
        initialize(path, anchor)
        config = {
            "state": {"path": str(path), "anchor_path": str(anchor)}, "storage": {},
            "execution": {
                "max_jobs": 1, "job_timeout_seconds": 30, "operation_timeout_seconds": 10,
                "cleanup_timeout_seconds": 7, "release_timeout_seconds": 11,
            },
        }
        return config, {"job": {"id": "fixture"}, **job}

    def test_job_and_operation_overrides_bound_runtime_deadlines(self):
        config, job = self.make_runner(timeouts={"job_timeout_seconds": 8, "operation_timeout_seconds": 3})
        with patch("backup_manager.jobs.monotonic_ns", return_value=NANOSECONDS) as clock:
            runner = JobRunner(config, job, timeout=6)
            self.assertEqual(runner.deadline, 7 * NANOSECONDS)
            self.assertEqual(runner.operation_deadline(), 4 * NANOSECONDS)
            clock.return_value = 6 * NANOSECONDS
            self.assertEqual(runner.operation_deadline(), runner.deadline)
            clock.return_value = runner.deadline
            with self.assertRaises(JobStopped):
                runner.check_running()

    def test_hooks_forward_arguments_credentials_and_independent_cleanup_deadline(self):
        hook = {
            "executable": "/bin/true", "args": ["literal argument"], "cwd": "/tmp",
            "env": {"CUSTOM": "value"},
            "secrets": {"LOCAL_SECRET": {"provider": "fixture_secret", "key": "fixture-value"}},
            "user": 1234, "group": 1235, "timeout_seconds": 9,
        }
        config, job = self.make_runner(hooks={"cleanup": [hook]})
        runner = JobRunner(config, job)
        runner.secrets["fixture_secret"] = FixtureSecret()
        runner.run_id = "fixture-run"
        runner.selected = SimpleNamespace(destination="primary")
        runner.deadline = NANOSECONDS
        runner.interrupted = Mock(return_value=True)
        expired = ScopedResult(None, b"fixture-value", b"", False, "scope", stop_reason="timeout")
        with (
            patch("backup_manager.jobs.monotonic_ns", return_value=2 * NANOSECONDS),
            patch("backup_manager.jobs.run_journaled", return_value=expired) as supervise,
            patch.object(runner, "record") as record,
        ):
            self.assertFalse(runner.hooks("cleanup", cleanup=True))
        runner.interrupted.assert_not_called()
        args, kwargs = supervise.call_args
        self.assertEqual(args[-1], ("/bin/true", "literal argument"))
        self.assertEqual((kwargs["user"], kwargs["group"], kwargs["cwd"]), (1234, 1235, "/tmp"))
        self.assertEqual(kwargs["deadline"], 9)
        self.assertIsNone(kwargs["cancelled"])
        self.assertEqual(kwargs["env"]["CUSTOM"], "value")
        self.assertEqual(kwargs["env"]["LOCAL_SECRET"], "fixture-value")
        self.assertEqual(record.call_args.kwargs["code"], "HOOK_TIMEOUT")
        self.assertEqual(record.call_args.kwargs["data"]["stdout"], "[REDACTED]")

    def test_default_and_explicit_hook_credentials_resolve(self):
        self.assertEqual(credentials(None, None), {})
        self.assertEqual(credentials(os.geteuid(), os.getegid()), {})
        account = SimpleNamespace(pw_uid=os.geteuid() + 1, pw_gid=os.getegid() + 1)
        with patch("backup_manager.scope_worker.pwd.getpwnam", return_value=account) as lookup:
            self.assertEqual(credentials("fixture-user", 1235), {
                "user": account.pw_uid, "group": 1235, "extra_groups": [],
            })
        lookup.assert_called_once_with("fixture-user")

    def test_global_slot_is_held_while_destination_selection_waits(self):
        config, job = self.make_runner()
        first = JobRunner(config, job)
        second = JobRunner(config, {"job": {"id": "competitor"}})

        def wait_for_destination(_adapter):
            self.assertEqual(second.acquire_slot(wait=False), "GLOBAL_BUSY")
            with state_read(first.storage.path, first.storage.anchor) as database:
                self.assertEqual(database.execute("SELECT job_id FROM job_runs").fetchall(), [("fixture",)])
            raise DestinationRejected("NO_USABLE_DESTINATION")

        with patch.object(first, "load_restic"), patch.object(first, "select", side_effect=wait_for_destination) as select:
            self.assertEqual(first.execute().status, Status.FAILED)
        select.assert_called_once()
        self.assertIsNone(second.acquire_slot(wait=False))

    def test_success_prepares_static_source_and_runs_cleanup_before_replication(self):
        config, job = self.make_runner(destinations={"primary": {}, "secondary": [{"id": "peer"}]})
        source = Path(config["state"]["path"]).parent / "prepared-source"
        job["sources"] = [{"path": str(source)}]
        job["hooks"] = {phase: [{"executable": f"/fixture/{phase}"}] for phase in ("pre", "post", "cleanup")}
        runner = JobRunner(config, job)
        selected = SimpleNamespace(destination="primary", claim_id="fixture-claim", lease=None)
        order = []

        def invoke(_path, _anchor, _operation, _owner, argv, **kwargs):
            phase = Path(argv[0]).name
            order.append(phase)
            if phase == "pre":
                source.touch()
            return ScopedResult(0, b"", b"", False, "fixture")

        def backup(_adapter, sources):
            self.assertEqual(sources, [source])
            order.append("backup")
            runner.backup_status = "succeeded"

        def replication(_runner, _adapter, *, after):
            order.append("replication")
            return True

        self.assertFalse(source.exists())
        with (
            patch.object(runner, "load_restic"),
            patch.object(runner, "select", return_value=selected),
            patch.object(runner.storage, "release"),
            patch.object(runner.storage, "release_repositories"),
            patch.object(runner, "backup", side_effect=backup),
            patch("backup_manager.jobs.run_journaled", side_effect=invoke),
            patch("backup_manager.replication.run_replication", side_effect=replication),
        ):
            result = runner.execute()
        self.assertEqual(result.status, Status.FULL)
        self.assertEqual(order, ["pre", "backup", "post", "cleanup", "replication"])

    def test_explicit_verification_checks_only_selected_repository(self):
        config, job = self.make_runner(destinations={"primary": {}, "secondary": [{"id": "peer"}]})
        runner = JobRunner(config, job)
        binary = Binary(Path("/fixture/restic"), "fixture-sha")
        version = "restic 0.18.0 compiled with go1.24.4 on linux/amd64"
        adapter = Restic(
            binary, SimpleNamespace(binary_sha256=binary.sha256, version_line=version),
            ScopedResult(0, version.encode(), b"", False, "scope"),
        )
        repo = Repository(Path("/fixture/peer"), b"fixture")
        target = SimpleNamespace(
            registration=SimpleNamespace(key="fixture-peer"), repository=repo,
            claim_id="fixture-claim", lease=StorageLease("fixture-lease", "disk", "fixture-disk"),
        )
        with (
            patch("backup_manager.maintenance.JobRunner", return_value=runner),
            patch.object(runner, "load_restic", return_value=adapter),
            patch.object(runner, "get_destination", return_value=target) as get_destination,
            patch.object(runner, "verify_identity") as verify_identity,
            patch.object(runner.storage, "release_repositories") as release_repositories,
            patch.object(runner.storage, "release") as release_storage,
            patch.object(runner, "run_command", return_value=ScopedResult(0, b"", b"", False, "scope")) as run,
        ):
            result = run_maintenance(config, job, "verify", "peer")
        self.assertEqual(result.status, Status.FULL)
        self.assertEqual([row["destination_id"] for row in result.data["destinations"]], ["peer"])
        get_destination.assert_called_once_with(adapter, "peer", {"id": "peer"}, access_mode="VERIFY")
        verify_identity.assert_called_once_with(adapter, target)
        release_repositories.assert_called_once_with(target.claim_id)
        release_storage.assert_called_once_with(target.lease)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0].argv, ("/fixture/restic", "--no-cache", "--repo", "/fixture/peer", "check"))

    def test_failed_preparation_attempts_all_pre_and_cleanup_hooks(self):
        config, job = self.make_runner(destinations={"primary": {}})
        job["hooks"] = {
            "pre": [{"executable": "/fixture/pre-one"}, {"executable": "/fixture/pre-two"}],
            "post": [{"executable": "/fixture/post"}],
            "cleanup": [{"executable": "/fixture/cleanup-one"}, {"executable": "/fixture/cleanup-two"}],
        }
        runner = JobRunner(config, job)
        selected = SimpleNamespace(destination="primary", claim_id="fixture-claim", lease=None)
        outcomes = [
            ScopedResult(code, b"", b"", False, "fixture")
            for code in (1, 0, 1, 0)
        ]
        with (
            patch.object(runner, "load_restic"),
            patch.object(runner, "select", return_value=selected),
            patch.object(runner.storage, "release"),
            patch.object(runner.storage, "release_repositories"),
            patch.object(runner, "sources") as sources,
            patch.object(runner, "backup") as backup,
            patch("backup_manager.jobs.run_journaled", side_effect=outcomes) as invoke,
        ):
            result = runner.execute()
        self.assertEqual(result.status, Status.FAILED)
        sources.assert_not_called()
        backup.assert_not_called()
        self.assertEqual([call.args[-1][0] for call in invoke.call_args_list], [
            "/fixture/pre-one", "/fixture/pre-two", "/fixture/cleanup-one", "/fixture/cleanup-two",
        ])
        self.assertEqual(
            [(row["phase"], row["status"]) for row in result.data["phases"]],
            [("pre", "failed"), ("pre", "succeeded"), ("cleanup", "failed"), ("cleanup", "succeeded")],
        )

    def test_storage_release_dispatch_has_independent_budget_after_job_timeout(self):
        config, job = self.make_runner(timeouts={"job_timeout_seconds": 2})
        config["storage"]["disk"] = {"provider": "fixture", "lifecycle": "on_demand", "config": {}}
        with patch("backup_manager.jobs.monotonic_ns", return_value=NANOSECONDS):
            runner = JobRunner(config, job)
        coordinator = runner.storage
        identity = StorageIdentity("disk-uuid", "disk-uuid", {}, True)
        with coordinator.write() as database:
            disk = reconcile_identity(database, "disk", identity, None)
            claim = admit_storage_claim(database, disk, coordinator.owner)
            database.execute("INSERT INTO storage_ownership VALUES ('disk', 1, 1, '{}')")
        with (
            patch("backup_manager.storage_coordinator.monotonic_ns", return_value=5 * NANOSECONDS),
            patch("backup_manager.storage_coordinator.start_dispatch") as dispatch,
        ):
            coordinator.release(StorageLease(claim, "disk", disk.coordination_id))
        dispatch.assert_called_once()
        args = dispatch.call_args.args
        self.assertEqual(args[2].kind, "release")
        self.assertEqual(args[6].deadline_monotonic_ns, 16 * NANOSECONDS)

    def test_registered_destinations_cannot_share_restic_identity(self):
        config, job = self.make_runner()
        coordinator = JobRunner(config, job).storage
        identity = StorageIdentity("disk-uuid", "disk-uuid", {}, True)
        insert = """INSERT INTO repository_registrations
                    (repository_key, job_id, destination_id, restic_repository_id, repo_generation,
                     approved_at, storage_id, repository_path)
                    VALUES (?, 'fixture', ?, ?, 1, 'now', 'disk', ?)"""
        with coordinator.write() as database:
            reconcile_identity(database, "disk", identity, None)
            database.execute(insert, ("one", "primary", "restic-one", "/disk/one"))
            with self.assertRaises(sqlite3.IntegrityError):
                database.execute(insert, ("two", "peer", "restic-one", "/disk/two"))
            database.execute(insert, ("two", "peer", "restic-two", "/disk/two"))

    def test_acquisition_dispatch_uses_global_budget_and_fences_unconfirmed_worker(self):
        config, job = self.make_runner(timeouts={"job_timeout_seconds": 2})
        config["execution"]["acquire_timeout_seconds"] = 13
        config["storage"]["disk"] = {"provider": "fixture", "lifecycle": "on_demand", "config": {}}
        with patch("backup_manager.jobs.monotonic_ns", return_value=NANOSECONDS):
            runner = JobRunner(config, job)
        coordinator = runner.storage
        identity = StorageIdentity("disk-uuid", "disk-uuid", {}, True)
        absent = StorageInspection(StorageState.UNAVAILABLE, False, "/fixture", identity, None)
        process = Mock()
        process.poll.return_value = 1
        with (
            patch.object(coordinator, "observe", return_value=(absent, identity)),
            patch("backup_manager.storage_coordinator.monotonic_ns", return_value=NANOSECONDS),
            patch("backup_manager.storage_coordinator.start_dispatch", return_value=process) as dispatch,
        ):
            with self.assertRaisesRegex(StorageUnavailable, "dispatcher failed"):
                coordinator.acquire("disk", deadline=runner.deadline)
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.args[6].deadline_monotonic_ns, 14 * NANOSECONDS)
        with coordinator.write() as database:
            disk = reconcile_identity(database, "disk", identity, None)
            view = lifecycle_view(database, disk.coordination_id)
            self.assertEqual(view[0][1:], ("acquire", "RECOVERY_REQUIRED"))
            self.assertIsNone(admit_storage_claim(database, disk, coordinator.owner))

    def test_job_deadline_withdraws_only_its_shared_acquisition_claim(self):
        config, job = self.make_runner()
        config["storage"]["disk"] = {"provider": "fixture", "lifecycle": "on_demand", "config": {}}
        leader = JobRunner(config, job).storage
        follower = JobRunner(config, {"job": {"id": "follower"}}).storage
        identity = StorageIdentity("disk-uuid", "disk-uuid", {}, True)
        with leader.write() as database:
            disk = reconcile_identity(database, "disk", identity, None)
            attempt = plan_acquisition(database, disk, leader.owner, (), available=False, on_demand=True)
        absent = StorageInspection(StorageState.UNAVAILABLE, False, "/fixture", identity, None)
        deadline = 2 * NANOSECONDS
        clock = Mock(return_value=NANOSECONDS)

        def expire(_seconds):
            with leader.read() as database:
                waiting = database.execute("SELECT count(*) FROM storage_claims WHERE state = 'WAITING'").fetchone()
                self.assertEqual(waiting, (2,))
            clock.return_value = deadline

        with (
            patch.object(follower, "observe", return_value=(absent, identity)),
            patch.object(follower, "dispatch") as dispatch,
            patch("backup_manager.storage_coordinator.monotonic_ns", clock),
            patch("backup_manager.storage_coordinator.sleep", side_effect=expire),
        ):
            with self.assertRaisesRegex(StorageUnavailable, "timed out"):
                follower.acquire("disk", deadline=deadline)
        dispatch.assert_not_called()
        with leader.read() as database:
            self.assertEqual(lifecycle_view(database, disk.coordination_id), (
                (attempt.operation_id, "acquire", "ACQUIRING"),
            ))
            self.assertEqual(dict(database.execute("SELECT owner_id, state FROM storage_claims")), {
                leader.owner: "WAITING", follower.owner: "RELEASED",
            })

    def test_primary_acquisition_failure_allows_fallback_and_is_rechecked_for_replication(self):
        primary = {
            "storage": "primary-disk", "repository": "/fixture/primary",
            "password": {"provider": "fixture_secret", "key": "first"},
        }
        secondary = {
            "id": "secondary", "storage": "secondary-disk", "repository": "/fixture/secondary",
            "password": {"provider": "fixture_secret", "key": "second"},
        }
        config, job = self.make_runner(destinations={"primary": primary, "secondary": [secondary]})
        runner = JobRunner(config, job)
        runner.secrets["fixture_secret"] = FixtureSecret()
        self.assertIsNone(runner.acquire_slot(wait=False))
        adapter = SimpleNamespace(profile=SimpleNamespace(binary_sha256="fixture", native_overlap=frozenset()))
        primary_lease = StorageLease("p", "primary-disk", "p")
        secondary_lease = StorageLease("s", "secondary-disk", "s")
        registrations = {
            name: Registration(name, name + "-id", 1, target["storage"], Path(target["repository"]))
            for name, target in (("primary", primary), ("secondary", secondary))
        }
        with (
            patch("backup_manager.jobs.registration", side_effect=lambda _db, _job, name: registrations[name]),
            patch.object(runner.storage, "acquire", side_effect=[
                StorageUnavailable("disconnected"), secondary_lease, primary_lease,
            ]) as acquire,
            patch("backup_manager.jobs.inspect_repository", return_value={}),
            patch.object(runner, "verify_identity"),
            patch.object(runner.storage, "reserve_repositories") as reserve,
            patch.object(runner.storage, "release_repositories"),
        ):
            runner.selected = runner.select(adapter)
            self.assertEqual(runner.selected.destination, "secondary")
            replication = Replication(runner, adapter)
            replication.discover()
        self.assertEqual(replication.source, "secondary")
        self.assertEqual(replication.repos["primary"].lease, primary_lease)
        self.assertEqual([call.args[0] for call in acquire.call_args_list], [
            "primary-disk", "secondary-disk", "primary-disk",
        ])
        self.assertEqual([call.args[1][0].access_mode for call in reserve.call_args_list], ["BACKUP", "READ"])
        with runner.storage.read() as database:
            self.assertEqual(database.execute(
                "SELECT target_id, status, error_code FROM phase_results WHERE phase = 'destination_selection' "
                "ORDER BY rowid",
            ).fetchall(), [
                ("primary", "unavailable", "STORAGE_UNAVAILABLE"),
                ("secondary", "selected", None),
            ])

    def test_repository_disconnect_stops_identity_read_without_reacquiring(self):
        config, job = self.make_runner()
        runner = JobRunner(config, job)
        identity = StorageIdentity("disk-uuid", "disk-uuid", {}, True)
        absent = StorageInspection(StorageState.UNAVAILABLE, False, "/fixture", identity, None)
        selected = SimpleNamespace(
            lease=StorageLease("claim", "disk", "disk-uuid"),
            repository=Repository(Path("/fixture/repo"), b"fixture"),
        )
        with (
            patch.object(runner.storage, "observe", return_value=(absent, identity)) as observe,
            patch.object(runner.storage, "acquire") as reacquire,
            patch.object(runner, "run_command") as command,
        ):
            with self.assertRaisesRegex(DestinationRejected, "REPOSITORY_DISCONNECTED"):
                runner.verify_identity(None, selected)
        observe.assert_called_once()
        reacquire.assert_not_called()
        command.assert_not_called()
