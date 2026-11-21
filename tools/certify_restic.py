#!/usr/bin/env python3
"""Run exact-binary certification against temporary local Restic repositories."""

import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import platform
import secrets
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backup_manager.restic import (
    Binary, CompatibilityProfile, Repository, Restic, ResticEvidenceError, confirm_backup, parse_backup, parse_snapshots,
    parse_version,
)
from backup_manager.supervisor import ScopedResult


def check(condition, description):
    if not condition:
        raise AssertionError(description)


class Fixture:
    def __init__(self, root, binary, version, repository_format):
        self.root = root
        self.binary = binary
        self.version = version
        self.format = repository_format
        # A candidate lets the disposable suite exercise the same argv and
        # parsers as production. It never enters the installed profile registry.
        self.restic = Restic(binary, CompatibilityProfile(binary.sha256, version, frozenset({1, 2})),
                             ScopedResult(0, version.encode(), b"", False, "fixture"))
        self.repos = {}
        self.commands = []

    def redact(self, output):
        for repo in self.repos.values():
            output = output.replace(repo.password, b"[REDACTED]")
        return output.replace(os.fsencode(self.root), b"[fixture]").decode(errors="replace")

    def observe(self, invocation, code, stdout, stderr):
        self.commands.append({
            "argv": [self.redact(os.fsencode(arg)) for arg in invocation.argv],
            "exit": code, "stdout": self.redact(stdout), "stderr": self.redact(stderr),
        })
        return ScopedResult(code, stdout, stderr, False, "fixture")

    def run(self, invocation, *, allowed=(0,), stdin=None):
        result = subprocess.run(invocation.argv, env=invocation.env, capture_output=True,
                                check=False, timeout=30, input=stdin)
        observed = self.observe(invocation, result.returncode, result.stdout, result.stderr)
        check(result.returncode in allowed, f"Unexpected exit from Restic command ({result.returncode}).")
        return observed

    def init(self, name, repository_format=None):
        repo = Repository(self.root / name, secrets.token_hex(24).encode())
        self.repos[name] = repo
        self.run(self.restic.explicit_init(repo, repository_format or self.format))
        return repo

    def inventory(self, repo):
        return parse_snapshots(self.run(self.restic.snapshots(repo)))

    def backup(self, repo, path, *flags):
        invocation = self.restic._command(repo, "backup", "--json", *flags, "--", str(path))
        before = self.inventory(repo)
        result = self.run(invocation)
        return confirm_backup(result, before, self.inventory(repo), expected_paths=(path,))

    def similar(self, item):
        return item.tree, item.time, item.hostname, item.paths, item.tags

    def copy(self, source, destination, *ids):
        return self.run(self.restic.copy(source, destination, ids))

    def held_copy(self, source, destination, snapshot, action, *, require_packs=False, finish=False):
        invocation = self.restic.copy(source, destination, (snapshot,))
        invocation = type(invocation)(
            (*invocation.argv[:1], "--limit-upload", "2048", "--pack-size", "4", *invocation.argv[1:]), invocation.env,
        )
        process = subprocess.Popen(
            invocation.argv, env=invocation.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        observed = False
        completed_action = False
        try:
            deadline = monotonic() + 15
            while monotonic() < deadline and process.poll() is None:
                packs_present = any(path.is_file() for path in (destination.path / "data").glob("*/*"))
                if (not require_packs or packs_present) and all(
                    any((repo.path / "locks").iterdir()) for repo in (source, destination)
                ):
                    process.send_signal(signal.SIGSTOP)
                    observed = True
                    break
                sleep(0.02)
            check(observed and process.poll() is None, "Could not hold a copy with both native locks present.")
            action()
            completed_action = True
        finally:
            if process.poll() is None:
                if not (finish and completed_action):
                    process.send_signal(signal.SIGINT)
                process.send_signal(signal.SIGCONT)
            try:
                stdout, stderr = process.communicate(timeout=25)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate(timeout=5)
            self.observe(invocation, process.returncode, stdout, stderr)
        if finish:
            check(process.returncode == 0, "First native copy failed after the second completed.")
        else:
            check(process.returncode != 0, "Fixture copy completed before interruption.")


def lineage(fixture):
    a, b, c = (fixture.init(name) for name in "abc")
    identities = [fixture.restic.repository_identity(fixture.run(fixture.restic.identity(repo))) for repo in (a, b, c)]
    wrong = replace(fixture.restic.identity(a), env={
        **fixture.restic.identity(a).env, "RESTIC_PASSWORD": os.fsdecode(b.password),
    })
    fixture.run(wrong, allowed=(12,))
    check(len({item.id for item in identities}) == 3, "Distinct repositories had identical identities.")
    check(len({item.chunker_polynomial for item in identities}) > 1,
          "Fixture did not exercise different chunker parameters.")
    file = fixture.root / "data"
    file.write_text("first content")
    flags = ("--time", "2025-01-01 00:00:00", "--host", "fixture", "--tag", "old")
    a_id = fixture.backup(a, file, *flags)
    a_snap = fixture.inventory(a)[0]
    file.write_text("independent content")
    b_local = fixture.backup(b, file, *flags)
    fixture.copy(a, b, a_id)
    b_first = fixture.inventory(b)
    check(len(b_first) == 2 and any(item.id == b_local for item in b_first),
          "Copy lost or conflated independent, similarly labeled history.")
    b_snap = next(item for item in b_first if item.id != b_local)
    check(b_snap.original == a_id and fixture.similar(a_snap) == fixture.similar(b_snap),
          "First copy did not preserve tested lineage and snapshot metadata.")
    fixture.copy(a, b, a_id)
    check(fixture.inventory(b) == b_first, "A repeated copy altered destination inventory.")
    fixture.copy(b, c, b_snap.id)
    c_snap, = fixture.inventory(c)
    check(c_snap.original == a_id and fixture.similar(c_snap) == fixture.similar(a_snap),
          "Transitive copy did not preserve first origin and metadata.")
    fixture.run(fixture.restic._command(b, "tag", "--set", "rewritten", b_snap.id))
    changed = next(item for item in fixture.inventory(b) if item.tags == ("rewritten",))
    check(changed.id != b_snap.id and changed.original == a_id,
          "Tag rewrite did not match tested lineage case.")
    fixture.copy(b, c, changed.id)
    inventory = fixture.inventory(c)
    check(len(inventory) == 2 and {item.tags for item in inventory} == {("old",), ("rewritten",)}
          and all(item.original == a_id for item in inventory),
          "Restic suppressed a distinct tag variant or removed destination history.")
    preview = fixture.run(fixture.restic.forget(c, {"keep_last": 1}, preview=True))
    check(isinstance(json.loads(preview.stdout), list) and fixture.inventory(c) == inventory,
          "Forget preview did not return JSON or changed the inventory.")
    fixture.run(fixture.restic._command(a, "forget", a_id))
    b_before = fixture.inventory(b)
    deleted = fixture.run(fixture.restic.copy(a, b, (a_id,)), allowed=(0, 1))
    b_after = fixture.inventory(b)
    check(not fixture.inventory(a) and b_after == b_before,
          "Source deletion did not preserve the observed destination inventory.")
    check(not any(item.original == a_id and fixture.similar(item) == fixture.similar(a_snap) for item in b_after),
          "Missing source unexpectedly produced a destination representation.")

    identical = fixture.init("identical")
    stdin_options = ("backup", "--json", *flags, "--stdin", "--stdin-filename", "fixture")
    original_id = parse_backup(fixture.run(
        fixture.restic._command(a, *stdin_options), stdin=b"matching tree",
    ))
    local_id = parse_backup(fixture.run(
        fixture.restic._command(identical, *stdin_options), stdin=b"matching tree",
    ))
    independent_source, = fixture.inventory(a)
    independent_target, = fixture.inventory(identical)
    check(original_id != local_id and independent_target.original is None
          and fixture.similar(independent_source) == fixture.similar(independent_target),
          "Independence fixture did not produce equal metadata and distinct original histories.")
    fixture.copy(a, identical, original_id)
    matching = fixture.inventory(identical)
    check(len(matching) == 2 and any(item.id == local_id for item in matching)
          and any(item.original == original_id for item in matching),
          "Restic conflated distinct independent histories with identical metadata.")
    unavailable = fixture.root / "unavailable"
    before = fixture.inventory(a)
    skipped = fixture.run(fixture.restic.backup(a, (unavailable, file)), allowed=(0,))
    after = fixture.inventory(a)
    check(parse_backup(skipped) in {item.id for item in after}, "Skipped source fixture did not create a snapshot.")
    try:
        confirm_backup(skipped, before, after, expected_paths=(unavailable, file))
    except ResticEvidenceError:
        pass
    else:
        raise AssertionError("Backup confirmation accepted a requested source Restic skipped.")
    unreadable = fixture.root / "unreadable"
    unreadable.write_text("deliberately unreadable fixture")
    unreadable.chmod(0)
    try:
        partial = fixture.run(fixture.restic.backup(a, (unreadable, file)), allowed=(3,))
    finally:
        unreadable.chmod(0o600)
    try:
        parse_backup(partial)
    except ResticEvidenceError:
        pass
    else:
        raise AssertionError("Partial Restic backup was parseable as successful.")
    return {
        "initial": asdict(a_snap), "copied": asdict(b_snap), "transitive": asdict(c_snap),
        "rewritten": asdict(changed), "deleted_source_exit": deleted.exit_code,
        "repository_identities": [asdict(item) for item in identities],
        "identical_metadata_independent_ids": [original_id, local_id],
        "missing_requested_source_exit": skipped.exit_code,
        "unreadable_source_exit": partial.exit_code,
    }


def locks(fixture):
    a, b, c, d = (fixture.init(name) for name in "abcd")
    payload = fixture.root / "large"
    payload.write_bytes(os.urandom(12 * 1024 * 1024))
    snapshot = fixture.backup(a, payload)
    verify = {}

    def overlap():
        # Read and backup use the same source concurrently with a held
        # copy, while the independent copy writes to another destination.
        check(any(item.id == snapshot for item in fixture.inventory(a)), "Concurrent source read failed.")
        extra = fixture.root / "extra"
        extra.write_text("parallel backup")
        verify["backup_while_copy"] = fixture.backup(a, extra)
        fixture.copy(a, c, snapshot)
        check(any(item.original == snapshot for item in fixture.inventory(c)), "Parallel copy was not represented.")
        for operation, invocation in (
            ("forget", fixture.restic.forget(b, {"keep_last": 1})),
            ("prune", fixture.restic.prune(b)),
            ("check", fixture.restic.check(b)),
        ):
            result = fixture.run(invocation, allowed=(11,))
            verify[operation] = result.exit_code

    fixture.held_copy(a, b, snapshot, overlap, require_packs=True)
    check(not fixture.inventory(b), "Interrupted copy published a snapshot before fixture cancellation.")
    fixture.run(fixture.restic.check(b))
    # Record native same-target behavior while a first copy is held. Core
    # policy always serializes same-target copies, regardless of this result.
    def same_target():
        result = fixture.run(fixture.restic.copy(a, d, (snapshot,)), allowed=(0, 11))
        verify["same_destination_copy"] = result.exit_code

    fixture.held_copy(a, d, snapshot, same_target, finish=True)
    copies = fixture.inventory(d)
    check(1 <= len(copies) <= 2 and all(item.original == snapshot for item in copies),
          "Same-target concurrent copy returned an unexplained destination inventory.")
    verify["same_destination_representations"] = len(copies)
    # Later independent invocation repairs no locks and must confirm its copy.
    fixture.copy(a, b, snapshot)
    check(any(item.original == snapshot for item in fixture.inventory(b)), "Fresh copy after cancellation failed.")
    return verify


def cross_format(fixture):
    old = fixture.init("v1", 1)
    new = fixture.init("v2", 2)
    file = fixture.root / "data"
    file.write_text("format one")
    from_old = fixture.backup(old, file)
    file.write_text("format two")
    from_new = fixture.backup(new, file)
    fixture.copy(old, new, from_old)
    fixture.copy(new, old, from_new)
    for repo, original in ((old, from_new), (new, from_old)):
        check(any(snapshot.original == original for snapshot in fixture.inventory(repo)),
              "Copy across repository formats lost the expected lineage.")
        fixture.run(fixture.restic.check(repo))
    return {"verified_directions": ["1->2", "2->1"]}


def case(binary, version, repository_format, name, test):
    with TemporaryDirectory(prefix="bm-restic-cert-") as temporary:
        fixture = Fixture(Path(temporary), binary, version, repository_format)
        try:
            detail = test(fixture)
            result = {"status": "pass", "observations": detail}
        except Exception as error:
            result = {"status": "failed_or_inconclusive", "reason": fixture.redact(str(error).encode())}
        result["commands"] = fixture.commands
    return {"test_id": name, "repository_format": repository_format, "fixture_removed": not Path(temporary).exists(), **result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restic", default="restic")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    runner_file = Path(__file__).resolve()
    adapter_file = runner_file.parents[1] / "src/backup_manager/restic.py"
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (runner_file, adapter_file)}
    binary = Binary.identify(args.restic)
    invocation = Restic.version(binary)
    executed = subprocess.run(invocation.argv, env=invocation.env, capture_output=True, check=False, timeout=10)
    version = parse_version(ScopedResult(executed.returncode, executed.stdout, executed.stderr, False, "fixture"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [
            pool.submit(case, binary, version, fmt, name, test)
            for fmt in (1, 2) for name, test in (("identity-json-lineage", lineage), ("native-locks-and-interruption", locks))
        ]
        tasks.append(pool.submit(case, binary, version, 2, "cross-format-copy", cross_format))
        cases = [future.result() for future in tasks]
    report = {
        "schema_version": 1,
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "runner_sha256": hashes[runner_file],
        "adapter_sha256": hashes[adapter_file],
        "command": [sys.executable, *sys.argv],
        "environment": platform.platform(), "restic_version": version,
        "restic_binary_sha256": binary.sha256, "cases": cases,
    }
    report["unchanged_inputs"] = (
        Binary.identify(args.restic) == binary
        and all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in hashes.items())
    )
    report["passed"] = (
        report["unchanged_inputs"]
        and all(item["status"] == "pass" and item["fixture_removed"] for item in cases)
    )
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    for item in cases:
        print(item["test_id"], f"v{item['repository_format']}", item["status"], item.get("reason", ""))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
