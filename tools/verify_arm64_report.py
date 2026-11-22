#!/usr/bin/env python3
"""Validate the GitHub ARM64 certification artifact before profile review."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile


ARCHIVE_SHA256 = "0669c47ed00d39836e5ee14c7c0102e622a9397e108bed143889e6a1e5088762"
RUN_COMMIT = "8af65967d7cec88d655eec9019ccfa87f1e63763"
BINARY_SHA256 = "a4aa97c7d651e87eb5ade27e32ae0f457887ee16b86958845e781aa18e524d03"
RUNNER_SHA256 = "1243c6fe0bff2ffe2cd21f569418bd5dad3b02033d09f8a7e91e08700b9fd04e"
ADAPTER_SHA256 = "187a8b26459219b568e82db58d0069120db8d761a161bff79d9be4ee89e6ee59"
REPORT_NAME = "restic-arm64-certification.json"
LIMIT = 2 * 1024 * 1024
REQUIRED_CASES = {
    ("identity-json-lineage", 1), ("identity-json-lineage", 2),
    ("native-locks-and-interruption", 1), ("native-locks-and-interruption", 2),
    ("cross-format-copy", 2),
}


def verify(archive_bytes):
    if hashlib.sha256(archive_bytes).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 differs from the recorded GitHub artifact digest.")
    with ZipFile(io.BytesIO(archive_bytes)) as archive:
        entries = archive.infolist()
        if len(entries) != 1 or entries[0].filename != REPORT_NAME or entries[0].file_size > LIMIT:
            raise ValueError("Unexpected certification archive contents.")
        report_bytes = archive.read(entries[0])
    report = json.loads(report_bytes)
    if (
        report["schema_version"] != 1 or report["passed"] is not True
        or report["unchanged_inputs"] is not True
        or report["git_commit"] != RUN_COMMIT
        or report["runner_sha256"] != RUNNER_SHA256 or report["adapter_sha256"] != ADAPTER_SHA256
        or report["restic_binary_sha256"] != BINARY_SHA256
        or not re.fullmatch(r"restic 0\.18\.0 compiled with go\S+ on linux/arm64", report["restic_version"])
        or not re.fullmatch(r"Linux-.*-aarch64(?:-.*)?", report["environment"])
    ):
        raise ValueError("Certification provenance or outcome differs from the recorded ARM64 run.")
    cases = report["cases"]
    if len(cases) != len(REQUIRED_CASES) or {
        (case["test_id"], case["repository_format"]) for case in cases
    } != REQUIRED_CASES:
        raise ValueError("Certification is missing required cases or contains duplicates.")
    if any(
        case["status"] != "pass" or case["fixture_removed"] is not True or not case["commands"]
        for case in cases
    ):
        raise ValueError("Certification has a failed case, missing command evidence, or incomplete cleanup.")
    return {
        "binary_sha256": report["restic_binary_sha256"],
        "version_line": report["restic_version"],
        "repository_formats": [1, 2],
        "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
    }, report_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="Original GitHub Actions artifact ZIP")
    parser.add_argument("--report-out", type=Path, help="Write the verified report to a new file")
    args = parser.parse_args()
    try:
        with args.archive.open("rb") as source:
            data = source.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError("Certification archive exceeds the size limit.")
        candidate, report = verify(data)
        if args.report_out:
            with args.report_out.open("xb") as output:
                output.write(report)
    except (OSError, ValueError, TypeError, KeyError, BadZipFile) as error:
        parser.exit(1, f"Certification artifact refused: {error}\n")
    print(json.dumps(candidate, indent=2))


if __name__ == "__main__":
    main()
