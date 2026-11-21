#!/usr/bin/env python3
"""Build both archives with stable timestamps and normalized sdist metadata."""

import argparse
import gzip
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory


def normalize_sdist(source, output, epoch):
    with (
        tarfile.open(source, "r:gz") as original,
        output.open("xb") as result,
        gzip.GzipFile(filename="", fileobj=result, mode="wb", mtime=epoch) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for entry in sorted(original.getmembers(), key=lambda item: item.name):
            if not (entry.isfile() or entry.isdir()):
                raise RuntimeError(f"Unexpected source distribution entry type: {entry.name}")
            entry.uid = entry.gid = 0
            entry.uname = entry.gname = "root"
            entry.mtime = epoch
            entry.mode = 0o755 if entry.isdir() else 0o644 | (entry.mode & 0o111)
            entry.pax_headers = {}
            archive.addfile(entry, original.extractfile(entry) if entry.isfile() else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epoch", type=int, required=True, help="UTC Unix timestamp used for archive metadata")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 315532800 <= args.epoch <= 4294967295:
        parser.error("epoch is outside the common ZIP and gzip timestamp range")
    root = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=False)
    with TemporaryDirectory() as staging:
        subprocess.run(
            [sys.executable, "-m", "build", "--no-isolation", "--outdir", staging],
            cwd=root, env={**os.environ, "SOURCE_DATE_EPOCH": str(args.epoch)}, check=True,
        )
        source, = Path(staging).glob("*.tar.gz")
        wheel, = Path(staging).glob("*.whl")
        normalize_sdist(source, args.output / source.name, args.epoch)
        with (args.output / wheel.name).open("xb") as output:
            output.write(wheel.read_bytes())


if __name__ == "__main__":
    main()
