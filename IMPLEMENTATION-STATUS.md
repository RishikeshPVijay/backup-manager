# Implementation status

Baseline v1.0. I1–I6 milestone gates verified on Linux x86_64. I7–I9 are pending. Restic 0.18.0 Linux amd64 is certified only for the exact recorded binary fingerprint and repository formats 1 and 2. The full 40-scenario release acceptance run remains pending.

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

## I4 Restic adapter and binary certification

- [x] A6/A7/D1/D14/D41/D63/R4/R6/R8/R9/R10/G3. Typed argv-only Restic adapter, credential separation, strict identity/backup/inventory evidence, exact installed profile selection, and disposable certification runner. The adapter exposes explicit admin invocations but does not schedule them.
- [x] V-001/V-002/V-005/V-006 for the tested Linux amd64 binary and formats 1 and 2. The profile records only observed compatible pairs: COPY_SOURCE with READ, BACKUP, or COPY_SOURCE. A9 still serializes same-destination copies. Forget, prune, and check remain exclusive. No native lock removal occurs in the runner or production adapter.
- [x] Certification report and traceability. Recorded 146 Restic command outcomes across five passing cases. Evidence at `tests/certification/restic-0.18.0-linux-amd64.json` includes executable and source hashes, redacted outputs, exit codes, and fixture removal.

Exact binary `/usr/bin/restic`, version line `restic 0.18.0 compiled with go1.24.4 on linux/amd64`, SHA-256 `7135345e471b64fe43356f8cc665d33d9e52fb042ba39e5cff77310c46d46857`. Report SHA-256 `62c4058f102436b79308919d42cc31c16b6e4c0414ce7bf1020c641b83798ea4`. Linux kernel 6.12.101+deb13-amd64, x86_64, glibc 2.41, CPython 3.13.5. The report records the I3 baseline revision and the SHA-256 of the exact I4 runner and adapter sources.

| Command | Exit | Evidence |
|---|---:|---|
| `/tmp/bm-dev/bin/python tools/certify_restic.py --restic /usr/bin/restic --report tests/certification/restic-0.18.0-linux-amd64.json` | 0 | All five cases passed. Verified formats 1 and 2, bidirectional cross-format copy, distinct repository identities and passwords, same-metadata independent histories, tag rewrites, transitive copy, repeated copy, lock overlaps/conflicts, and interruption after destination packs were written. |
| `/tmp/bm-dev/bin/python tools/validate_handoff.py` | 0 | Static handoff and certified-report reference verified. |
| `/tmp/bm-dev/bin/python -m build --no-isolation --outdir /tmp/bm-i4-dist` | 0 | Built sdist and wheel. |
| `/tmp/bm-dev/bin/python -m pip --disable-pip-version-check install --no-deps --force-reinstall /tmp/bm-i4-dist/*.whl` | 0 | Installed final-profile wheel. |
| `/tmp/bm-dev/bin/python -m unittest discover -s tests/unit -q` | 0 | 65 passed. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_scoped_restic.py` | 0 | Installed profile and supervisor executed a temporary backup and explicit copy with different credentials. The resulting snapshot and provenance were verified, credentials were absent from SQLite and its witness, and fixture repositories were cleaned up. |
| Installed-wheel systemd regressions `linux_journaled_supervisor.py`, `linux_shared_storage.py`, and `linux_launch_boundaries.py` | 0 each | Private stdin and a 160 KiB result, scoped lifecycle dispatch, and crash fencing still passed after the transport changes. |

Two provisional certification runs exited 1. The first assumed equal-time snapshots returned in a stable order, so a read-only preview looked like a mutation. Sorting by the full snapshot ID corrected the comparison. The other assumed a missing requested source yielded exit 3. Direct observations showed exit 0 and a snapshot missing that source, whereas an existing unreadable source yielded exit 3. The final harness asserts both behaviors. The adapter requires a fresh snapshot whose paths match the actual backup request. The final report also records a missing explicit copy source yielding exit 0 without creating the requested destination representation. Production copy confirmation must independently revalidate both source and destination inventory.

Only the tested executable fingerprint is installed. Other Restic builds, operating environments, and repository formats have no certification here. I5 job orchestration, I6 production equivalence and convergence, I7 operations, and release acceptance remain pending.

## I5 job execution gate

- [x] D5/D8/D9/D13/D14/D44–D48/D60–D63/D73/D74/L5. Backup selection authenticates and verifies an explicitly registered Restic repository at its recorded storage and path. Primary selection precedes secondaries in declaration order. Linux launch uses a pinned repository directory and verifies the repository and new snapshot after backup. A failed backup never switches repositories.
- [x] D25–D36/D49/D50/D55/D56/L4. Transactional same-job exclusion and global admission, immutable batch configuration, ordered batch admission, per-job outcomes, cancellation and interruption fencing.
- [x] D7/D16–D18/D51–D53/D64–D79/L1. Ordered hooks and required-source validation, independent post and cleanup results, bounded and redacted output, hook identity configuration, durable history and per-job history retention.
- [x] Packaged migration 003 adds job-owner and phase runtime state, registered repository path bindings and command-to-claim links. Existing registrations can be explicitly bound only after verification of the already approved Restic ID.

| Command or fixture | Exit | Evidence |
|---|---:|---|
| `/tmp/bm-dev/bin/python tools/validate_handoff.py` | 0 | Handoff schemas, examples, reference DDL and unchanged I4 certification profile. |
| `/tmp/bm-dev/bin/python -m build --no-isolation --outdir /tmp/bm-i5-dist` and install final wheel | 0 each | Built source and wheel packages. Final tested wheel SHA-256 `51b61a948a81abfdbf11d6153bc0eaa3196833d36e8a884e5fc34a100b3390f9`. |
| `/tmp/bm-dev/bin/python -m unittest discover -s tests/unit -q` | 0 | 68 passed, including migration rollback, generation fencing, job/history admission and retention, and repository path replacement rejection. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_i5_job.py` | 0 | Real certified Restic backups to disposable repositories. Assertions cover explicit registration, no auto-init, verified primary and ordered fallback, wrong credential and ID isolation, source preparation, missing sources, partial backup with fixed target, hook timeout and cleanup, secret redaction, cancellation, crash fencing and later fresh invocation, batch concurrency and immutable queued configuration. |
| Installed-wheel integration regressions `linux_scoped_restic.py`, `linux_journaled_supervisor.py`, `linux_shared_storage.py`, `linux_launch_boundaries.py`, `linux_crash_fencing.py`, and `linux_atomic_claims.py` | 0 each | Scoped Restic backup/copy, recovery and storage claims, crash injection, and reverse-ordered repository claims. |

