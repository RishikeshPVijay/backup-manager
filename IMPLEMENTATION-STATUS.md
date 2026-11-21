# Implementation status

Baseline v1.0. I1 foundation is implemented. I2 provider and worker gate verified. I3 is in progress. I4–I9 are pending. Mandatory acceptance scenarios remain 0 executed and 0 passed out of 40. Restic certification, runtime migrations, and process/storage crash recovery have no execution evidence.

## I1 foundation checklist

- [x] D1 (foundation scope), Linux PEP 621 package and console command.
- [x] D35 (CLI foundation), typed statuses, exit code mapping and schema-compatible JSON result.
- [x] D49 (static subset), strict TOML structure, multiple diagnostics, destination identifier/path checks, and redaction of offending values.
- [x] D50 (snapshot primitive), deep immutable configuration detached from later edits. Batch admission integration is pending.
- [x] D47 (configuration boundary), malformed TOML and validation diagnostics suppress raw values. Runtime secret handling is pending.
- [x] Foundation CI and isolated unit fixtures.
- [x] D56, validate approved job timeout overrides.
- [x] D52, validate approved per-job history limits.
- [x] D22/R3/G6, validate approved explicit prune selection.
- [ ] D49, integrate provider configuration schema validation and complete acceptance coverage when providers and runtime exist.

### Evidence

Local Linux x86_64, CPython 3.13.5, 2026-11-21 UTC. Changes built on baseline revision `fe47883592b37341af0fe5b03678d1c9328d5dd8`.

| Command | Exit | Observation |
|---|---:|---|
| `/tmp/bm-dev/bin/python tools/validate_handoff.py` | 0 | Passed before and after changes. Four schemas, three examples, reference DDL, 140 requirements and 40 scenario definitions verified statically. |
| `/tmp/bm-dev/bin/python -m build --no-isolation --outdir /tmp/bm-i1-dist` | 0 | Built sdist and built wheel from that sdist. Build log at `/tmp/bm-i1-build.log`. |
| `/tmp/bm-dev/bin/python -m pip install --no-deps --force-reinstall /tmp/bm-i1-dist/*.whl` | 0 | Installed wheel in the isolated development environment. |
| `/tmp/bm-dev/bin/python -m unittest discover -s tests/unit -v` | 0 | Eight passed against the installed wheel. Report at `/tmp/bm-i1-tests.log`. |
| `/tmp/bm-dev/bin/backup-manager --json version` | 0 | One versioned JSON result for installed package 0.1.0. |

Wheel SHA-256 `7ac8c8719af32a28eeb2a28deab9eb15bee13f659f0b6b9ab619adc53c8bb9f9`.
Source distribution SHA-256 `f5e481cf31d7a10bfa875f2470a5dbb0e2459ff9f570187324e9944062e0f88f`.
Python 3.12 and hosted CI were not run locally. The installed Restic reports version 0.18.0. The version query is environment inventory only.

### I1 configuration clarification checkpoint

The user confirmed the three configuration decisions recorded in docs/14. Static handoff, sdist/wheel build, wheel installation, and the ten-test foundation suite all exited 0 after the changes. Logs are at `/tmp/bm-i1-clarifications-build.log` and `/tmp/bm-i1-clarifications-tests.log`. The runtime enforcement of retention, history deletion, and deadlines belongs to later milestones.

The CLI uses argparse, packages the checked-in schemas with setuptools, and locates individual job configuration at `jobs/<job-id>.toml` relative to the global configuration file.

## I2 plugin and storage checkpoint

- [x] A1/D47, shipped provider API, discovery with manifest and capabilities checks, file and environment providers.
- [x] A2/D86/D91, read-only Linux mount inspection primitive with block-device identity verification. Positive verification on real block devices remains pending.
- [ ] A5/D82/D84/D92/D93/D94/L3, supervised side-effecting lifecycle path and tested ownership and termination guarantees.

The installed wheel passed 14 unit tests on Linux x86_64 with Python 3.13.5. Static handoff validation, package build and wheel installation exited 0. The new assertions cover duplicate providers, secret file permissions, final symlinks and FIFOs, absent environment secrets, and rejection of an unmounted directory.

### Lifecycle gate blocker

On Linux 6.12.101+deb13-amd64, `python3 tests/experiments/linux_unmount_identity.py` ran in a disposable user and mount namespace. It pinned a manager fixture mount with `O_PATH`, stacked a second fixture mount over it, and passed the saved descriptor path to `umount2`. The syscall returned zero and removed the **second** mount. The pinned manager fixture remained mounted until fixture cleanup. This rules out both a check followed by pathname unmount and the tested descriptor-path substitution as ownership-preserving release approaches under concurrent mount replacement.

The built-in provider exposes read-only capabilities pending a release mechanism tied to the verified mount instance, or an approved operating restriction that rules out concurrent external mount changes. No runtime mount or unmount command is wired. D92 and D94 prohibit using the observed behavior in production. I2 remains open and the dependency gate holds I3–I9.

