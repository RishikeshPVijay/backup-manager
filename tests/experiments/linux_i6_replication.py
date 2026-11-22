"""Verify fixed-source copy and additive convergence using disposable repositories."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from backup_manager.convergence import logical_identity, parse_native_inventory
from backup_manager.state import state_read
from backup_manager.supervisor import ScopedResult

from linux_i5_job import Fixture


def inventory(fixture, name):
    output = fixture.restic(name, "snapshots", "--json").encode()
    return parse_native_inventory(ScopedResult(0, output, b"", False, "fixture"))


def identities(fixture, name):
    return {logical_identity(item) for item in inventory(fixture, name)}


def main():
    with TemporaryDirectory(prefix="bm-i6-replication-") as directory:
        fixture = Fixture(Path(directory))
        fixture.config.write_text(
            fixture.config.read_text().replace("job_timeout_seconds = 50", "job_timeout_seconds = 180"),
        )
        fixture.job(secondary=True)
        repos = ("primary", "secondary", "third")
        preserved = set()
        for index, name in enumerate(repos):
            seed = fixture.root / f"seed-{name}"
            revisions = range(3) if name == "primary" else range(1)
            for revision in revisions:
                seed.write_text(f"independent history {index}/{revision}")
                fixture.restic(name, "backup", str(seed))
            preserved |= identities(fixture, name)

        job_config = fixture.root / "jobs" / "fixture.toml"
        designated_primary = job_config.read_bytes()
        first = fixture.cli("job", "run", "fixture", expected=2, overrides={"PRIMARY_SECRET": "wrong-fixture"})
        assert job_config.read_bytes() == designated_primary
        assert first["data"]["destination_id"] == "secondary"
        first_id = first["data"]["snapshot_id"]
        secondary = {snapshot.id: snapshot for snapshot in inventory(fixture, "secondary")}
        first_key = logical_identity(secondary[first_id])
        assert first_key in identities(fixture, "third") and first_key not in identities(fixture, "primary")
        assert all(preserved <= (identities(fixture, "primary") | identities(fixture, name)) for name in ("secondary", "third"))

        copied = next(item for item in inventory(fixture, "third") if logical_identity(item) == first_key)
        fixture.restic("third", "tag", "--set", "third-variant", copied.id)
        variant = next(item for item in inventory(fixture, "third") if item.tags == ("third-variant",))
        assert variant.original == first_id
        expected_variant = logical_identity(variant)
        assert expected_variant != first_key

        fixture.job(secondary=True, hooks=[("post", "post-failed", 1, 0), ("cleanup", "cleanup", 0, 0)])
        second = fixture.cli("job", "run", "fixture")
        assert second["data"]["destination_id"] == "primary"
        expected = preserved | {first_key, expected_variant}
        views = [identities(fixture, name) for name in repos]
        assert views[0] == views[1] == views[2]
        assert expected < views[0] and len(views[0]) == len(expected) + 1
        # The three primary revisions share a path and host. A keep-last of two
        # would remove one after convergence.
        assert len([item for item in inventory(fixture, "secondary")
                    if item.paths == (str(fixture.root / "seed-primary"),)]) == 3
        summary = second["data"]["phases"]
        assert any(row["phase"] == "replication" and row["target_id"] is None and row["status"] == "succeeded" for row in summary)
        assert any(row["phase"] == "convergence" and row["target_id"] is None and row["status"] == "succeeded" for row in summary)
        assert summary[-1]["phase"] == "retention" and summary[-1]["status"] == "succeeded"
        assert fixture.seen(["post-failed", "cleanup"])
        cleanup_index = next(index for index, row in enumerate(summary) if row["phase"] == "cleanup")
        replication_index = next(index for index, row in enumerate(summary) if row["phase"] == "replication")
        assert cleanup_index < replication_index
        assert any(row["error_code"] == "CHUNKER_MISMATCH" for row in summary)

        with state_read(fixture.db, fixture.anchor, wait=5) as database:
            normal = database.execute(
                """SELECT source.destination_id, attempt.snapshot_ids_json, execution.launched_at
                   FROM copy_attempts AS attempt JOIN repository_registrations AS source
                     ON source.repository_key = attempt.source_key
                   JOIN command_executions AS execution USING (execution_id)
                   WHERE attempt.run_id = ? AND attempt.phase = 'replication' AND attempt.state = 'SUCCEEDED'""",
                (second["operation_id"],),
            ).fetchall()
            assert len(normal) == 2 and all(row[0] == "primary" and row[2] for row in normal)
            assert json.loads(normal[0][1]) == json.loads(normal[1][1])
            assert second["data"]["snapshot_id"] in json.loads(normal[0][1])
            assert len(json.loads(normal[0][1])) == 4
            assert database.execute("SELECT count(*) FROM copy_evidence").fetchone()[0] >= 8
            assert database.execute("SELECT count(*) FROM snapshot_provenance").fetchone()[0] == len(views[0]) * 3
    print("Fixed source, fallback catch-up, divergent union, evidence, and no retention filtering passed.")


if __name__ == "__main__":
    main()