During fixture development, a crash test left a direct SQLite connection open and triggered the expected fail-closed WAL check. The final fixture uses the immutable state reader and verifies recovery without SQLite sidecars. A separate disposable native-password probe confirmed Restic strips file whitespace and does not strip environment password newlines. The final backup fixture verifies file-provider whitespace handling through the scoped adapter.

Verified on Linux x86_64 and CPython 3.13.5 with user systemd scopes. I5 used a marker-file storage provider for its multi-process orchestration fixture, real Restic repositories, and the installed exact binary profile. Actual block-mount verification remains covered by the separate I2/I3 loop fixture. Hook execution as the invoking identity was exercised. Switching to a different user or group requires deployment privileges.

Multi-destination backup jobs explicitly report partial while replication and convergence await I6. Retention, admin initialization, full operation administration, and dry-run/preflight remain scheduled for I7. Release acceptance remains pending.

## I6 replication and convergence

- D10–D12/D15/D16/D23/D33/D37–D39/R1–R10/G1/G3–G7. Normal replication freezes one complete inventory of the backup-receiving repository. Convergence follows cleanup and normal replication, accumulates the union of complete fresh observations, and preserves destination-only history. The phase records unavailable destinations, chunker mismatch warnings, and independent attempt outcomes. Retention remains deferred to I7 and never filters this phase.
- A8–A12/G5. Migration 004 adds a generation- and profile-scoped provenance index, frozen transfer metadata, and durable requirement outcomes. The launch journal links an attempt before dispatch and records the started boundary with the execution receipt. Only an unstarted pending requirement can select a replacement source. Failed started attempts remain blocked and verified successes remain immutable. Both repository directories stay pinned through the scoped copy. Repository claim sets serialize a common destination and allow tested shared-source overlap for independent destinations.
- A disposable exact-binary probe disproved the initial lineage-plus-ordered-tags comparison. Native copy accepts a destination-local snapshot ID as a lineage reference even when the destination itself has a different original ID. Native copy also treats reordered tags as a match. The initial comparison was replaced before I6 signoff. The corrected comparison preserves full native metadata and determines target representation separately from the lineage propagated by a source. See `tests/experiments/linux_i6_native_equivalence.py` and the linked upstream comparator in docs/08.

| Command or fixture | Exit | Evidence |
|---|---:|---|
| `/tmp/bm-dev/bin/python tools/validate_handoff.py` | 0 | Static handoff and installed I4 profile report reference validated. |
| `/tmp/bm-dev/bin/python -m build --no-isolation --outdir /tmp/bm-i6-dist` and final wheel install | 0 each | Built sdist and installed the wheel. Final tested wheel SHA-256 `d346b719637fbb2cabe2b301ecdc5b3bc1335b019c0689461e308ec226f35956`. |
| `/tmp/bm-dev/bin/python -m unittest discover -s tests/unit -q` | 0 | 80 passed, including normal copy scheduling, fixed-source and incomplete-enumeration failures, native comparison, generation-scoped provenance, and failure fencing. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_i6_native_equivalence.py` | 0 | Both formats passed the disposable alias and tag-order cases. Exact binary and observations are in `tests/certification/restic-0.18.0-i6-native-equivalence.json`. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_i6_replication.py` | 0 | Fixed full-history source, primary catch-up after fallback, divergent history, rewritten tags, independent post-hook failure, cleanup ordering, chunker warnings, and retention-independent replication. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_i6_faults.py` | 0 | A real started copy failure was isolated and stayed blocked. A successful copy exit followed by lost source evidence was unverified, preserving target data. A prelaunch failure could use a different verified source. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_atomic_claims.py` | 0 | Reverse-ordered claims from independent processes produced one complete set and one busy result. |

Normal replication uses up to four target workers per job. I6 model and lifecycle tests ran on Linux x86_64, Python 3.13.5, user systemd scopes, the certified Restic binary, and disposable repositories. I4 certification and its original report remain unchanged. Supplemental comparison evidence and the affected test assertions are indexed in the requirements traceability table.

I6 gate complete. The final installed-wheel regression ran `linux_i5_job.py`, `linux_crash_fencing.py`, `linux_launch_boundaries.py`, and `linux_scoped_restic.py` with exit 0 each. A provisional I5 regression required a later replica to contain the fallback backup despite the fixture's partial overall result. The I5 assertion now verifies that the later replica never took its own backup. The dedicated I6 fixture verifies confirmed fallback replication separately. No real backup repositories or mounts were used in these tests. I7 retention and operator commands, I8 hardening, and I9 release acceptance remain pending.
