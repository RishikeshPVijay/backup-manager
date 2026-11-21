# Implementation status

Baseline v1.0. I1–I3 milestone gates verified on Linux x86_64. I4–I9 are pending. The full 40-scenario release acceptance run and exact Restic binary certification remain pending. I3 crash and storage fixture evidence is recorded below.

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

### I3 shared lifecycle transitions and crash reconciliation

- D20/D82/D96–D101/L3. Transactional storage transitions now elect one acquisition leader and associate later participants with that operation. A single withdrawal preserves shared work. The last waiting withdrawal requests cancellation and fences new acquisitions. Release is reserved only after the last claimant and only with recorded ownership evidence. Failed release preserves the evidence and blocks subsequent acquisitions.
- D25/D84/D85. Owner reconciliation releases stale claims only when the exact process is gone and all commands have proved termination, or no launch intent exists. An unresolved intent fences the claims. Recovery does not unmount storage. Repository claims must clear before a live job relinquishes storage.
- A5. Provider worker requests can travel through the journaled scope supervisor in private stdin. Launch authorization is rechecked in the same transaction as intent creation. This transport has no public CLI mutation entry point.
- Build, installation, the 50-test installed-wheel suite and static handoff each exited 0. Initial migration failure injection proved SQLite DDL rolled back and no witness was published. The journaled scope fixture exited 0 outside the sandbox and covered private stdin, scope exit, detached child termination and timeout fencing.
- I3 remains open. The cross-process lifecycle driver still needs to consume cancellation requests, revalidate physical state after worker exit, and drive canceled-acquisition cleanup. Operator recovery of unresolved scopes and backup-before-upgrade migrations still need implementation and acceptance testing. Later milestones remain gated.

### I3 lifecycle driver and state backup checkpoint

- D82/D94/D98/D99/L3. The driver now executes an authorized lifecycle worker and independently re-inspects the filesystem after scope termination. A mismatching mount or worker receipt fences the resource. It polls the durable cancel request, stops the scope, and clears a canceled acquisition only when it can confirm no mount remains. If acquisition completed first and manager ownership is verified, it reserves a release before another claimant can enter.
- The SQLite online backup helper now verifies the resulting database and writes an instance ID, generation, and SHA-256 manifest. It refuses to overwrite an existing backup. Upgrade integration and operator restore remain pending.
- Handoff validation, source and wheel builds, wheel installation, and the 54-test installed-wheel regression exited 0. The user-scope fixture exited 0 outside the sandbox. It exercised cancellation from a second process and verified the canceled operation reached durable termination. Driver tests cover worker-versus-physical mismatch, cancellation with no mount, and release reservation after a completed acquisition races cancellation.
- I3 gate remains open. Job-facing admission and independent lifecycle dispatch, execution of a reserved cleanup with its own release budget, operator reconciliation of unresolved launches and upgrades from existing state still need end-to-end coverage. Restic certification remains pending.

### I3 gate closeout

Completed SQLite migrations and verified backups, cross-process storage and repository claims, independently dispatched resource lifecycle, owner fencing, durable process supervision, and evidence-based recovery for interrupted owners and storage. I3 adds public state backup and explicit storage recovery. Restic-dependent operation compatibility, repository validation, job orchestration, and full state restore remain in their designated later milestones. Native Restic locks are untouched. D106 remains paused.

| Command or evidence | Exit | Observed assertion |
|---|---:|---|
| `/tmp/bm-dev/bin/python tools/validate_handoff.py` | 0 | Static references, schemas, example files, DDL, requirements and scenario inventory validated. |
| `/tmp/bm-dev/bin/python -m build --no-isolation --outdir /tmp/bm-i3-dist` | 0 | Built source distribution and wheel. |
| `/tmp/bm-dev/bin/python -m pip --disable-pip-version-check install --no-deps --force-reinstall /tmp/bm-i3-dist/*.whl` | 0 | Installed the built wheel. |
| `/tmp/bm-dev/bin/python -m unittest discover -s tests/unit -q` | 0 | 59 passed. Includes confirmed-generation backup, upgrade rollback, last-waiter race, preservation of orphaned mounts, and storage-before-repository enforcement. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_atomic_claims.py` | 0 | Reverse-ordered processes yielded one busy result and one entire claim set. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_launch_boundaries.py` | 0 | Exits after intent, after scope preparation and after exec before receipt never recorded success. Command side effect was observed only after exec. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_crash_fencing.py` | 0 | SIGKILL of an owner left a live child fenced. Explicit fixture scope termination allowed evidence-based recovery and relinquishment. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_shared_storage.py` | 0 | Independent processes shared one acquisition, a single participant withdrew, last-waiter cancellation completed, a new request retried, final release was independent, and the external fixture mount was preserved. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_journaled_supervisor.py` | 0 | Durable scope and process observations, duplicate launch rejection, private stdin, detached descendant cleanup, cancellation, and uncertain timeout verified. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_lifecycle_mountinfo.py` | 0 | Real mount topology checks and cleanup using private tmpfs mounts. |
| `tests/experiments/linux_loop_lifecycle.py` in the I2 disposable test image described above | 0 | Real ext4 loop identity before and after acquisition, independent verification, verified release, preservation of an external mount, and complete fixture cleanup. |

Verified 2026-11-21 UTC on Linux 6.12.101+deb13-amd64 x86_64 and CPython 3.13.5. Final tested wheel SHA-256 `1bad08c1b89944256fb3df08b111f85c99e59d4ed96450711081953eebc0f956`. The supervisor uses verified user systemd scopes and keeps work fenced when the scope or its recovery evidence cannot be verified. Cross-process coordination tests used a marker-file provider; physical block mount tests ran separately in the private loop-device container. These results establish the I3 milestone on the tested platform. The release acceptance run and I4 binary certification are separate gates.