### Descriptor-based move follow-up

Ran `python3 tests/experiments/linux_move_mount_identity.py` on the same Linux build in a disposable user and private mount namespace. The fixture verified the source descriptor could still read the manager's marker before each move. It used empty source and destination paths with both descriptors.

| Scenario | Syscall result | Observed mount locations |
|---|---|---|
| Manager mount alone | Success | Manager mount moved to staging. |
| External mount stacked at target | Success | Original target became uncovered. The external mount was visible at staging. |
| Manager detached and external replacement mounted | EINVAL | External replacement remained at target. Staging remained empty. |
| External child mounted under manager mount | Success | Manager mount moved to staging with the external child. |

The command exited 0 after reproducing all observations and removing fixture mounts. Its assertions reproduce the failure modes, so this is evidence against using this release mechanism. A post-move identity check would occur after the external mount was already moved. No production lifecycle changes were made. The original unmount experiment, static handoff validator, and 14 installed-wheel unit tests also exited 0. I2 stays blocked on D92/D94.

### Approved dedicated-mountpoint implementation

The user approved the dedicated `on_demand` mountpoint restriction recorded in docs/14. Under that operating policy, the Linux provider can reuse a verified existing mount without claiming ownership, acquire at a dedicated empty mountpoint, and release only with fresh matching ownership and exactly one mount at or below the path. The worker protocol runs lifecycle methods in a separate session through a bounded command executor. Worker exit and worker replies are advisory until the core supervisor confirms termination of the containment. Public CLI mutation remains disabled.

