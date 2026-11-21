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
