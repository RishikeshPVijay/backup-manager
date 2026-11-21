# Restic integration and compatibility certification

Restic is the only backup engine. Use system-installed binary, configurable path defaulting to `restic`. Exact tested versions only; version range is a registry of certified releases, not a permissive semver interval. Reject unsupported binary/repository format with actionable diagnostic. Do not hardcode an untested release as certified.

## Adapter

Typed commands: repository identity, snapshots, backup, copy with frozen explicit source IDs, forget, prune, check, init/register/admin unlock. Use argv arrays, no shell interpolation; secrets via secure non-argv channel. Capture bounded/redacted stdout/stderr; JSON parsers command-specific, tolerant of unknown extension fields but strict on required fields. Separate exit success, snapshot confirmation, and full integrity check.

## Copy equivalence

Cross-repo snapshot IDs may differ. Snapshot `original` is candidate lineage evidence, not universally authoritative alone. Certification must establish behavior for A→B, B→C, repeated copies, tags/rewrites, independently similar snapshots, duplicate concurrent copies and snapshot deletion. Persist source/destination evidence; revalidate with complete enumerations. If equivalence unresolved, mark incomplete; never invent mapping. Snapshot presence is not full data integrity proof.

## Lock matrix

Start conservatively: read/read and backup/read/copy-source candidate overlap; copy-destination conflicts with itself by A9; forget, prune, check and admin actions exclusive until validated. The effective compatibility is intersection of Restic-tested native behavior and Backup Manager policy. External Restic processes are not coordinated by SQLite; respect native locks, never auto unlock.

## Certification runner

Disposable local repos with distinct passwords; isolate fixtures, record exact version, binary fingerprint, OS, repository format, test suite revision, command output redacted, pass/fail/inconclusive. Mandatory tests: repo identity, JSON, A→B, repeat, A→B→C, divergence, metadata rewrite, same-destination copy race, copy/copy distinct destinations, backup/copy, forget/prune/check locking, source deletion, interrupted copy, different chunker params when supported. Any correctness-critical inconclusive => not certified. `tools/restic-copy-poc.sh` is exploratory only, not certification evidence.

## I4 certification procedure

Run the harness as an unprivileged user so a mode-000 fixture can exercise an actual partial read. Provide a new report path.

```sh
python3 tools/certify_restic.py --restic /usr/bin/restic --report /tmp/restic-certification.json
```

The harness tests the packaged adapter's command shapes against disposable repositories and records commands, exit status, outputs with fixture credentials removed, independent inventories, and cleanup. Its profile inputs include the complete version line, executable SHA-256, repository formats, and positively observed native overlaps. Only explicitly installed profiles are available to the runtime. A successful candidate harness run does not install a profile automatically.

Production calls pass the two repository credentials as distinct environment variables over the supervisor's private command channel. The environment is set for the Restic child. It is excluded from the scope manager's argv and SQLite records. The adapter exposes redacted diagnostics separately from the in-memory result used for parsing. Capture truncation prevents a result from establishing successful backup or complete inventory.

Backup confirmation requires a clean Restic exit, one completed backup summary, a newly observed snapshot ID, and matching requested paths in a fresh inventory. A deleted or missing copy source can produce exit zero. Copy success requires fresh complete inventory evidence, which the replication executor must check. The certification tests also distinguish a tag rewrite sharing the same origin from independently created snapshots with identical metadata. These observations do not constitute a deployed convergence planner.

The installed Linux amd64 profile covers the tested Restic 0.18.0 executable and repository formats 1 and 2, including copies across formats in either direction. The report contains the exact fingerprint. It also records native acceptance of overlapping same-destination copy commands, so Backup Manager retains its own same-destination exclusion. Validated shared-source overlaps are limited to the read, backup, and copy-source pairs in the profile. [Certification report](../tests/certification/restic-0.18.0-linux-amd64.json)
