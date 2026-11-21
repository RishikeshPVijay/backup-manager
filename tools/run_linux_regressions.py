#!/usr/bin/env python3
"""Run installed-wheel Linux regressions and retain individual fixture logs."""

import argparse
from pathlib import Path
import subprocess
import sys

SUITES = {
    "scoped": (
        "linux_atomic_claims", "linux_scope_termination", "linux_journaled_supervisor",
        "linux_launch_boundaries", "linux_crash_fencing", "linux_shared_storage",
        "linux_scoped_restic", "linux_i5_job", "linux_i6_native_equivalence",
        "linux_i6_replication", "linux_i6_faults", "linux_i7_operations",
    ),
    "namespace": ("linux_lifecycle_mountinfo", "linux_unmount_identity", "linux_move_mount_identity"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=SUITES)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    fixtures = Path(__file__).resolve().parents[1] / "tests" / "experiments"
    failed = False
    for name in SUITES[args.suite]:
        command = [sys.executable, str(fixtures / f"{name}.py")]
        with (args.output / f"{name}.log").open("x") as log:
            print(command, file=log, flush=True)
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
            print(f"exit: {result.returncode}", file=log)
        print(f"{name}: {result.returncode}", flush=True)
        failed |= result.returncode != 0
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
