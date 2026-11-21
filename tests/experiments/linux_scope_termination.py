"""Verify user systemd scope termination against disposable worker processes."""

import os
from pathlib import Path
import select
import subprocess
import sys
from uuid import uuid4


def identity(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None


def main():
    unit = f"backup-manager-fixture-{uuid4().hex}.scope"
    with subprocess.Popen(
        ["systemd-run", "--user", "--scope", "--quiet", f"--unit={unit}",
         sys.executable, __file__, "worker"],
        stdout=subprocess.PIPE, text=True,
    ) as process:
        try:
            if not select.select([process.stdout], [], [], 10)[0]:
                raise RuntimeError("Worker did not report process identities.")
            pids = [int(pid) for pid in process.stdout.readline().split()]
            assert len(pids) == 2
            identities = {pid: identity(pid) for pid in pids}
            for pid, start in identities.items():
                assert start is not None and unit in Path(f"/proc/{pid}/cgroup").read_text()
            subprocess.run(["systemctl", "--user", "stop", unit], check=True, timeout=15)
            process.wait(timeout=10)
            for pid, start in identities.items():
                assert identity(pid) != start
            print("Scope termination removed the worker and its child.")
        finally:
            subprocess.run(["systemctl", "--user", "stop", unit], check=False, timeout=15,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if process.poll() is None:
                process.kill()


def worker():
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) as child:
        print(os.getpid(), child.pid, flush=True)
        child.wait()


if __name__ == "__main__":
    worker() if len(sys.argv) == 2 and sys.argv[1] == "worker" else main()
