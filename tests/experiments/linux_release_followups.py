"""Exercise remaining requirement-level assertions with disposable repositories."""

from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from acceptance_trace import record
from backup_manager.state import state_read
from linux_i5_job import Fixture


def main():
    with TemporaryDirectory(prefix="bm-release-followups-") as temporary:
        fixture = Fixture(Path(temporary))
        fixture.job(secondary=True)
        missing = fixture.cli("job", "run", "fixture", expected=2, overrides={"PRIMARY_SECRET": ""})
        assert missing["data"]["backup"] == "succeeded" and missing["data"]["destination_id"] == "secondary"
        assert any(row["error_code"] == "CREDENTIAL_UNAVAILABLE" for row in missing["data"]["phases"])

        fixture.job(sources=[(fixture.absent, True)])
        location = fixture.root / "jobs" / "fixture.toml"
        location.write_text(location.read_text().replace("required = true\n", ""))
        default_required = fixture.cli("job", "run", "fixture", expected=3)
        assert default_required["errors"][0]["code"] == "REQUIRED_SOURCE_MISSING"

        for pre_exit, expected in ((0, 0), (1, 3)):
            fixture.marker.unlink(missing_ok=True)
            fixture.job(hooks=[
                ("pre", "pre", pre_exit, 0), ("post", "post", 0, 0),
                ("cleanup", "cleanup-first", 1, 0), ("cleanup", "cleanup-second", 0, 0),
            ])
            result = fixture.cli("job", "run", "fixture", expected=expected)
            assert fixture.seen(["pre", *(["post"] if not pre_exit else []), "cleanup-first", "cleanup-second"])
            history = fixture.history(result)
            assert [row["status"] for row in history["phases"] if row["phase"] == "cleanup"] == ["failed", "succeeded"]
            assert history["overall_outcome"] == ("failed" if pre_exit else "full")
            if not pre_exit:
                human = fixture.args("history", "show", result["operation_id"])
                human.remove("--json")
                shown = subprocess.run(human, env=fixture.env, capture_output=True, text=True, check=True, timeout=15)
                assert "fixture full backup succeeded" in shown.stdout
                assert "cleanup" in shown.stdout and not shown.stderr
        with state_read(fixture.db, fixture.anchor, wait=5) as database:
            record(database, "D48/D53/D61/D68/D70/D71", {
                "missing_secret": missing["operation_id"], "default_required_source": default_required["operation_id"],
                "last_hook_run": result["operation_id"],
            })
    print("Missing credential, source default, readable history, and independent ordered cleanup assertions passed.")


if __name__ == "__main__":
    main()
