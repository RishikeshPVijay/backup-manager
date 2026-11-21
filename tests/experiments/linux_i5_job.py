"""End-to-end I5 fixtures with disposable Restic repositories and user scopes."""

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep

from backup_manager.state import state_read


def quote(value):
    return json.dumps(str(value))


class Fixture:
    def __init__(self, root):
        self.root = root
        self.config = root / "config.toml"
        self.db = root / "state" / "state.db"
        witness = self.anchor = root / "witness" / "identity"
        self.storage = root / "storage"
        for directory in (root / "jobs", self.db.parent, witness.parent, self.storage):
            directory.mkdir(mode=0o700)
        (self.storage / "mounted").touch()
        self.source = root / "source"
        self.source.write_text("disposable backup content")
        self.absent = root / "missing"
        self.marker = root / "hooks"
        self.env = {
            **os.environ, "PYTHONPATH": str(root), "PRIMARY_SECRET": secrets.token_hex(20),
            "SECONDARY_SECRET": secrets.token_hex(20), "HOOK_SECRET": secrets.token_hex(20),
            "PEER_SECRET": secrets.token_hex(20), "THIRD_SECRET": secrets.token_hex(20),
        }
        entry = root / "fixture_storage-1.0.dist-info"
        entry.mkdir()
        (entry / "entry_points.txt").write_text("[backup_manager.storage]\nfixture_storage = lifecycle_provider:FixtureProvider\n")
        shutil.copyfile(Path(__file__).parent / "fixtures" / "lifecycle_provider.py", root / "lifecycle_provider.py")
        self.config.write_text(f"""
schema_version = 1
[state]
path = {quote(self.db)}
anchor_path = {quote(witness)}
[execution]
max_jobs = 1
job_timeout_seconds = 50
operation_timeout_seconds = 15
cleanup_timeout_seconds = 5
release_timeout_seconds = 5
acquire_timeout_seconds = 5
[storage.disk]
provider = "fixture_storage"
lifecycle = "always_on"
[storage.disk.config]
root = {quote(self.storage)}
""")
        self.cli("state", "init", "--yes")
        for repo in ("primary", "secondary", "third", "peer"):
            self.restic(repo, "init", "--repository-version", "2")
        self.job(secondary=True)
        self.cli("repository", "register", "fixture", "primary", "--yes")
        self.cli("repository", "register", "fixture", "secondary", "--yes")
        self.cli("repository", "register", "fixture", "third", "--yes")

    def args(self, *args):
        return [sys.executable, "-m", "backup_manager.cli", "--json", "--config", str(self.config), *args]

    def parse(self, process, expected):
        assert process.returncode == expected, (process.returncode, process.stdout, process.stderr)
        assert not process.stderr, process.stderr
        for key in ("PRIMARY_SECRET", "SECONDARY_SECRET", "HOOK_SECRET", "PEER_SECRET", "THIRD_SECRET"):
            assert self.env[key] not in process.stdout
        payload = json.loads(process.stdout)
        assert payload["schema_version"] == 1
        return payload

    def cli(self, *args, expected=0, overrides=None):
        process = subprocess.run(self.args(*args), env={**self.env, **(overrides or {})},
                                 capture_output=True, text=True, check=False, timeout=80)
        return self.parse(process, expected)

    def restic(self, repo, *args):
        environment = {"PATH": os.defpath, "RESTIC_PASSWORD": self.env[f"{repo.upper()}_SECRET"]}
        return subprocess.run(
            ["/usr/bin/restic", "--no-cache", "--repo", str(self.storage / repo), *args],
            env=environment, capture_output=True, text=True, check=True, timeout=20,
        ).stdout

    def job(self, *, secondary=False, sources=None, hooks=(), job_id="fixture", primary_repo="primary"):
        sources = sources if sources is not None else [(self.source, True)]
        content = f"""
schema_version = 1
[job]
id = {quote(job_id)}
enabled = true
"""
        for path, required in sources:
            content += f"""
[[sources]]
path = {quote(path)}
required = {str(required).lower()}
"""
        content += f"""
[destinations.primary]
storage = "disk"
repository = {quote(self.storage / primary_repo)}
[destinations.primary.password]
provider = "environment"
variable = "{primary_repo.upper()}_SECRET"
"""
        if secondary:
            content += f"""
[[destinations.secondary]]
id = "secondary"
storage = "disk"
repository = {quote(self.storage / "secondary")}
[destinations.secondary.password]
provider = "environment"
variable = "SECONDARY_SECRET"
"""
        if secondary:
            content += f"""
[[destinations.secondary]]
id = "third"
storage = "disk"
repository = {quote(self.storage / "third")}
[destinations.secondary.password]
provider = "environment"
variable = "THIRD_SECRET"
"""
        content += "\n[retention]\nkeep_last = 2\n"
        for phase, label, exit_code, delay in hooks:
            command = (
                "import os,pathlib,sys,time;"
                "p=pathlib.Path(sys.argv[1]);"
                "f=p.open('a'); f.write(sys.argv[2]+'\\n'); f.close();"
                "assert os.environ['BACKUP_MANAGER_PHASE']==sys.argv[3];"
                "print(os.environ['HOOK_MATERIAL']);"
                "pathlib.Path(sys.argv[6]).touch() if sys.argv[2]=='prepare' else None;"
                "time.sleep(float(sys.argv[4]));sys.exit(int(sys.argv[5]))"
            )
            argv = ["-c", command, str(self.marker), label, phase, str(delay), str(exit_code),
                    str(self.root / "prepared")]
            content += f"""
[[hooks.{phase}]]
executable = {quote(sys.executable)}
args = {json.dumps(argv)}
timeout_seconds = {1 if label == "timeout" else 12}
user = {os.geteuid()}
group = {os.getegid()}
[hooks.{phase}.secrets.HOOK_MATERIAL]
provider = "environment"
variable = "HOOK_SECRET"
"""
        (self.root / "jobs" / f"{job_id}.toml").write_text(content)

    def history(self, outcome):
        return self.cli("history", "show", outcome["operation_id"])["data"]

    def seen(self, phase):
        return self.marker.read_text().splitlines() == phase


