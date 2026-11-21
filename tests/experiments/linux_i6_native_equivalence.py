"""Probe destination-local lineage aliases and reordered tags on the exact binary."""

import os
from pathlib import Path
import secrets
import subprocess
from tempfile import TemporaryDirectory

from backup_manager.convergence import logical_identity, parse_native_inventory, representation, representation_index
from backup_manager.restic import Binary, Restic
from backup_manager.supervisor import ScopedResult


def exercise(binary, version):
    with TemporaryDirectory(prefix="bm-i6-native-") as temporary:
        root = Path(temporary)
        path = root / "payload"
        path.write_text("disposable data")
        secrets_by_repo = {name: secrets.token_hex(24) for name in "abc"}

        def invoke(repo, *command, source=None):
            env = {"PATH": os.defpath, "RESTIC_PASSWORD": secrets_by_repo[repo]}
            if source:
                env["RESTIC_FROM_PASSWORD"] = secrets_by_repo[source]
            result = subprocess.run(
                [str(binary.path), "--no-cache", "--repo", str(root / repo), *command],
                env=env, capture_output=True, check=True, timeout=20,
            )
            return ScopedResult(result.returncode, result.stdout, result.stderr, False, "fixture")

        def inventory(repo):
            return parse_native_inventory(invoke(repo, "snapshots", "--json"))

        def copy(source, destination, snapshot):
            invoke(destination, "copy", "--from-repo", str(root / source), "--", snapshot, source=source)

        for repo in "abc":
            invoke(repo, "init", "--repository-version", str(version))
        invoke("a", "backup", "--host", "fixture", "--tag", "rewrite,a,b", str(path))
        original, = inventory("a")
        copy("a", "b", original.id)
        retained, = inventory("b")
        invoke("b", "rewrite", "--new-host", "fixture", retained.id)
        variant = next(item for item in inventory("b") if item.id != retained.id)
        assert variant.original == retained.id
        copy("b", "c", variant.id)
        invoke("b", "forget", variant.id)
        transferred, = inventory("c")
        before = inventory("b")
        copy("c", "b", transferred.id)
        assert inventory("b") == before == (retained,)
        assert logical_identity(transferred) != logical_identity(retained)
        assert representation(transferred, representation_index(before)) == retained

        invoke("b", "tag", "--set", "b,a,rewrite", retained.id)
        reordered, = inventory("b")
        assert reordered.tags != retained.tags and set(reordered.tags) == set(retained.tags)
        assert logical_identity(reordered) == logical_identity(original)
        copy("a", "c", original.id)
        before = inventory("c")
        copy("b", "c", reordered.id)
        assert inventory("c") == before
        assert representation(reordered, representation_index(before)) is not None
    print(f"format {version}: native destination-local lineage and tag-order behavior verified; fixtures removed.")


def main():
    binary = Binary.identify("restic")
    invocation = Restic.version(binary)
    version = subprocess.run(invocation.argv, env=invocation.env, capture_output=True, check=True, timeout=10)
    Restic.certified(binary, ScopedResult(0, version.stdout, version.stderr, False, "fixture"))
    print(version.stdout.decode().strip(), binary.sha256)
    for repository_format in (1, 2):
        exercise(binary, repository_format)


if __name__ == "__main__":
    main()
