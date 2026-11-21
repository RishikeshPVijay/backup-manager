"""Probe descriptor-based move_mount in a disposable Linux mount namespace."""

import ctypes
from pathlib import Path
import os
import subprocess
import sys
from tempfile import TemporaryDirectory

MOVE_MOUNT_F_EMPTY_PATH = 4
MOVE_MOUNT_T_EMPTY_PATH = 64


def mount(path, marker):
    subprocess.run(["mount", "-t", "tmpfs", marker, path], check=True)
    (path / "owner").write_text(marker)


def marker(path):
    owner = path / "owner"
    return owner.read_text() if owner.exists() else "none"


def probe(root, scenario, move_mount):
    target = root / "target"
    staging = root / "staging"
    target.mkdir(exist_ok=True)
    staging.mkdir(exist_ok=True)
    mount(target, "manager")
    source_fd = os.open(target, os.O_PATH | os.O_DIRECTORY)
    destination_fd = os.open(staging, os.O_PATH | os.O_DIRECTORY)
    try:
        if scenario == "replacement":
            subprocess.run(["umount", "-l", target], check=True)
        if scenario in {"stacked", "replacement"}:
            mount(target, "external")
        if scenario == "external-child":
            child = target / "child"
            child.mkdir()
            mount(child, "external-child")
        pinned_marker = os.open("owner", os.O_RDONLY, dir_fd=source_fd)
        with os.fdopen(pinned_marker) as stream:
            assert stream.read() == "manager"
        result = move_mount(
            source_fd, b"", destination_fd, b"",
            MOVE_MOUNT_F_EMPTY_PATH | MOVE_MOUNT_T_EMPTY_PATH,
        )
        error = ctypes.get_errno() if result else 0
        surfaces = tuple(marker(path) for path in (target, staging, target / "child", staging / "child"))
        print(f"{scenario}: result={result} errno={error} target/staging/children={surfaces}", flush=True)
        return result, surfaces
    finally:
        os.close(source_fd)
        os.close(destination_fd)
        for path in (staging / "child", target / "child", staging, target):
            while subprocess.run(["mountpoint", "-q", path], check=False).returncode == 0:
                subprocess.run(["umount", path], check=True)


def main():
    namespace = os.readlink("/proc/self/ns/mnt")
    if len(sys.argv) == 1:
        return subprocess.call([
            "unshare", "--user", "--map-root-user", "--mount", "--propagation", "private",
            sys.executable, __file__, namespace,
        ])
    if namespace == sys.argv[1]:
        raise RuntimeError("Expected a disposable mount namespace.")
    libc = ctypes.CDLL(None, use_errno=True)
    move_mount = libc.move_mount
    move_mount.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
    )
    move_mount.restype = ctypes.c_int
    with TemporaryDirectory(prefix="bm-move-mount-") as temporary:
        root = Path(temporary)
        normal = probe(root, "normal", move_mount)
        stacked = probe(root, "stacked", move_mount)
        replacement = probe(root, "replacement", move_mount)
        child = probe(root, "external-child", move_mount)
        assert normal == (0, ("none", "manager", "none", "none"))
        # These assertions reproduce unsafe outcomes on the tested Linux build.
        assert stacked == (0, ("none", "external", "none", "none"))
        assert replacement == (-1, ("external", "none", "none", "none"))
        assert child == (0, ("none", "manager", "none", "external-child"))
    print("Fixture mounts removed.")


if __name__ == "__main__":
    sys.exit(main())
