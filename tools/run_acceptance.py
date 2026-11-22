#!/usr/bin/env python3
"""Run the checked-in release scenarios and fail closed on incomplete evidence."""

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROBES = {
    "unit": ("-m", "unittest", "discover", "-s", "tests/unit", "-v"),
    "jobs": "linux_i5_job",
    "replication": "linux_i6_replication",
    "faults": "linux_i6_faults",
    "operations": "linux_i7_operations",
    "boundaries": "linux_i9_boundaries",
    "claims": "linux_atomic_claims",
    "copy-claims": "linux_i9_copy_claims",
    "launch": "linux_launch_boundaries",
    "crash": "linux_crash_fencing",
    "shared": "linux_shared_storage",
    "scoped": "linux_scoped_restic",
    "supervisor": "linux_journaled_supervisor",
    "lineage": "linux_i6_native_equivalence",
    "inventory": "linux_i9_inventory",
    "identical": "linux_i9_identical",
    "migration": "linux_i9_migration",
    "native": ("tools/certify_restic.py",),
}


def load_plan():
    directory = ROOT / "tests" / "acceptance"
    specifications = yaml.safe_load((directory / "scenarios.yaml").read_text())["scenarios"]
    definition = yaml.safe_load((directory / "assertions.yaml").read_text())
    plan = definition["scenarios"]
    if definition["schema_version"] != 1 or set(plan) != {row["id"] for row in specifications}:
        raise ValueError("Acceptance plan must bind every scenario exactly once.")
    for test_id, row in plan.items():
        if not row["expected"] or not row["probes"] or set(row["probes"]) - PROBES.keys():
            raise ValueError(f"Invalid acceptance binding: {test_id}")
    return plan


def write_json(path, data):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def command_for(probe, output, restic):
    selector = PROBES[probe]
    if isinstance(selector, str):
        selector = (f"tests/experiments/{selector}.py",)
    if probe == "native":
        selector += ("--restic", str(restic), "--report", str(output / "certification.json"))
    return [sys.executable, *selector]


def run_probe(probe, root, restic):
    directory = root / probe
    directory.mkdir(mode=0o700)
    fixtures = directory / "fixtures"
    fixtures.mkdir(mode=0o700)
    log = directory / "probe.log"
    trace = directory / "cli-and-sqlite.jsonl"
    command = command_for(probe, directory, restic)
    with log.open("x", encoding="utf-8") as stream:
        completed = subprocess.run(
            command, cwd=ROOT, check=False, stdout=stream, stderr=subprocess.STDOUT,
            env={**os.environ, "TMPDIR": str(fixtures), "BACKUP_MANAGER_ACCEPTANCE_TRACE": str(trace)},
        )
    report = {
        "probe": probe, "command": command, "exit_code": completed.returncode,
        "fixture_directory": str(fixtures), "leftover_fixture_names": sorted(item.name for item in fixtures.iterdir()),
        "log": str(log), "trace": str(trace) if trace.is_file() and trace.stat().st_size else None,
    }
    if probe == "native" and (directory / "certification.json").is_file():
        report["certification"] = str(directory / "certification.json")
    report["report_sha256"] = {}
    for path in (report["log"], report["trace"], report.get("certification")):
        if path:
            with Path(path).open("rb") as stream:
                report["report_sha256"][path] = hashlib.file_digest(stream, "sha256").hexdigest()
    write_json(directory / "execution.json", report)
    return report


def scenario_manifest(test_id, row, observations, host):
    missing = set(row["probes"]) - observations.keys()
    runs = [observations[name] for name in row["probes"] if name in observations]
    gaps = [row["gap"]] if row.get("gap") else []
    if missing:
        gaps.append("Required probes were omitted: " + ", ".join(sorted(missing)))
    if any(run["leftover_fixture_names"] for run in runs):
        gaps.append("At least one fixture left resources in its temporary directory.")
    if not any(run["trace"] for run in runs):
        gaps.append("No CLI/SQLite trace was collected.")
    success = bool(runs) and all(run["exit_code"] == 0 for run in runs)
    result = "failed" if any(run["exit_code"] != 0 for run in runs) else ("inconclusive" if gaps else "passed")
    return {
        **host, "test_id": test_id, "expected": row["expected"],
        "observed": {"status": result, "gaps": gaps, "probes": runs},
        "command": [run["command"] for run in runs], "exit_code": 0 if success else (1 if runs else None),
        "report_paths": [path for run in runs for path in (run["log"], run["trace"], run.get("certification")) if path],
        "passed": success and not gaps,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--parallel", type=int, choices=(1, 2), default=1)
    parser.add_argument("--probe", choices=PROBES, action="append", help="Run a subset for diagnosis; the full gate remains closed.")
    args = parser.parse_args()
    plan = load_plan()
    if not __debug__:
        parser.error("acceptance requires assertions enabled")
    restic_path = shutil.which("restic")
    if restic_path is None:
        parser.error("the certified Restic executable must be on PATH")
    restic = Path(restic_path).resolve()
    with restic.open("rb") as stream:
        fingerprint = hashlib.file_digest(stream, "sha256").hexdigest()
    registry = json.loads((ROOT / "schemas" / "restic-profiles.json").read_text())
    version = subprocess.check_output([str(restic), "version"], text=True, timeout=10).strip()
    if not any(
        entry["binary_sha256"] == fingerprint and entry["version_line"] == version
        for entry in registry["profiles"]
    ):
        parser.error("the current Restic executable has no certified profile")
    host = {
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
        "restic_version": version, "restic_binary_sha256": fingerprint,
    }
    root = args.output.absolute()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    selected = args.probe or list(PROBES)
    observations = {}
    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        pending = {name: executor.submit(run_probe, name, root, restic) for name in dict.fromkeys(selected)}
        for name, future in pending.items():
            observations[name] = future.result()
            print(f"{name}: {observations[name]['exit_code']}", flush=True)
    manifest = [scenario_manifest(key, row, observations, host) for key, row in plan.items()]
    write_json(root / "scenarios.json", manifest)
    with (ROOT / "contracts" / "requirements-traceability.csv").open(newline="") as stream:
        requirements = list(csv.DictReader(stream))
    unverified = [row["requirement_id"] for row in requirements
                  if row["policy_status"] == "confirmed" and row["evidence_status"] != "verified"]
    certified_architectures = {
        entry["version_line"].rsplit("/", 1)[-1] for entry in registry["profiles"]
    }
    missing_profiles = sorted({"amd64", "arm64"} - certified_architectures)
    write_json(root / "release-gate.json", {
        **host, "missing_requirement_signoff": unverified,
        "missing_linux_restic_profiles": missing_profiles,
        "open_scenarios": [row["test_id"] for row in manifest if not row["passed"]],
    })
    passed = sum(row["passed"] for row in manifest)
    print(f"{passed}/{len(manifest)} scenarios supported by observed evidence. {len(unverified)} requirements await signoff.")
    return 0 if (
        not args.probe and passed == len(manifest) and not unverified and not missing_profiles
    ) else 1


if __name__ == "__main__":
    sys.exit(main())