| Command | Exit | Observation |
|---|---:|---|
| `/tmp/bm-dev/bin/python tools/validate_handoff.py` | 0 | Static handoff passed. |
| `/tmp/bm-dev/bin/python -m build --no-isolation --outdir /tmp/bm-i2-dist` | 0 | Built source and wheel packages. |
| `/tmp/bm-dev/bin/python -m pip --disable-pip-version-check install --no-deps --force-reinstall /tmp/bm-i2-dist/*.whl` | 0 | Installed the rebuilt wheel. |
| `/tmp/bm-dev/bin/python -m unittest discover -s tests/unit -q` | 0 | 20 tests passed. Lifecycle tests use simulated device observations and command results. Worker tests exercise bounded child output and unsafe request rejection. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_lifecycle_mountinfo.py` | 0 | Private-namespace tmpfs fixture confirmed unmounted, mounted-but-unverified, nested, and cleanly removed observations. |

Kernel 6.12.101+deb13-amd64, Linux x86_64, Python 3.13.5. Required positive block-device lifecycle evidence is pending. This environment exposes neither a disposable loop device nor a loop-control node. Existing disk devices were excluded from the fixture. The user systemd scope bus also refused access. I2 remains open pending end-to-end verification on a disposable block filesystem and supervised termination evidence. No acceptance scenarios or Restic certification were run.

### I2 gate verification

I2 provider and worker gate verified on Linux x86_64. The absolute SQLite-backed coordinator integration and crash recovery are I3.

- Installed-wheel unit suite, static handoff, and private tmpfs topology checks exited 0. The suite now has 22 tests including strict nested storage config and unknown-provider rejection.
- `python3 tests/experiments/linux_scope_termination.py` exited 0 outside the sandbox. The disposable user systemd scope contained both fixture processes. Stopping it removed both original PID/start identities.
- `tests/experiments/linux_loop_lifecycle.py` exited 0 in a private Docker mount namespace with network disabled, access limited to loop block devices, and the fixture source mounted read-only. It used a fresh sparse ext4 image and filesystem UUID. Assertions covered verified acquisition, no ownership from reuse, refusal of an empty ownership receipt, verified release, preservation of a pre-existing external fixture mount, and cleanup of the mount, loop attachment, and image.
- The base image lacked `mke2fs`. The first custom-image run failed on mount under the default Docker profile and cleaned up the fixture. It passed with the fixture container's AppArmor profile disabled. The test image derived from `python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90`.

The experiments exercise real fixture resources only. They do not validate production crash recovery or issue Restic certification.

## I3 initial state checkpoint

- [x] D25/L3 foundation, explicit state initialization, runtime migration v1, version and checksum witness, SQLite WAL and foreign-key enforcement.
- [x] Read-only state check rejects missing/corrupt state, permission violations, unreconciled SQLite sidecars, and mismatch against the adjacent identity witness. It does not admit work.
- [ ] A3/A4/A5/D25/D82/D83/D84/D103/D104/D105/L3, durable launch and containment, claim fencing, interrupted-operation reconciliation.
- [ ] Independent generation witness and backup-before-migration flow for upgrades to existing instances.

Static handoff validation exited 0 before and after the runtime migration addition. Build, wheel installation, and the 29-test installed-wheel unit suite exited 0 on Linux x86_64 with Python 3.13.5. The suite covers missing state without writes, duplicate initialization refusal, WAL and migration checksum, corrupted state, generation mismatch, unexpected WAL refusal, and CLI confirmation and exit codes. Init currently recognizes btrfs, ext2/3/4, f2fs, tmpfs, xfs and zfs as local filesystems. Unrecognized filesystem types fail closed.

### Rollback witness gate

In an isolated SQLite fixture, advanced the database and its adjacent identity file from generation 1 to 2. Restoring only the SQLite file was detected and rejected. Restoring both files produced the same inputs as the original generation 1 and passed the local integrity check. The command exited 0 after asserting both observations. The current check is therefore evidence of local integrity only.

The frozen configuration defines only the state database path. Whole-state rollback protection requires a generation witness outside the rollback domain. Its location and operational authority, as well as how to maintain it through recovery, must be established before authorizing production work. Claims, launch admission, state recovery, Restic certification and mandatory acceptance remain incomplete.

### I3 separate generation witness and claim primitives

- The user authorized continuing with `state.anchor_path`. The strict schema and example now supply it. Runtime requires separate canonical directory trees; operators must provision the witness outside the database rollback domain. The checker rejects older database versions while the independent witness remains current.
- A write reserves and syncs the next witness generation before starting the SQLite transaction. An interrupted write leaves the instance fenced for explicit recovery. The witness directory's OS lock serializes admitted writers and read-only checks. A process-death failure injection used exit 31 after reservation and confirmed the check fails closed.
- Added boot, PID namespace and process start observations. Failed access is an unknown observation. Absence from procfs alone no longer proves termination; the check also uses pidfd lookup where available. Process observations alone never establish containment termination.
- Added transactional all-or-none repository claim-set admission, fencing each request against its repository generation. Until a certified compatibility matrix is supplied, the primitive defaults to exclusive access. Same-destination copies are always excluded.
- The installed-wheel unit suite exited 0 with 37 tests after an initial fixture-mode failure was fixed by creating private fixture directories. Static handoff, source/wheel build, and wheel installation exited 0. Build command used `/tmp/bm-dev/bin/python -m build --no-isolation --outdir /tmp/bm-i3-dist`.
- Coordinator integration, durable containment launch receipts, explicit recovery of inconsistent state, backup-before-upgrade and later milestones remain open. No Restic certification or mandatory acceptance run yet.

### I3 launch journal and storage identity checkpoint

- A4/A5/A12/D25/D82/L3. SQLite transactions now record launch intent and launch receipts with events. A receipt must match the active supervisor and boot identity. Uncertain launch fences both storage and repository claims. Missing receipts block otherwise compatible repository use. The journal is a primitive and requires real containment observations from the coordinator.
- D86/D87/D88/D89/L6. The storage identity primitive rejects verified aliases, unapproved identity changes, and unverifiable resources without explicit coordination identity. Equal explicit identities share the same admission fences.
- `/tmp/bm-dev/bin/python -m build --no-isolation --outdir /tmp/bm-i3-dist` and installed-wheel regression exited 0. `/tmp/bm-dev/bin/python -m unittest discover -s tests/unit -q` passed 43 tests. `/tmp/bm-dev/bin/python tools/validate_handoff.py` exited 0.
- The crash fixture deliberately exited 32 after committing intent and before writing any receipt. Another process verified the persisted intent and absence of launch confirmation. Other tests verify supervisor binding, recovery fencing, and prevention of new conflicting claims.
- I3 remains open for the production supervisor, durable termination attestations, shared storage lifecycle transitions and crash reconciliation. The CLI cannot launch backup, copy, or mount operations. Restic certification and mandatory acceptance remain pending.

### I3 journaled Linux scope supervisor checkpoint

A4/A5/A12/D25/D82/D84/L3. A supervisor commits intent, creates a dedicated scope, verifies worker identity and cgroup membership, then sends the command over a pipe. It separately commits the command's launch receipt and terminal exit. It drains bounded stdout/stderr and stops the scope. A timeout or missing reply records uncertainty and holds claims. The successful terminal transition requires confirmed empty containment and the same scope receipt. The supervisor still needs the storage coordinator and explicit crash recovery integration.

- Source/wheel build and installation exited 0. Installed-wheel suite exited 0 with 44 tests. Static handoff validator exited 0.
- Outside the sandbox, `/tmp/bm-dev/bin/python tests/experiments/linux_journaled_supervisor.py` exited 0. A disposable command launched a new-session child. Scope shutdown removed it. SQLite recorded an ordinary exit as TERMINATED and a separate deadline case as LAUNCH_UNCERTAIN. Only test commands were used.
- I3 remains open for lifecycle integration, owner crash reconciliation and explicit recovery. The CLI still holds production commands unavailable. I4–I9, Restic certification and mandatory acceptance remain pending.