def await_condition(predicate):
    end = monotonic() + 15
    while monotonic() < end:
        if predicate():
            return
        sleep(0.1)
    raise AssertionError("Fixture condition did not become true.")


def main():
    with TemporaryDirectory(prefix="bm-i5-job-") as temporary:
        fixture = Fixture(Path(temporary))
        fixture.job(sources=[(fixture.root / "prepared", True), (fixture.absent, False)], hooks=[
            ("pre", "prepare", 0, 0), ("post", "post-failed", 1, 0), ("cleanup", "cleanup", 0, 0),
        ])
        password_file = fixture.root / "repo-password"
        password_file.write_text("\u00a0" + fixture.env["PRIMARY_SECRET"] + "\u2009\n")
        password_file.chmod(0o600)
        job_file = fixture.root / "jobs" / "fixture.toml"
        job_file.write_text(job_file.read_text().replace(
            'provider = "environment"\nvariable = "PRIMARY_SECRET"',
            f'provider = "file"\npath = {quote(password_file)}',
        ))
        outcome = fixture.cli("job", "run", "fixture")
        history = fixture.history(outcome)
        assert outcome["data"]["backup"] == "succeeded" and outcome["data"]["destination_id"] == "primary"
        assert fixture.seen(["prepare", "post-failed", "cleanup"])
        assert any(row["error_code"] == "OPTIONAL_SOURCE_MISSING" for row in history["phases"])
        assert any(row["phase"] == "post" and row["status"] == "failed" for row in history["phases"])
        assert any("[REDACTED]" in row["data"].get("stdout", "") for row in history["phases"])
        originals = len(json.loads(fixture.restic("primary", "snapshots", "--json")))

        fixture.marker.unlink()
        fixture.job(sources=[(fixture.absent, True)], hooks=[
            ("pre", "first-failed", 1, 0), ("pre", "second", 0, 0),
            ("post", "post", 0, 0), ("cleanup", "cleanup", 0, 0),
        ])
        failed = fixture.cli("job", "run", "fixture", expected=3)
        assert fixture.seen(["first-failed", "second", "cleanup"])
        assert not any(row["phase"] in {"sources", "backup", "post"} for row in fixture.history(failed)["phases"])

        for required, code in ((True, "REQUIRED_SOURCE_MISSING"), (False, "NO_SOURCES_AVAILABLE")):
            fixture.job(sources=[(fixture.absent, required)])
            no_sources = fixture.cli("job", "run", "fixture", expected=3)
            assert no_sources["errors"][0]["code"] == code
        assert len(json.loads(fixture.restic("primary", "snapshots", "--json"))) == originals

        fixture.job(secondary=True)
        fallback = fixture.cli("job", "run", "fixture", expected=2, overrides={"PRIMARY_SECRET": "wrong-fixture"})
        assert fallback["data"]["backup"] == "succeeded" and fallback["data"]["destination_id"] == "secondary"
        # An I6 copy may appear here. The I5 selection invariant is that
        # this later secondary never receives its own backup snapshot.
        assert all(item.get("original") for item in json.loads(fixture.restic("third", "snapshots", "--json")))
        assert len(json.loads(fixture.restic("primary", "snapshots", "--json"))) == originals

        primary = fixture.storage / "primary"
        saved = fixture.root / "saved-primary"
        primary.rename(saved)
        try:
            fixture.restic("primary", "init", "--repository-version", "2")
            mismatch = fixture.cli("job", "run", "fixture", expected=2)
            assert mismatch["data"]["destination_id"] == "secondary"
            assert any(row["error_code"] == "REPOSITORY_ID_MISMATCH" for row in mismatch["data"]["phases"])
            assert not json.loads(fixture.restic("primary", "snapshots", "--json"))
        finally:
            transient = fixture.root / "disposable-replacement"
            if primary.exists():
                primary.rename(transient)
            saved.rename(primary)

        fixture.marker.unlink()
        revoked = fixture.root / "unreadable"
        revoked.write_text("fixture access failure")
        revoked.chmod(0)
        try:
            fixture.job(secondary=True, sources=[(fixture.source, True), (revoked, True)], hooks=[
                ("post", "post", 0, 0), ("cleanup", "cleanup", 0, 0),
            ])
            second_before = fixture.restic("secondary", "snapshots", "--json")
            partial = fixture.cli("job", "run", "fixture", expected=3)
            assert partial["data"]["backup"] == "failed" and partial["data"]["destination_id"] == "primary"
            assert fixture.restic("secondary", "snapshots", "--json") == second_before
            assert fixture.seen(["cleanup"])
            assert any(phase["phase"] == "backup" and phase["data"].get("exit_code") == 3
                       for phase in fixture.history(partial)["phases"])
        finally:
            revoked.chmod(0o600)

        fixture.marker.unlink()
        fixture.job(hooks=[("pre", "timeout", 0, 8), ("pre", "following", 0, 0), ("cleanup", "cleanup", 0, 0)])
        timed = fixture.cli("job", "run", "fixture", expected=3)
        assert fixture.seen(["timeout", "following", "cleanup"])
        assert any(phase["error_code"] == "HOOK_TIMEOUT" for phase in fixture.history(timed)["phases"])

        fixture.marker.unlink()
        fixture.job(hooks=[("pre", "waiting", 0, 25), ("cleanup", "cleanup", 0, 0)])
        fixture.job(job_id="other")
        with subprocess.Popen(fixture.args("job", "run", "fixture"), env=fixture.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as running:
            try:
                await_condition(lambda: fixture.marker.exists())
                assert fixture.cli("job", "run", "fixture", expected=5)["errors"][0]["code"] == "JOB_BUSY"
                assert fixture.cli("job", "run", "other", expected=5)["errors"][0]["code"] == "GLOBAL_BUSY"
                active, = fixture.cli("operation", "list", "--active-only")["data"]["runs"]
                fixture.cli("operation", "cancel", active["run_id"], "--yes")
                stdout, stderr = running.communicate(timeout=30)
                cancelled = fixture.parse(subprocess.CompletedProcess([], running.returncode, stdout, stderr), 6)
                assert cancelled["operation_id"] == active["run_id"]
                assert fixture.seen(["waiting", "cleanup"])
            finally:
                if running.poll() is None:
                    running.terminate()
        fixture.marker.unlink()
        fixture.job(hooks=[("pre", "crash-window", 0, 20), ("cleanup", "cleanup", 0, 0)])
        unit = None
        with subprocess.Popen(fixture.args("job", "run", "fixture"), env=fixture.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as running:
            try:
                await_condition(lambda: fixture.marker.exists())
                active, = fixture.cli("operation", "list", "--active-only")["data"]["runs"]
                with state_read(fixture.db, fixture.anchor, wait=5) as database:
                    receipt = database.execute(
                        "SELECT process_identity_json FROM command_executions WHERE state = 'LAUNCHED' ORDER BY rowid DESC",
                    ).fetchone()[0]
                unit = Path(json.loads(receipt)["containment_id"]).name
                running.kill()
                assert running.wait(timeout=10) < 0
                fixture.cli("job", "run", "fixture", expected=5)
                assert fixture.cli("history", "show", active["run_id"])["data"]["state"] == "RECOVERY_REQUIRED"
                subprocess.run(["systemctl", "--user", "stop", unit], check=True, timeout=10)
                fixture.job()
                fresh = fixture.cli("job", "run", "fixture")
                assert fresh["operation_id"] != active["run_id"]
                assert fixture.cli("history", "show", active["run_id"])["data"]["state"] == "INTERRUPTED"
                assert fixture.seen(["crash-window"])
            finally:
                if unit:
                    subprocess.run(["systemctl", "--user", "stop", unit], check=False, timeout=10,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if running.poll() is None:
                    running.terminate()

        fixture.marker.unlink()
        fixture.job(job_id="peer", primary_repo="peer")
        fixture.cli("repository", "register", "peer", "primary", "--yes")
        fixture.job(hooks=[("pre", "batch-wait", 0, 3)])
        (fixture.root / "jobs" / "bad.toml").write_text("invalid toml")
        command = fixture.args("batch", "run", "fixture", "peer", "bad")
        with subprocess.Popen(command, env=fixture.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as batch:
            try:
                await_condition(lambda: fixture.marker.exists())
                fixture.job(job_id="peer", primary_repo="peer", sources=[(fixture.absent, True)])
                stdout, stderr = batch.communicate(timeout=45)
                payload = fixture.parse(subprocess.CompletedProcess([], batch.returncode, stdout, stderr), 3)
                assert [(row["job_id"], row["status"]) for row in payload["data"]["jobs"]] == [
                    ("fixture", "full"), ("peer", "full"), ("bad", "invalid"),
                ]
            finally:
                if batch.poll() is None:
                    batch.terminate()
        assert fixture.cli("job", "run", "peer", expected=3)["errors"][0]["code"] == "REQUIRED_SOURCE_MISSING"

        fixture.marker.unlink()
        fixture.config.write_text(fixture.config.read_text().replace("max_jobs = 1", "max_jobs = 2"))
        fixture.job(hooks=[("pre", "parallel-first", 0, 4)])
        fixture.job(job_id="peer", primary_repo="peer", hooks=[("pre", "parallel-second", 0, 4)])
        with subprocess.Popen(fixture.args("batch", "run", "fixture", "peer"), env=fixture.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as batch:
            try:
                await_condition(lambda: fixture.marker.exists() and len(fixture.marker.read_text().splitlines()) == 2)
                assert len(fixture.cli("operation", "list", "--active-only")["data"]["runs"]) == 2
                stdout, stderr = batch.communicate(timeout=45)
                passed = fixture.parse(subprocess.CompletedProcess([], batch.returncode, stdout, stderr), 0)
                assert [row["status"] for row in passed["data"]["jobs"]] == ["full", "full"]
            finally:
                if batch.poll() is None:
                    batch.terminate()

        fixture.job(job_id="unregistered", primary_repo="missing")
        rejected = fixture.cli("job", "run", "unregistered", expected=3)
        assert rejected["errors"][0]["code"] == "NO_USABLE_DESTINATION"
        assert not (fixture.storage / "missing").exists()
        for name in ("PRIMARY_SECRET", "SECONDARY_SECRET", "HOOK_SECRET", "PEER_SECRET", "THIRD_SECRET"):
            assert fixture.env[name].encode() not in fixture.db.read_bytes()
        assert (fixture.storage / "mounted").exists()
        with state_read(fixture.db, fixture.anchor, wait=5) as database:
            assert database.execute("SELECT count(*) FROM repository_claim_sets WHERE state != 'RELEASED'").fetchone() == (0,)
    print("I5 verified real backup, hooks, redaction, source failures, fixed backup target, fallback, admission, cancellation, crash recovery, frozen batch and history.")


if __name__ == "__main__":
    main()
