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

## I7 operations and recovery

- D7/D21–D24/R3/G6. Integrated and standalone retention use each registered destination's exclusive claim. Integrated retention starts after normal replication and convergence. Forget and opt-in prune are separate journaled commands. Verification is separately scheduled or invoked.
- D6/D41–D45/A6/A7. Repository commands require the certified Restic profile and registered identity checks where applicable. Explicit init reserves the verified storage/path and checks for emptiness at launch. Approval verifies the full replacement ID and advances the registration generation. Explicit unlock uses Restic stale-lock handling.
- D51/D53–D55. Added durable operation listing and inspection, preserving the job records expected by existing callers. Operator cancellation remains available for jobs. Dry-run only plans. Preflight can acquire storage and verifies registered repository identities. Doctor defaults to read-only state and Restic checks.
- D19/D20/D57–D59/L2. Job and shared-storage release notifications use separate scoped attempts and persistent outcomes. Job notification follows durable job completion and claim relinquishment.
- Engineering contract 16 and operations contract 19. Recovery verifies the independent witness, manifest, migration checksums, SQLite integrity and registered repository identities. It rebuilds visible snapshot inventory and resets inherited release authority. An older backup with no usable live ledger is refused because newer process termination cannot be proved. The operations guide documents the manual registration path.

| Command or fixture | Exit | Evidence |
|---|---:|---|
| `/tmp/bm-dev/bin/python tools/validate_handoff.py` | 0 | Validated handoff schema, examples, 140 requirement rows, 40 scenario specifications and the exact I4 Restic profile reference. |
| `/tmp/bm-dev/bin/python -m build --no-isolation --outdir /tmp/bm-i7-dist` and wheel install | 0 each | Built sdist and installed wheel SHA-256 `5b400773591dd254a6e27e1311aea702f89eb28298d6bd7fcc6f60ed3f6ad95d`. Migration 005 is packaged. |
| `/tmp/bm-dev/bin/python -m unittest discover -s tests/unit -q` | 0 | 80 passed. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_i6_replication.py` | 0 | Convergence and normal replication regression, with a nonremoving fixture policy. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_i6_faults.py` | 0 | Real started-copy failure, fresh source-loss verification and alternate-source prelaunch replanning regressions. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_shared_storage.py` | 0 | Independent release and notification completion after the last claimant, two successful scoped notification attempts, no release or notification for externally held storage. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_i6_native_equivalence.py` | 0 | Verified destination-local lineage and reordered tag behavior for disposable repository formats 1 and 2 using certified Restic 0.18.0, SHA-256 `7135345e471b64fe43356f8cc665d33d9e52fb042ba39e5cff77310c46d46857`. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_i5_job.py` | 0 | Job admission, operator cancellation, cleanup, crash recovery, batch and history regression. An earlier run exposed the missing legacy `runs` field. The final wheel preserves it. |
| `/tmp/bm-dev/bin/python tests/experiments/linux_i7_operations.py` | 0 | Frozen four-snapshot copies before retention, two surviving snapshots per destination, injected primary forget failure with successful independent peers and job outcome, opt-in prune, check, explicit unlock, wrong-ID replacement refusal, nonempty-init refusal, read-only dry-run and preview, failed job notification, older recovery refusal and same-generation recovery with four verified repositories. |

The I7 implementation and scoped regression gate passed on Linux x86-64 with
CPython 3.13.5 and user systemd scopes. Fixtures used disposable Restic
repositories and marker-backed storage. No production repository or mount was
modified. Operator cancellation addresses job runs. Unsettled process evidence
continues to require explicit reconciliation. I8 hardening and I9 release
acceptance remain pending.

## I8 hardening and release tooling

- [x] D47/D76. Suppress credential-bearing captures after native truncation to avoid persisting a secret prefix. Verify file secret ownership, restrictive modes, final-component symlink rejection, FIFO rejection, complete-stream redaction and bounded capture.
- [x] A4/A6/A7/D56/D78. Recheck the intended Restic SHA-256 at launch and execute the verified open inode. Refuse a nonregular candidate. Keep read-only timeout cleanup bounded even if a killed process cannot exit. The inherited witness lock keeps conflicting work blocked until the process exits.
- [x] D25/D81–D85/D92/D103–D105/L3/A3–A5/A9–A12. Exercise launch crashes, live-child fencing, atomic claims, shared acquisition and release, real kernel mount topology, and SQLite migration rollback. No schema revision was needed for I8.
- [x] Contracts 16/19/20. Build reproducible wheel and source archives from pinned dependencies, add CI workflows and operator runbooks, and document the state upgrade helper. D106 stays paused.

### Security review

Core subprocesses use argument arrays. Restic credentials use the child
environment and the private scope request channel. Tests verify credentials
are absent from the persistent command journal. Native operations still rely
on registered identity, storage claims, scope termination evidence and Restic
locks. Kernel probes covered the known unmount and move-mount races without
widening the dedicated-mountpoint contract.

The first dependency audit reported three entries against setuptools 78.1.1
(`PYSEC-2026-3447` appeared twice and `GHSA-xmj3-9gf7-h52w` once). Both build
pins were updated to 84.1.0. The revised lock scan reported no known
vulnerabilities.

| Command or fixture | Exit | Evidence |
|---|---:|---|
| `tools/validate_handoff.py` on Python 3.12 and 3.13 | 0 | Static schemas, configs, 140 requirement links, 40 scenario definitions, and the installed exact-profile report reference. |
| `tools/build_release.py --epoch 1704067200 --output DIR`, twice per interpreter, followed by `diff -r` | 0 | Byte-identical within and across Python 3.12.16 and 3.13.5 on Linux x86-64. Wheel SHA-256 `ecf0bbfe67d4467035b57eb5eae44f8420bfb2e907ba0a49e0ce594921d360d0`. Sdist SHA-256 `3b5bd0322b25266c52baab6872405d97ecfef3d8ecf17e914f2d57c4836863a6`. Lock SHA-256 `b484fd4f981f15c79ec7b09502f9e0b40f16b3dbbe7a7a8ca4ef4925816ff0fd`. |
| `pip_audit --no-deps --disable-pip -r requirements-dev.lock --format json` | 0 | No known vulnerabilities for the Python 3.13 lock entries. Report `/tmp/bm-i8-dependency-audit-fixed.json`. |
| `tools/certify_restic.py --restic /usr/bin/restic --report /tmp/bm-i8-final-restic-certification.json` | 0 | All five disposable cases passed and cleaned up. Exact Restic 0.18.0 Linux amd64 SHA-256 `7135345e471b64fe43356f8cc665d33d9e52fb042ba39e5cff77310c46d46857`. Report SHA-256 `b2bee86135db801e6c5d3f811a72fb306f78dd7f18ac0c5ddb574d9940cfa908`. The installed certified profile was not replaced. |
| `tools/run_linux_regressions.py namespace --output /tmp/bm-i8-namespace` | 0 | Private tmpfs mount topology, descriptor unmount and move-mount race probes. All three fixtures exited 0. |
| `tests/experiments/linux_loop_lifecycle.py` in the dedicated network-disabled Docker fixture | 0 | Real ext4 acquisition, verified ownership/release, preservation of the external fixture mount, and cleanup of image, mount and loop device. |
| `tools/upgrade_state.py --config CONFIG --backup BACKUP` | 0 | Independently upgraded a disposable schema-4 SQLite state, confirmed generation advanced, and verified that the old schema and manifest were preserved in the backup. |
| `python -m pip check`, `python -m unittest discover -s tests/unit -q`, and `backup-manager --json version` on both final wheel installs | 0 each | 84 tests on Python 3.12.16 and 84 on 3.13.5. Coverage includes migration rollback, native binary replacement, nonregular executable refusal, truncated-secrets handling and bounded read-only timeout cleanup. |
| `pip_audit --no-deps --disable-pip -r /tmp/bm-i8-audit-all-targets.txt --format json` | 0 | Audited all 11 pins by including the Python 3.12-only typing-extensions entry. Report SHA-256 `d5fd563d249ce01f4145c15640175ad39b7444723b712d9ff35dd7783f83cb67`. |
| `/tmp/bm-i8-py312/bin/python tests/experiments/linux_i7_operations.py` | 0 | Final wheel repository inspection, retention failure isolation, recovery, notifications and operator endpoints in disposable scopes. |
| `/tmp/bm-dev/bin/python tools/run_linux_regressions.py scoped --output /tmp/bm-i8-scoped` | 0 | All 12 fixtures passed on Python 3.13.5. This build preceded the final read-only cleanup change, which was tested on the final wheel by the timeout unit injection on both interpreters and the I7 integration on Python 3.12. |
| `/tmp/bm-i8-py313-final/bin/python tests/experiments/linux_scoped_restic.py` | 0 | Final Python 3.13 wheel exercised certified backup and copy. The replaced executable was refused before launch and recorded terminally with no launch receipt. |

The I8 local gate passed on Linux 6.12.101 x86-64. The hosted workflows were
parsed locally. GitHub-hosted and dedicated self-hosted workflow executions,
an aarch64 Restic profile, and the full acceptance evidence matrix remain
release verification work. I9 release acceptance has not been signed off.

## I9 acceptance checkpoint

- [x] AT-001–AT-040. Bound the scenario definitions to executable probes and reviewed local Linux amd64 evidence. The acceptance harness retains exit codes, command logs, redacted CLI results, selected SQLite events, fixture cleanup observations, and report hashes.
- [x] A3–A5/A9–A12, D14/D19/D41/D47/D49/D55/D76, R4/R9/G3. Added live failure and concurrency evidence for stale locks, corrupt state, unsupported binaries, same-target serialization, different-target overlap, cancellation without termination proof, late release failure, incomplete enumeration, distinct identical-metadata history, and migration rollback.
- [x] A6/A7. Fresh exact-binary Restic certification passed locally for Linux amd64 formats 1 and 2.
- [ ] D89/D90, remaining confirmed requirement evidence, aarch64 certification and remote workflow execution. Release acceptance stays open.

| Command or fixture | Exit | Observed evidence |
|---|---:|---|
| `tools/validate_handoff.py` | 0 | Static consistency of installed certification reference, 140 traceability rows and 40 scenarios. |
| `tools/run_acceptance.py --parallel 2 --output /tmp/bm-i9-acceptance-final` | 1 | All 15 selected probes exited 0. At that run's plan revision, 37 scenarios had adequate evidence. Same-target serialization, different-target overlap and incomplete-inventory coverage were still open. The release gate correctly stayed closed. |
| `tools/run_acceptance.py --probe inventory --output /tmp/bm-i9-inventory` | 1 | Inventory probe exited 0. Two native enumeration failures leave the previous complete provenance intact and start zero copies. |
| `tools/run_acceptance.py --probe copy-claims --output /tmp/bm-i9-copy-claims-final` | 1 | Copy-claims probe exited 0. Same-target admission blocks while the first process is live. Distinct targets have two simultaneously live contained Restic processes and successful terminal receipts. |
| `tools/run_acceptance.py --probe identical --output /tmp/bm-i9-identical` | 1 | Identical-metadata probe exited 0. All three repositories retain both independently created logical identities, corroborated by SQLite provenance. |
| Review of the four completed bundles against the final assertion plan | 0 | Verified equal build-host/source-base metadata, each report SHA-256, each probe exit, and empty fixture directories. All 40 scenarios have passing probes on Linux amd64. `/tmp/bm-i9-reviewed/scenarios.json` SHA-256 `fa22e987214e42a91a6f7c6ae4748ba485e2947049f2aaf94cff10913723774b`. Review output explicitly records release as blocked. |
| Native certification within the matrix | 0 | All five cases passed and cleaned up. Report `/tmp/bm-i9-acceptance-final/native/certification.json` SHA-256 `649ff70da73a4e7cdd5c67ab199e790dd43ace0ad96d2dfffbd958f1d63db73f`. Restic 0.18.0 SHA-256 `7135345e471b64fe43356f8cc665d33d9e52fb042ba39e5cff77310c46d46857`. |
| `tools/run_linux_regressions.py namespace --output /tmp/bm-i9-namespace` and the network-disabled ext4 loop-device fixture | 0 each | All three namespace probes passed. Ext4 fixture verified physical lifecycle operations, external mount preservation and loop/image cleanup. |
| `pip_audit --no-deps --disable-pip -r /tmp/bm-i8-audit-all-targets.txt --format json` | 0 | No known vulnerabilities for the 11 pinned Python dependencies. Report `/tmp/bm-i9-dependency-audit.json`. |

The initial evidence export used an incorrect event timestamp column and was
discarded. The full matrix above used the corrected exporter. A preliminary
copy fixture attempted a storage acquisition while holding a repository claim.
The corrected fixture releases its inspection claim before acquiring the next
storage lease, then requests the atomically ordered copy claim set.

The traceability register now points 84 of the 139 confirmed requirements to
observed assertions. The remaining 55 IDs are listed in
`/tmp/bm-i9-reviewed/release-gate.json`. G2 is superseded and D106 remains
paused. Local evidence comes from a working-tree candidate based on I8 commit
`6d9f98e`. The remote acceptance workflow is configured for an isolated
runner and has not executed in this session.

The D89/D90 storage identity probe verifies that explicit
`storage recover disk --yes` refuses a newly observed physical identity and
preserves the prior binding and generation. The confirmed CLI grammar lacks
an explicit contract for approving the verified replacement independently of
repository approval. Treating generic recovery confirmation as replacement
approval would change what the operator authorizes. That workflow needs an
approved contract. The available host is Linux x86-64 and the installed
Restic registry has no aarch64 profile. Both block release signoff.

All 84 unit tests passed on CPython 3.12.16 and 3.13.5 with the exact
candidate wheel. The Python 3.12 dependency consistency check exited 0.
Final archive builds using `tools/build_release.py --epoch 1704067200` on
both interpreters and the archive directory comparison exited 0. Exact
hashes were wheel `ecf0bbfe67d4467035b57eb5eae44f8420bfb2e907ba0a49e0ce594921d360d0`,
sdist `b6b65610bc6e1e480c8811d6b95b1318daaa732ad13380146ad5971feb509ad2`,
and lock `b484fd4f981f15c79ec7b09502f9e0b40f16b3dbbe7a7a8ca4ef4925816ff0fd`.

## Post-I9 storage replacement and traceability

Operator approval resolved the D89/D90 CLI contract. Run
`storage approve-replacement STORAGE_ID --expected-id PHYSICAL_ID --yes`
after verifying and mounting the replacement. The command advances the
storage identity generation and records the approved old and new bindings.
Active claims, unresolved lifecycle or repository work, outstanding owned
mounts, identity aliases, and unverifiable observations prevent approval.
Repository registration and approval remain independent. The acceptance
fixture exercises both approval orders and confirms the provider's mount
marker is preserved.

| Command or fixture | Exit | Evidence |
|---|---:|---|
| `tools/validate_handoff.py` | 0 | Static schemas, examples, traceability and certification reference. |
| Installed-wheel unit suite on CPython 3.12.16 and 3.13.5 | 0 each | 85 tests each. New tests reject active claims, ownership, unresolved lifecycle, and aliases, then check one generation advance, one audit event and idempotence. |
| `tools/run_acceptance.py --parallel 2 --probe boundaries --probe operations --probe unit --output /tmp/bm-storage-approval-gate` | 1 | All three probes exited 0. Exact-ID approval was required and left the registered repository ID and generation intact until separate repository approval. Subset runner exit 1 records an open release gate. |
| `tools/run_acceptance.py --probe followups --output /tmp/bm-release-followups` | 1 | Probe exited 0 after validating empty credential isolation, default required sources, both history formats, and ordered independent cleanup. Subset runner kept the release gate open. |
| `tools/build_release.py --epoch 1704067200` on both versions followed by archive comparison | 0 | Byte-identical archives. Wheel SHA-256 `7bec4f7e96e243479cb79ba4fe7995b8abef8fe2023dffcf22a610cdc8777d8a`; sdist SHA-256 `95389ee501f2f170e967634b16421e1c9742375c8eeee6494acc02cc92cd24e4`. Wheel member bytes matched the installed test wheel. |

Assertion-level traceability covers 107 of 139 confirmed requirements.
The 32 open rows, exact aarch64 certification and authenticated dispatch
of the remote release workflow remain release gates. GitHub is reachable
over SSH and the public Actions API supports read access. No
non-interactive GitHub HTTPS credential is configured for manual dispatch.

### Full matrix and hosted validation

- `tools/run_acceptance.py --parallel 2 --output /tmp/bm-storage-approved-full`
  exited 1. All 19 probes exited 0 and all 40 scenarios had passing evidence.
  The release gate lists the 32 remaining requirements and the missing installed
  ARM64 profile. Scenario manifest SHA-256
  `30395a07e8dae8f62c5a73c93a0d4f5d2a1a6390298ecab1a3226880371c5fa4`.
- The hosted build and dependency audit completed successfully on validation
  commit `621a2f5`. The push created
  `codex/storage-replacement-approval`. Remote main was left at its prior
  commit. [Hosted build and audit](https://github.com/RishikeshPVijay/backup-manager/actions/runs/45001909420)
- Commit `8af6596` ran the native GitHub Linux ARM64 candidate job. The
  architecture and upstream archive verification, disposable certification,
  and artifact upload steps all concluded successfully. The verified
  decompressed candidate SHA-256 is
  `a4aa97c7d651e87eb5ade27e32ae0f457887ee16b86958845e781aa18e524d03`.
  The job uploaded artifact `14745954011` with SHA-256
  `0669c47ed00d39836e5ee14c7c0102e622a9397e108bed143889e6a1e5088762`.
  [Native ARM64 run](https://github.com/RishikeshPVijay/backup-manager/actions/runs/45003235957)
  [Upstream Restic release](https://github.com/restic/restic/releases/tag/v0.18.0)

The unauthenticated GitHub artifact download returned HTTP 404 from the web
endpoint and HTTP 403 from the API. The exact candidate completed the remote
certification job. Installing its profile still requires the report bytes,
digest verification, and installed-wheel platform acceptance. The separate
manual release workflow also requires authenticated dispatch. Release signoff
remains open.

## ARM64 artifact intake and lifecycle evidence

Added `tools/verify_arm64_report.py` to verify the original artifact ZIP from
the successful native GitHub run. It checks the recorded archive digest and
exact report member, run commit, binary and source hashes, native architecture,
five required cases, command evidence, and fixture cleanup. Its output is
candidate metadata for review. The original artifact was unavailable in this
workspace. The ARM64 profile is still pending.

Added assertion-level evidence for D82 and D93. The tests inject unconfirmed
lifecycle termination and show that another acquisition remains fenced. A
separate test reads the persisted verified ownership receipt by generation.
The register now has evidence for 109 of 139 confirmed requirements.

| Check | Exit | Evidence |
|---|---:|---|
| Installed-wheel unit tests on CPython 3.12 and 3.13 | 0 each | 90 passed on each version, including verifier tests with fabricated inputs and the two new lifecycle assertions. |
| `tools/validate_handoff.py` | 0 | Static schemas, examples, traceability, and installed profile reference. |
| `tools/build_release.py --epoch 1704067200 --output /tmp/bm-arm64-review-dist` | 0 | Built the wheel and sdist. The source archive contains the verifier and its tests. |

Authenticated retrieval of the original ARM64 artifact, installed-wheel ARM64
acceptance, manual release workflow dispatch, and 30 remaining requirement
rows still block release signoff. The current workspace treats Git metadata as
read-only, so this checkpoint is present as working-tree changes.

## ARM64 certification registered

The supplied original `restic-linux-arm64-candidate-report.zip` passed
`tools/verify_arm64_report.py` (exit 0). The checked-in report SHA-256 is
`bd3590a53c3b9d913c705fb216bfde46946c4133590d77394c31b713678a9bcd`.
All five native cases passed, including formats 1 and 2, both cross-format
directions, and held-copy overlap for backup, source reads and copies to
distinct destinations. Both formats reported exit 11 for conflicting forget,
prune and check. Registered the exact ARM64 binary and those observed overlap
modes. [Native run](https://github.com/RishikeshPVijay/backup-manager/actions/runs/45003235957)

- `tools/validate_handoff.py` exited 0 with both installed profiles.
- Built wheel and source archives. The installed wheel passed 91 unit tests
  on each of Python 3.12 and 3.13, including acceptance of the exact ARM64
  version/fingerprint pair and refusal of a mismatched architecture version.
  Wheel SHA-256 `ad7ffaa057c720d9f75f19827eb088353b8ab5c32d92a7383baa20c23a6946cd`.
- Parsed the manual CI acceptance matrix and verified its architecture and
  digest entries against the registry. One native copy fixture now resolves
  Restic from PATH, matching the acceptance runner's configured binary.
- Installed-wheel ARM64 acceptance and authenticated CI dispatch remain
  pending. The local host is x86-64 and its user scope bus check returned
  operation not permitted. Thirty requirement assertions remain open.

## I9 local acceptance follow-up

- [x] D81. Inject a confirmed release timeout and assert fresh inspection,
  retained ownership, and a recovery fence that prevents automatic release retry.
- [x] D100. Assert that release fences acquisition, that the pre-release
  observation expires when the release settles, and that fresh absence permits
  a new acquisition plan.
- [x] D105. Inject a deadline and cancellation separately while an incompatible
  repository holder is active. Assert neither waiter takes claims and the
  competitor can acquire after the holder releases.

| Command | Exit | Evidence |
|---|---:|---|
| `python3 tools/build_release.py --epoch 1704067200 --output /tmp/bm-unblocked.tbPKSD/dist` | 0 | Built the wheel and sdist. The wheel SHA-256 is `ad7ffaa057c720d9f75f19827eb088353b8ab5c32d92a7383baa20c23a6946cd`. |
| `python3 tools/run_acceptance.py --parallel 2 --probe unit --probe migration --probe native --probe lineage --output /tmp/bm-unblocked.tbPKSD/evidence` | 1 | All four probes exited 0. All five exact Linux x86-64 Restic 0.18.0 cases passed and removed their fixtures. The subset kept the release gate open. |
| `python3 tools/run_acceptance.py --parallel 2 --probe unit --probe migration --probe claims --output /tmp/bm-unblocked.tbPKSD/final-checks` | 1 | All three probes exited 0. The wheel payload passed 93 tests on Python 3.13, the injected migration preserved the prior state, and independent processes produced one complete claim set and one busy result. |
| `python3 tools/validate_handoff.py` | 0 | Static validation checked both registered profile report references. |

The traceability register now has assertion evidence for 112 of 139 confirmed
requirements. The local probes used disposable fixtures and loaded the package
from the built wheel. The user systemd bus is unavailable in this environment.
The full x86-64 and ARM64 workflow artifacts and 27 requirement rows remain
open for release signoff.

## I9 expanded local evidence

- [x] D1, D2, D3, A1, A2. Audit the local CLI and wheel. Check separate
  repository paths and registered Restic IDs, job document composition, real entry-point metadata discovery and
  required storage capabilities.
- [x] D10, D24, D30, D39, L4. Check fallback and fresh replication discovery,
  explicit selected-target verification, batch order and slot retention.
- [x] D56, D64, D65, D69, D75, D77, D79, L1. Exercise job and operation
  deadlines, static source preparation, hook ordering, credential resolution,
  independent hook failures and a cleanup deadline after job expiry.
- [x] D80, D86, D94, D95, D97, D98. Check independent storage deadlines,
  canonical identity, conflicting physical observations, disconnect refusal
  and withdrawal of only the expired participant in a shared acquisition.

The exact assertions are recorded in the traceability register. The unit
checks use real configuration, package metadata and isolated SQLite state.
Where the assertion reaches a worker boundary, the tests supply a controlled
response or clock. Full scoped execution remains a release gate.

| Command | Exit | Evidence |
|---|---:|---|
| `python tools/run_acceptance.py --parallel 2 --probe unit --probe claims --probe migration --output /tmp/bm-local-acceptance.NrWpDc/release-local` | 1 | Each probe exited 0. The installed wheel passed 113 tests on Python 3.13. The subset produced two passing scenarios and kept release acceptance open. |
| `python tools/run_acceptance.py --parallel 2 --output /tmp/bm-local-acceptance.NrWpDc/installed-probes` | 1 | All 19 probes ran. Unit, claims, migration, lineage and native certification exited 0. Fourteen scoped probes failed. Each probe reported an empty fixture directory afterward. |
| `python tools/run_linux_regressions.py namespace --output /tmp/bm-local-acceptance.NrWpDc/namespace` | 0 | All three probes passed with disposable mounts and reported cleanup. |
| `systemctl --user show-environment` | 1 | User bus connection denied. The native supervisor probe could not confirm scope termination, and installed-wheel fixtures requiring repository registration returned `TERMINATION_UNCERTAIN`. |
| `docker image inspect backup-manager-disposable-loop-test:latest` | 1 | Docker socket access denied and the current workspace exposes no loop-control device. The ext4 regression needs its disposable runner. |
| `python tools/build_release.py --epoch 1704067200` (two output directories); `diff -qr` | 0 each | The two builds produced byte-identical archives. The wheel exposes one local console entry point. The sdist includes the added test module. |
| `python tools/validate_handoff.py`; `python tools/verify_arm64_report.py`; `cmp` | 0 each | Static validation passed and the supplied ZIP extracted to the exact registered JSON bytes. |

Assertion coverage is 136 of 139 confirmed requirements. D37 needs a target
disconnect injected during a scoped copy, with peer success and no retry
asserted. D102 and L3 need native evidence of shared lifecycle coordination
and cross-process physical reconciliation. Both manually generated workflow
artifacts remain pending.

## GitHub-hosted Linux workflow

Run `45006727526` queued for an x64 self-hosted runner. The repository has
no registered self-hosted runners. The replacement workflow uses
`ubuntu-24.04` and `ubuntu-24.04-arm`. It checks the exact Debian amd64
package and upstream ARM64 archive before extraction, checks the registered
executable digest and version, and verifies a populated cgroup v2 scope under
the runner user. Acceptance artifacts retain the preflight log if setup
fails. The ext4 fixture runs inside a private mount namespace with a private
`/dev` on its own hosted VM.

| Check | Exit | Evidence |
|---|---:|---|
| Installed-wheel `python -m unittest discover -s tests/unit` | 0 | 113 tests passed on Python 3.13. |
| `python tools/validate_handoff.py` | 0 | Both registered profile report references validated. |
| Workflow YAML, matrix and shell checks | 0 | Both runner images and registered binary pins matched. All workflow run blocks and the helper passed Bash syntax checks. The helper refused a non-hosted runner. |
| Debian download probe from this workspace | 6 | DNS unavailable. The package name and archive checksum were checked against the Debian package listing. |

Hosted preflight, loop execution and both acceptance artifacts still need a
new workflow run on the default branch. Release acceptance remains open.

## Hosted acceptance evidence review

Reviewed both supplied acceptance archives for commit
`1a0e9fb833c596b051894e9965f18a24a5911717`. ZIP integrity checks and
all recorded probe report hashes passed. Both architectures matched the
registered Restic version and fingerprint, passed 19 probes and 40 scenarios,
and reported empty fixture directories. Both native Restic reports passed all
five certification cases. The release reports had no missing profiles or
open scenarios. The three unsigned requirements in each report account for
the acceptance job exit of 1.

| Supplied archive | SHA-256 |
|---|---|
| `release-acceptance-evidence-x64.zip` | `f05543a62538f53437d0deb79dd14a2679e31b0eec048f33a6219dad4e8da9be` |
| `release-acceptance-evidence-arm64.zip` | `7a71ca17fe494d1f63a3f9d622c51b3674fadf3427e45a19170c0ee21dc2440c` |

Verified L3 using the hosted shared and crash probes plus the existing
storage-driver assertions. The independent clients preserved a freshly
observed external mount, the shared trace recorded five settled acquire or
release operations, and owner recovery released the fenced claim after
verified scope exit. The storage-driver tests cover durable ownership and
fresh physical inspection. Traceability now covers 137 of 139 confirmed
requirements.

D37 still needs a target-disconnect assertion during scoped copy. The hosted
fault probe injects a bad password or source loss. D102 still needs an
assertion connecting backup, maintenance and repository administration to
the same storage lifecycle. The acceptance ZIPs do not contain the separate
`scoped` job output. Release acceptance remains open.

## Hosted scoped regression archive review

`linux-regression-results.zip` passed ZIP integrity checking. Its SHA-256 is
`f84935b237f5af567f8743624035f221bcbbc626f4c6b54ffcf4e6b8e286836a`.
Hosted preflight, native Restic certification and all 12 scoped probes passed.
All three mount-namespace probes exited 2 with Python reporting permission
denied opening the fixture in the checkout. The workflow had run the suite
through sudo before each fixture mapped its invoking user to namespace root.

The workflow now runs the namespace suite as the checkout owner. On the
disposable hosted VM it temporarily permits unprivileged user namespace
capabilities if Ubuntu's AppArmor sysctl requires it, then restores the
original setting when the step exits. Each probe still creates a private
mount namespace. The local namespace suite exited 0 with all three fixtures
passing. Hosted verification of this workflow change remains pending.

## Remaining acceptance assertions

- [x] D37 / AT-040. The faults probe removes access to the disposable target
  path in the scoped Restic launch callback after the durable receipt. It
  checks partial job status, one started and blocked target attempt, no
  successful evidence for that attempt, and a verified copy to the peer.
- [x] D102 / AT-021. The boundaries probe holds one on-demand storage claim
  while three independent CLI processes run backup, verification, and
  repository registration. It checks that each uses the same storage
  resource and generation, that the holder keeps the mount acquired, and that
  one release settles after the final claim is dropped.

The checklist tracks assertion implementation. Both requirement rows stay
open pending hosted execution. The probes emit named SQLite evidence records
for review in `faults/cli-and-sqlite.jsonl` and
`boundaries/cli-and-sqlite.jsonl`.

| Command | Exit | Evidence |
|---|---:|---|
| `python tools/validate_handoff.py` | 0 | Validated handoff and both profile references. |
| `python -m py_compile tests/experiments/linux_i6_faults.py tests/experiments/linux_i9_boundaries.py` | 0 | Both changed probes compiled. |
| `python -m unittest discover -s tests/unit` | 0 | 113 tests passed against the installed wheel. |
| `python tools/run_acceptance.py --parallel 2 --probe faults --probe boundaries --output /tmp/bm-d37-d102.M4BxAv/evidence` | 1 | Both probes stopped at disposable repository registration with `TERMINATION_UNCERTAIN`. This workspace cannot access the user systemd bus. Neither new assertion was reached. |

## Final requirement evidence intake

Reviewed the four supplied archives from the hosted workflow running
`f221f92c29b2b05bb55fe3b73f18ae6e6abb4891`. ZIP integrity and all recorded
acceptance probe report hashes passed. Both architectures matched their
registered Restic binaries. For each architecture all 19 probes exited 0,
all 40 scenarios passed, and all five native certification cases passed
and cleaned up. The scoped archive confirms all 12 scoped and all three
namespace probes exited 0. The loop log confirms acquisition, ownership,
release, external reuse and fixture cleanup.

| Supplied archive | SHA-256 |
|---|---|
| `linux-regression-results.zip` | `5bd943145df23ec19f48dcd1dfdc3f5e88e9e302ccbc5fc83b2e49b647659a12` |
| `loop-device-regression.zip` | `f24ce578c15faf9c3c6716dca9f9e4589b5d0a558503ecc29bd67a9edef52b8c` |
| `release-acceptance-evidence-x64.zip` | `985a32fc377302a4c7dcbb6d2521b5aebdbc0a9c7969263461aa4690844a0c03` |
| `release-acceptance-evidence-arm64.zip` | `0f7ab0856d0325239531e4d0c9426e8cea89c27a8115a789bac8e42a1ec8483a` |

- D37. Both named trace records identify exactly one started target attempt
  as `UNVERIFIED` with `COPY_INVENTORY_UNAVAILABLE` and confirm peer success.
  The probe also asserted that the disconnected target requirement was
  blocked and that no copy evidence was accepted for that attempt.
- D102. Both named records show three distinct CLI owners with released
  claims on `disk` generation 1. The provider recorded one acquisition and
  one release. The probe asserted that the resource remained acquired until
  the holder dropped the final claim.

Both acceptance jobs exited 1 because the checked-out traceability register
still listed D37 and D102 as unsigned. This review signs off both requirements
and brings traceability to 139 of 139 confirmed requirements. A fresh
default-branch run must confirm that the release gate exits 0 with the updated
register. Release acceptance remains open.

## I9 release acceptance complete

Reviewed the replacement artifacts for the clean default-branch commit
`9d6213205ac392fc5dafdf2fd6083e018afcc2ec`. Both acceptance reports have
empty lists for unsigned requirements, missing profiles, and open scenarios.
Each architecture passed all 19 probes, all 40 scenarios, and all five
native Restic certification cases. D37 and D102 evidence passed again.
All 12 scoped and three mount-namespace regressions passed. The ext4
loop fixture passed acquisition, ownership, release, and external reuse
and reported cleanup.

The archive review checked ZIP integrity, commit and profile fingerprints,
every recorded acceptance probe output hash, and fixture cleanup. Traceability
covers all 139 confirmed requirements. The earlier packaging and dependency
audit evidence is recorded above. This signoff records successful Linux
release acceptance for the tested commit.

| Supplied archive | SHA-256 |
|---|---|
| `linux-regression-results.zip` | `a167b54e32242ee05c3a66cebb4a9f6558ffaf649a43f619411c4460e94b230c` |
| `loop-device-regression.zip` | `196d950281c8bd22863abc79a80365beb3fcec40e18001117e8985e309785602` |
| `release-acceptance-evidence-x64.zip` | `7861128b2f3a083bc6f813d9c31b1135609b9ef7102269008b33c66a0ef09b83` |
| `release-acceptance-evidence-arm64.zip` | `f63b2e7ce2107b68d3d4430529e174bb74d26f50c0af1144281df934770e5c34` |
