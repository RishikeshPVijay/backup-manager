import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from backup_manager.config import load_global, load_job, validator


EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


class FoundationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.global_path = self.root / "config.toml"
        self.jobs = self.root / "jobs"
        self.jobs.mkdir()
        self.job_path = self.jobs / "immich.toml"
        shutil.copyfile(EXAMPLES / "config.toml", self.global_path)
        shutil.copyfile(EXAMPLES / "immich.toml", self.job_path)

    def cli(self, *args, exit_code):
        process = subprocess.run(
            [sys.executable, "-m", "backup_manager.cli", "--json",
             "--config", str(self.global_path), *args],
            capture_output=True, text=True, check=False, timeout=10,
        )
        self.assertEqual(process.returncode, exit_code, process.stderr)
        self.assertEqual(process.stderr, "")
        payload = json.loads(process.stdout)
        validator("cli-result").validate(payload)
        return payload

    def test_valid_examples_and_no_storage_access(self):
        files_before = set(self.root.rglob("*"))
        payload = self.cli("config", "validate", "--all", exit_code=0)
        self.assertEqual(payload["status"], "full")
        self.assertEqual(set(self.root.rglob("*")), files_before)

    def test_reports_multiple_errors_without_echoing_values(self):
        original = self.job_path.read_text()
        changed = original.replace('id = "immich"', 'id = "immich"\npassword = "SENTINEL"')
        changed = changed.replace('keep_daily = 7', 'keep_daily = false')
        changed = changed.replace('path = "/srv/immich/library"', 'path = "relative"')
        self.job_path.write_text(changed)
        payload = self.cli("config", "validate", "--job", "immich", exit_code=4)
        self.assertEqual(
            {item["context"]["field"] for item in payload["errors"]},
            {"job", "retention.keep_daily", "sources.0.path"},
        )
        self.assertNotIn("SENTINEL", json.dumps(payload))

    def test_malformed_toml_does_not_echo_content(self):
        self.job_path.write_text("password = SENTINEL")
        payload = self.cli("config", "validate", "--all", exit_code=4)
        self.assertEqual(payload["errors"][0]["code"], "CONFIG_READ")
        self.assertNotIn("SENTINEL", json.dumps(payload))

    def test_destinations_unique_and_reject_traversal(self):
        changed = self.job_path.read_text().replace('id = "replica"', 'id = "primary"')
        self.job_path.write_text(changed.replace("immich-replica", "immich/../immich"))
        payload = self.cli("config", "validate", "--all", exit_code=4)
        self.assertEqual(
            {item["code"] for item in payload["errors"]},
            {"DUPLICATE_DESTINATION", "REPOSITORY_PATH"},
        )

    def test_snapshot_is_immutable_and_independent_of_disk(self):
        document = load_job(self.job_path, load_global(self.global_path))
        self.assertEqual(document.errors, ())
        self.job_path.write_text("")
        self.assertEqual(document.data["job"]["id"], "immich")
        with self.assertRaises(TypeError):
            document.data["sources"][0]["path"] = "/other"

    def test_invalid_flag_combinations(self):
        for args in (
            ("batch", "run"), ("batch", "run", "--all", "immich"),
            ("job", "run", "immich", "--dry-run", "--preflight"),
            ("history", "list", "--limit", "1001"),
        ):
            with self.subTest(args=args):
                self.assertEqual(self.cli(*args, exit_code=4)["status"], "invalid")

    def test_init_requires_existing_safe_state_without_creating_files(self):
        files_before = set(self.root.rglob("*"))
        payload = self.cli("repository", "init", "immich", "primary", "--yes", exit_code=8)
        self.assertEqual(payload["status"], "unsafe")
        self.assertEqual(set(self.root.rglob("*")), files_before)

    def test_version(self):
        self.assertEqual(self.cli("version", exit_code=0)["data"]["version"], "0.1.0")

    def test_confirmed_timeout_and_retention_configuration(self):
        self.global_path.write_text(self.global_path.read_text() + "\n[logging]\nhistory_count = 25\n")
        self.job_path.write_text(
            self.job_path.read_text() + "prune = true\n[timeouts]\njob_timeout_seconds = 20\n"
        )
        self.assertEqual(self.cli("config", "validate", "--all", exit_code=0)["errors"], [])
        with self.job_path.open("a") as stream:
            stream.write("operation_timeout_seconds = 9999\nrelease_timeout_seconds = 1\n")
        codes = {item["code"] for item in self.cli("config", "validate", "--all", exit_code=4)["errors"]}
        self.assertEqual(codes, {"CONFIG_SCHEMA", "JOB_TIMEOUT_CAP"})

    def test_prune_requires_keep_selector(self):
        content = self.job_path.read_text().split("[retention]")[0]
        self.job_path.write_text(content + "[retention]\nprune = true\n")
        payload = self.cli("config", "validate", "--all", exit_code=4)
        self.assertEqual(payload["errors"][0]["context"]["field"], "retention")

    def test_nested_storage_configuration_rejects_unknown_fields(self):
        with self.global_path.open("a") as stream:
            stream.write('unknown = "SENTINEL"\n')
        payload = self.cli("config", "validate", exit_code=4)
        self.assertEqual([item["code"] for item in payload["errors"]], ["STORAGE_CONFIG"])
        self.assertNotIn("SENTINEL", json.dumps(payload))

    def test_unknown_storage_provider_rejected(self):
        self.global_path.write_text(
            self.global_path.read_text().replace('"linux_filesystem"', '"unknown_provider"'),
        )
        payload = self.cli("config", "validate", exit_code=4)
        self.assertEqual([item["code"] for item in payload["errors"]], ["UNKNOWN_STORAGE_PROVIDER"])

    def test_state_init_requires_confirmation_and_state_check_is_safe(self):
        db_dir = self.root / "state"
        db_dir.mkdir(mode=0o700)
        witness_dir = self.root / "witness"
        witness_dir.mkdir(mode=0o700)
        database = db_dir / "state.sqlite3"
        self.global_path.write_text(self.global_path.read_text().replace(
            "/var/lib/backup-manager/state.sqlite3", str(database),
        ).replace("/var/lib/backup-manager-witness/state.identity", str(witness_dir / "state.identity")))
        self.assertEqual(self.cli("state", "check", exit_code=8)["status"], "unsafe")
        self.assertEqual(self.cli("state", "init", exit_code=4)["status"], "invalid")
        self.assertFalse(database.exists())
        initialized = self.cli("state", "init", "--yes", exit_code=0)
        checked = self.cli("state", "check", exit_code=0)
        self.assertEqual(checked["data"], initialized["data"])
