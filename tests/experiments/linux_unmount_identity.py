"""Reproduce the unmount identity race inside a disposable mount namespace.

Run with python3 tests/experiments/linux_unmount_identity.py.
"""

import ctypes
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


def main():
    namespace = os.readlink("/proc/self/ns/mnt")
    if len(sys.argv) == 1:
        return subprocess.call([
            "unshare", "--user", "--map-root-user", "--mount",
            sys.executable, __file__, namespace,
        ])
    if namespace == sys.argv[1]:
        raise RuntimeError("Run through unshare in a disposable mount namespace.")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.umount2.argtypes = (ctypes.c_char_p, ctypes.c_int)
    with TemporaryDirectory(prefix="bm-unmount-") as target:
        subprocess.run(["mount", "-t", "tmpfs", "manager-fixture", target], check=True)
        marker = Path(target) / "owner"
        marker.write_text("manager-fixture")
        fd = os.open(target, os.O_PATH)
        try:
            subprocess.run(["mount", "-t", "tmpfs", "external-fixture", target], check=True)
            marker.write_text("external-fixture")
            before = marker.read_text()
            result = libc.umount2(os.fsencode(f"/proc/self/fd/{fd}"), 0)
            after = marker.read_text()
            print(f"before={before} umount2={result} after={after}")
            assert before == "external-fixture"
            assert result == 0
            assert after == "manager-fixture"
        finally:
            os.close(fd)
            while subprocess.run(["mountpoint", "-q", target], check=False).returncode == 0:
                subprocess.run(["umount", target], check=True)
        print("Fixture mounts removed.")


if __name__ == "__main__":
    sys.exit(main())
