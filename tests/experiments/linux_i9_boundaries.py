"""Exercise automatic and explicit CLI paths at release safety boundaries."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic_ns

from acceptance_trace import record
from backup_manager.config import load_global
from backup_manager.state import state_read
from backup_manager.storage_coordinator import StorageCoordinator

from linux_i5_job import Fixture, await_condition


def stale_lock(fixture, primary):
    lock_path = primary / "locks"
    assert not list(lock_path.iterdir())
    env = {"PATH": os.defpath, "RESTIC_PASSWORD": fixture.env["PRIMARY_SECRET"]}
    command = [shutil.which("restic"), "--no-cache", "--repo", str(primary),
               "backup", "--stdin", "--stdin-filename", "disposable"]
    with subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL) as locking:
        try:
            await_condition(lambda: any(lock_path.iterdir()) and locking.poll() is None)
        finally:
            if locking.poll() is None:
                locking.kill()
            locking.communicate(timeout=10)
    before = {item.name: item.read_bytes() for item in lock_path.iterdir()}
    assert before
    verified = fixture.cli("verify", "run", "fixture", "--destination", "primary", expected=3)
    command_result, = verified["data"]["destinations"][0]["operations"]
    assert command_result["exit_code"] == 11
    assert {item.name: item.read_bytes() for item in lock_path.iterdir()} == before
    fixture.cli("repository", "unlock", "fixture", "primary", "--yes")
    assert not list(lock_path.iterdir())


def storage_identity_cannot_be_silently_reapproved(fixture):
    replacement = fixture.storage / "replacement-identity"
    replacement.write_text("replacement-disk")
    try:
        changed = fixture.cli("storage", "recover", "disk", "--yes", expected=8)
        assert changed["errors"]
        with state_read(fixture.db, fixture.anchor, wait=5) as database:
            row = database.execute(
                "SELECT expected_identity, identity_generation FROM storage_resources WHERE resource_id = 'disk'",
            ).fetchone()
            assert row == ("fixture-disk", 1)
            record(database, "D89-D90", {"changed_storage": "fenced", "stored_identity": row})
    finally:
        replacement.unlink()


def hold_storage(config, ready, release):
    document = load_global(config)
    assert not document.errors
    settings = document.data
    coordinator = StorageCoordinator(
        Path(settings["state"]["path"]), Path(settings["state"]["anchor_path"]), settings["storage"],
        acquire_seconds=10, release_seconds=10,
    )
    lease = coordinator.acquire("disk", deadline=monotonic_ns() + 10_000_000_000)
    ready.touch()
    from time import sleep

    deadline = monotonic_ns() + 60_000_000_000
    while not release.exists() and monotonic_ns() < deadline:
        sleep(0.05)
    assert release.exists()
    coordinator.release(lease)


def later_release_failure(fixture):
    config = fixture.config.read_text()
    fixture.config.write_text(config.replace('lifecycle = "always_on"', 'lifecycle = "on_demand"'))
    marker = fixture.storage / "mounted"
    ready, release = fixture.root / "lease-ready", fixture.root / "lease-release"
    marker.unlink()
    (fixture.storage / "fail-release").touch()
    command = [sys.executable, __file__, "--hold", str(fixture.config), str(ready), str(release)]
    with subprocess.Popen(command, env=fixture.env) as peer:
        try:
            await_condition(ready.exists)
            completed = fixture.cli("job", "run", "fixture")
            with state_read(fixture.db, fixture.anchor, wait=5) as database:
                assert database.execute("SELECT 1 FROM storage_operations WHERE kind = 'release'").fetchone() is None
            release.touch()
            assert peer.wait(timeout=15) == 0

            def release_failed():
                with state_read(fixture.db, fixture.anchor, wait=5) as database:
                    row = database.execute("SELECT state FROM storage_operations WHERE kind = 'release'").fetchone()
                    return row == ("RECOVERY_REQUIRED",)

            await_condition(release_failed)
            assert fixture.history(completed)["overall_outcome"] == "full" and marker.exists()
            assert (fixture.storage / "actions").read_text().splitlines() == ["acquire", "release"]
            with state_read(fixture.db, fixture.anchor, wait=5) as database:
                record(database, "AT-018", {"earlier_job": "full", "later_release": "RECOVERY_REQUIRED"})
        finally:
            if peer.poll() is None:
                peer.kill()


def main():
    with TemporaryDirectory(prefix="bm-i9-boundaries-") as temporary:
        fixture = Fixture(Path(temporary))
        fixture.job()
        config = fixture.config.read_text()

        candidate = fixture.root / "unsupported-restic"
        shutil.copyfile("/usr/bin/true", candidate)
        candidate.chmod(0o700)
        fixture.config.write_text(config + f"\n[restic]\nexecutable = {json.dumps(str(candidate))}\n")
        unsupported = fixture.cli("job", "run", "fixture", expected=7)
        assert unsupported["errors"][0]["code"] == "RESTIC_UNSUPPORTED"
        assert fixture.restic("primary", "snapshots", "--json").strip() in {"[]", "null"}
        fixture.config.write_text(config)

        # A missing destination directory is never created by automatic work.
        primary = fixture.storage / "primary"
        parked = fixture.root / "parked-repository"
        primary.rename(parked)
        absent = fixture.cli("job", "run", "fixture", expected=3)
        assert absent["errors"][0]["code"] == "NO_USABLE_DESTINATION"
        assert not primary.exists()
        parked.rename(primary)
        stale_lock(fixture, primary)

        invalid = fixture.root / "jobs" / "fixture.toml"
        original = invalid.read_text()
        invalid.write_text(original + "\nunknown_fixture_field = 1\n")
        prior = fixture.db.read_bytes(), fixture.anchor.read_bytes()
        rejected = fixture.cli("config", "validate", "--job", "fixture", expected=4)
        assert rejected["errors"]
        assert prior == (fixture.db.read_bytes(), fixture.anchor.read_bytes())
        invalid.write_text(original)

        storage_identity_cannot_be_silently_reapproved(fixture)

        # State corruption blocks admission before storage lifecycle or Restic.
        inventory = fixture.restic("primary", "snapshots", "--json")
        fixture.db.write_bytes(b"corrupt disposable database")
        witness = fixture.anchor.read_bytes()
        corrupt = fixture.cli("job", "run", "fixture", expected=8)
        assert corrupt["errors"][0]["code"] == "STATE_UNSAFE"
        assert fixture.anchor.read_bytes() == witness
        assert fixture.db.read_bytes() == b"corrupt disposable database"
        assert fixture.restic("primary", "snapshots", "--json") == inventory
        assert not (fixture.storage / "actions").exists()
    with TemporaryDirectory(prefix="bm-i9-release-failure-") as temporary:
        fixture = Fixture(Path(temporary))
        fixture.job()
        later_release_failure(fixture)
    print("Later release failure and storage identity refusal gates passed.")
    print("Unsupported binary, missing repository, stale lock, strict config, and corrupt-state gates passed.")


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--hold":
        hold_storage(*(Path(value) for value in sys.argv[2:]))
    else:
        main()
