# Backup Manager — Consolidated Product Specification

**Version:** 1.0 (product-definition baseline)  
**Date:** 2026-11-21  
**Status:** Product-policy audits for replication and lifecycle closed; architecture and implementation not approved.  
**Authority:** Confirmed D1–D105, R1–R10, G1–G7, L1–L6 and subsequent explicit shared-retention clarification. D106 is paused.

> This document consolidates previously confirmed product decisions. It does not approve new architecture, defaults, interfaces, or implementation details. Where wording is compressed, the original decision remains authoritative. Later explicit decisions supersede earlier incompatible wording only as identified in the change register.

## 1. Purpose and boundaries

Backup Manager is a local-first, cross-process backup **orchestrator**, initially using Restic. It coordinates jobs, backup-source selection, repository replication, multi-repository history convergence, retention, verification, storage acquisition/release, hooks, CLI, logs, and history. It does not reimplement Restic encryption/deduplication/pruning; it does not replace OS mounts, external schedulers (systemd/cron), or application-specific consistency hooks. Initial scope excludes a web UI, remote agents, and a required daemon.

A **job** represents a logical workload (e.g. Immich) with configured sources, hooks, destinations, and a single job-level retention configuration. Each destination has a separate Restic repository for that job. A **storage resource** is a separately coordinated underlying resource and can contain repositories for multiple jobs.

## 2. Normative end-to-end execution

1. Validate a snapshot of the invoked job configuration, including required definitions and credentials references; enforce job admission and global concurrency.
2. Acquire/validate candidate storage resources and registered repositories. Select the configured primary if usable, otherwise the first usable secondary in declaration order. If none usable, fail and clean up.
3. Execute pre-backup hooks sequentially, attempting all. Any pre-hook failure aborts the backup; skip source validation and post-backup hooks; run cleanup hooks after preparation has begun.
4. After successful preparation, validate required/optional source availability. Fail if any required source is missing or all sources are missing. Run Restic backup against the selected repository. A successful backup requires successful Restic exit and confirmation of the expected newly created snapshot; Restic partial/error exits count as failure even if a snapshot exists.
5. Run post-backup hooks only after successful backup. Their failures are independent. Run cleanup hooks after preparation has begun, in declaration order, with independent bounded timeout; failures do not negate a successful backup.
6. After successful backup, perform **normal replication** from the fixed backup-receiving repository to other available destinations, copying complete source snapshot history. Re-evaluate availability at the replication phase. A failure is isolated per destination; do not switch normal replication source mid-run.
7. Perform **history convergence** across available configured repositories, propagating the union of snapshot histories, including fallback-originated snapshots, by additive required transfers. No replication or convergence transfer deletes destination-only snapshots. Each transfer has a defined expected source snapshot set, successful Restic copy, and destination representation confirmation. Fail and report incomplete transfers when enumeration or required snapshot representation fails; preserve successful transfers and Restic-managed partial data.
8. **Only after normal replication and convergence**, apply the **same job-level retention policy** independently at each available destination. There is no pre-transfer retention-eligibility filtering. Snapshots transferred during convergence may be removed immediately by retention. Retention failures are independent.
9. Relinquish each job's resource claims once its own work is complete. A shared resource remains acquired while other operations use it. Job completion/notification does not wait for unrelated operations to finish; shared resource physical release has an independent persistent lifecycle outcome and notification.
10. Persist job/phase/transfer outcomes and history, report CLI status/exit codes, and invoke configured notifications. Notification failures are independent.

A backup failure prevents normal replication and convergence for that invocation. A successful backup with any required unavailable destination, failed normal replication, or failed required convergence transfer is **partial success** rather than full success. Retention, hook, notification, and storage-release failures remain independently recorded; they do not redefine the successful Restic backup itself.

## 3. Functional requirements by domain

### 3.1 Repository registration, integrity, and credentials

- Repositories are initialized or registered **only by explicit admin action**; never silently initialize during backup.
- Init requires absent/empty target; reject nonempty paths. Existing repositories require explicit registration and repository-ID validation. Changes to expected repository ID require explicit admin approval.
- Validate storage acquisition, repository path, expected repository identity, and credentials before considering a destination usable. Credential failures isolate that destination.
- Secrets initially come from files and environment through an extensible interface; never disclose known secrets in logs. No automatic Restic unlock; unlock is an explicit admin action.
- Different chunker parameters between registered source and destination are allowed, with an efficiency warning.

### 3.2 Replication and convergence

- Normal replication is **source-fixed** for the run and complete-history, even if some other repository contains extra history.
- Convergence is a distinct later phase. It automatically propagates histories created at any available destination, including when a secondary was the temporary backup destination.
- Additive semantics: neither normal replication nor convergence deletes destination-only snapshots. Retention may delete snapshots afterward according to job policy.
- Restic-native snapshot equivalence and copy semantics are authoritative; optional persistent index is advisory and must be revalidated against repositories. Repository-local snapshot IDs alone are insufficient.
- Each required transfer has its own outcome. Failure/unavailability does not undo other transfers. No automatic rollback or in-run retry after failed transfers. Future independent runs may use Restic's normal continuation/deduplication behavior.
- Failure to enumerate the required complete snapshot set aborts the affected replication phase. If an expected snapshot disappears during transfer, fail the affected transfer and report missing/unconfirmed snapshots rather than silently reducing the baseline.
- Convergence failure or required destination unavailability contributes to partial job outcome when backup succeeded.

### 3.3 Retention and verification

- Exactly **one retention configuration per job** applies to all its destinations. Execute independently at each available destination, after replication and convergence; isolate errors.
- Retention may also be run as a separately scheduled/admin operation.
- Verification is separate scheduled/admin work, not mandatory for each backup. Snapshot representation confirmation after copying is not a full integrity scan.
- History/log retention is distinct from Restic snapshot retention.

### 3.4 Hooks and source consistency

- Pre, post-backup, cleanup, notification, and storage lifecycle hooks have documented phase context. Application consistency and filesystem snapshot orchestration remain the workload's responsibility.
- Pre-hooks are sequential, all attempted. Any failure aborts backup and skips source validation. Post-backup hooks run only after successful backup. Cleanup hooks run whenever preparation began, including pre-hook failure/cancellation where feasible; run in declaration order.
- Static source paths may be made available by hooks; no dynamic source substitution. Required sources default to required; optional sources can be skipped and reported; no empty-source backup.
- Hooks support executable/args, working directory, extra environment, secret references, configurable execution identity, bounded stdout/stderr, redaction best effort, and timeout/termination of process groups. Detached descendants may escape termination; crash cleanup cannot be guaranteed.

### 3.5 Storage lifecycle and ownership

- A canonical storage resource ID is shared across jobs and all operations, including maintenance/admin. Validate underlying physical identity where possible. Reject confirmed duplicate physical identities under distinct IDs; require explicit admin approval for changed identity.
- If physical identity cannot be verified, require a stable explicit coordination identity. Equal identities coordinate as one resource, but different IDs are not proof of physical distinctness.
- Reuse already-mounted validated storage without remounting. Preserve externally mounted storage after use; only physically release storage whose lifecycle Backup Manager owns.
- Concurrent acquisition requests join one attempt; waiting participants can withdraw independently. If last participant withdraws, cancel the unused acquisition, confirm termination, reconcile races, and release owned storage if needed. Later independent requests may retry without cooldown.
- While release is running, new acquisition waits for completion and then freshly validates; it cannot cancel release. Shared storage releases after final dependent relinquishes claims.
- Persist coordination claims, lifecycle ownership, operation identity and termination state. Recover stale owners only after confirmed termination. Durable records are evidence, not authority: reconcile against actual physical state before reuse/release. Do not automatically release orphaned storage on stale-claim recovery.
- On timed-out/unconfirmed lifecycle operation termination, block conflicting operations until termination is confirmed. Acquisition/release have independent bounded timeouts. Disconnect mid-operation fails affected work without automatic reacquisition.
- Job completion and resource lifecycle completion are distinct; resource lifecycle outcome and notification persist independently.

### 3.6 Cross-process concurrency

- No overlapping invocations of the same job: reject/skip, do not queue. Different jobs may run concurrently.
- Configurable global admitted-job limit. Individual jobs reject when full; batch jobs wait for slots in invocation order. Once admitted, a job holds a slot while waiting for repository/storage access until job completion.
- Repository-level coordination is separate from storage coordination, cross-process, and operation-specific. Compatible operations may overlap; incompatible operations wait with timeout/cancellation. Native Restic locks remain additional safeguards; no automatic unlock.
- Multi-repository claims are acquired in deterministic global order, respecting compatibility and timeout/cancellation; storage ordering must be incorporated in architecture.
- **D106 remains paused:** fairness/admission ordering among waiting repository operations is not yet confirmed.

### 3.7 Failure, cancellation, and status

- Graceful cancellation prevents new operations, requests active Restic/process-group termination, performs bounded applicable cleanup/release, and records cancellation. Job, operation, hook, acquisition, cleanup, and release timeouts have separately applicable limits.
- Interrupted/crashed operations are detected/reported on subsequent invocation, without automatic resume/retry; Restic may reuse its retained partial transfer data on a later fresh invocation.
- Overall job status: full success if backup and all required replication/convergence succeed with destinations available; partial if backup succeeded but required destination replication/convergence is incomplete; failure if backup failed. Independent hook, retention, notification, and storage lifecycle outcomes are separately visible.
- CLI exposes distinct full/partial/failure exit codes. Batch exit precedence: failure > partial > full. No independent batch outcome classification is required beyond aggregate exit status.

### 3.8 Configuration, CLI, reporting

- Strict validation at invocation, report all errors, reject unknown fields; batch config is snapshotted for all requested jobs at batch start and invalid jobs do not prevent valid jobs running.
- Persistent structured per-job execution history, independently configurable history retention by age/count, human-readable and JSON CLI history, and detailed operational logs.
- Side-effect-free dry-run planning is distinct from operational preflight, which may acquire storage for read-only checks and then release it.
- Admin CLI supports explicit repository init, registration/identity approval, unlock, inspection, retention, and verification. Exact command schema remains open.
- Notifications are external scripts/commands with structured outcome context; failures do not change job status and are not retried automatically. Job and shared-resource lifecycle notifications are separate.

## 4. Decision register — D1–D105

The following entries are concise traceability labels, not replacements for the original detailed decisions.

| ID | Confirmed decision |
|---|---|
| D1 | Orchestrator scope and exclusions |
| D2 | One Restic repository per job per destination |
| D3 | Job is logical workload with sources, hooks, destinations, retention |
| D4 | Storage resource independently coordinated and shareable |
| D5 | Usability requires acquired storage, recognized repo, working credentials |
| D6 | Explicit repository initialization only |
| D7 | Separate backup, replication, retention outcomes |
| D8 | Primary fallback to first usable secondary |
| D9 | Secondary fallback follows declaration order |
| D10 | Re-evaluate destinations at replication phase |
| D11 | Fixed backup-receiving repository as normal replication source |
| D12 | Isolate target replication failure |
| D13 | Backup failure aborts backup and replication; no mid-run fallback |
| D14 | Backup success requires successful Restic exit and new expected snapshot |
| D15 | Replicate after backup confirmation and post-backup hooks |
| D16 | Post-backup hook failures independent; replication continues |
| D17 | Failed pre-hook aborts backup after pre-hook attempts |
| D18 | Hooks sequential declaration order; continue within phase on failure |
| D19 | Storage release failure independently reported |
| D20 | Release shared storage after final dependent operation |
| D21 | Retention integrated or separately scheduled/admin |
| D22 | Retention independently executed at each destination (shared policy clarified later) |
| D23 | Copy complete source snapshot history |
| D24 | Verification separate from every backup |
| D25 | Detect/report interrupted operations, no automatic resume |
| D26 | Reject overlapping same-job invocation |
| D27 | Different jobs may execute concurrently |
| D28 | Configurable global job concurrency limit |
| D29 | Individual rejects at limit; batch waits |
| D30 | Batch invocation order |
| D31 | Batch job failures isolated |
| D32 | Aggregate batch CLI exit status |
| D33 | Overall job outcome based on backup and replication (extended by G7) |
| D34 | Full/partial/failed job status (extended by G7) |
| D35 | Distinct CLI full/partial/failure exit codes |
| D36 | Batch exit precedence failure > partial > full |
| D37 | Target disconnect: fail target, continue others, no retry |
| D38 | Source disconnect: no normal replication source failover |
| D39 | Storage acquisition failure marks unavailable; later replication re-evaluation |
| D40 | No usable destination: immediate failure and cleanup |
| D41 | No automatic removal of stale Restic locks |
| D42 | Admin repo init/unlock/inspection with lifecycle handling |
| D43 | Init only absent/empty path; reject nonempty |
| D44 | Enforce expected repository ID; explicit replacement approval |
| D45 | Explicit registration of existing repository |
| D46 | Bad repository credentials isolate destination |
| D47 | File/environment secrets via extensible interface; no logging leaks |
| D48 | Missing secret isolates destination |
| D49 | Strict per-job invocation validation, all errors, unknown fields rejected |
| D50 | Batch snapshots all job configs at start |
| D51 | Persistent structured history and operational logs |
| D52 | Configurable history retention by age/count |
| D53 | Human-readable and JSON history CLI |
| D54 | Side-effect-free dry run versus operational preflight |
| D55 | Graceful cancellation and cleanup |
| D56 | Configurable job and operation timeouts |
| D57 | External script/command notification hooks |
| D58 | Notification after lifecycle cleanup (refined by L2 into separate job/resource completion) |
| D59 | Notification failure independent; no retry |
| D60 | Required/optional source paths |
| D61 | Sources required by default |
| D62 | All sources missing means failure, no empty snapshot |
| D63 | Restic partial/error exits count as failed backup |
| D64 | App consistency delegated to hooks/workload |
| D65 | Filesystem snapshots via hooks only |
| D66 | Distinct post-backup and cleanup hook phases |
| D67 | Cleanup after backup phase before replication |
| D68 | Cleanup failures independent |
| D69 | Success ordering: pre, backup, post, cleanup, replication |
| D70 | Cleanup hooks declaration order |
| D71 | All cleanup hooks attempted once preparation begins |
| D72 | Hook context via environment variables |
| D73 | Static source paths; pre-hooks may prepare them |
| D74 | Validate actual sources after pre-hooks (skipped on failed pre-hooks per L5) |
| D75 | Hook command, args, cwd, env, secret references |
| D76 | Bounded stdout/stderr, best-effort redaction, truncation logged |
| D77 | Hook OS user/group defaults and optional overrides |
| D78 | Process-group timeout termination and grace |
| D79 | Independent bounded cleanup timeout |
| D80 | Independent bounded storage-release timeout |
| D81 | Release timeout requires revalidation; no automatic release retry |
| D82 | Block conflicts while lifecycle operation termination unconfirmed |
| D83 | Cross-process storage coordination, no mandatory daemon |
| D84 | Recover stale owners only after confirmed termination |
| D85 | No automatic release on stale-claim recovery; reconcile at next acquisition |
| D86 | Canonical storage resource IDs plus provider-aware identity validation |
| D87 | Reject verified physical aliases under different IDs |
| D88 | Require explicit coordination identity when physical identity unverifiable |
| D89 | Changed physical identity requires admin approval |
| D90 | Storage and repository identity approvals independent |
| D91 | Reuse already-mounted storage after validation |
| D92 | Preserve externally mounted storage; release only owned lifecycle |
| D93 | Persist acquisition ownership in recoverable state |
| D94 | Physical state verification overrides persisted ownership assumptions |
| D95 | Disconnect mid-operation: fail, no automatic reacquire |
| D96 | Concurrent acquisition joins one attempt |
| D97 | Independent acquisition timeout; block until termination confirmed |
| D98 | Job timeout withdraws its own shared acquisition participation |
| D99 | Last withdrawal cancels unused acquisition; reconcile race |
| D100 | Acquisition during release waits for release, then fresh validation |
| D101 | Join active acquisition or reuse acquired resource; release after last user |
| D102 | Unified storage lifecycle for backup, maintenance, and admin |
| D103 | Separate cross-process repository coordination; compatibility-aware |
| D104 | Operation-specific repository compatibility matrix |
| D105 | Wait for compatible repository access with timeout/cancellation |

## 5. Replication and convergence decision register

| ID | Confirmed decision / current status |
|---|---|
| R1 | Additive replication; do not delete destination-only snapshots |
| R2 | Preserve divergent histories, including fallback-created snapshots |
| R3 | Replication and convergence before retention |
| R4 | Copy success plus expected snapshot representation confirmation |
| R5 | Preserve Restic-managed partial progress; no automatic retry/rollback |
| R6 | Explicit expected source snapshot set per transfer |
| R7 | Automatic union-history convergence across available repositories |
| R8 | Allow differing chunker parameters with warning |
| R9 | Fail affected replication phase if complete enumeration fails |
| R10 | Fail affected transfer if expected snapshot disappears; preserve completed work |
| G1 | Separate fixed-source normal replication and later history convergence |
| G2 | Originally retention-aware pre-transfer filtering; **superseded** by shared job-level retention and full convergence before retention |
| G3 | Restic-authoritative snapshot equivalence, optional revalidated index |
| G4 | Independent required transfer outcomes; no rollback |
| G5 | Deterministic global multi-repository claim ordering |
| G6 | **Resolved:** no retention-eligibility filtering before copy; converge then apply common job policy |
| G7 | Required convergence failures/unavailable destinations yield partial job outcome after successful backup |

## 6. Lifecycle decision register

| ID | Confirmed decision |
|---|---|
| L1 | Formal hook-phase semantics, independent hook failures, cleanup after preparation |
| L2 | Separate job completion and persistent shared-resource lifecycle completion/notification |
| L3 | Durable claims/ownership/operation identity, physically reconciled before reuse/release |
| L4 | Admitted jobs hold global slots while waiting for resources |
| L5 | Skip source validation if any pre-backup hook failed |
| L6 | Stable coordination identity required when physical identity unverifiable |

## 7. Supersession and clarification register

1. **G2 → G6 and shared retention clarification:** Earlier G2=B proposed avoiding convergence copies of snapshots that retention would discard. The user later explicitly confirmed a *single job-level retention configuration* and **full normal replication plus full additive convergence before retention**. Pre-copy filtering is therefore removed. G2's previous filtering requirement is superseded, not silently retained.
2. **D22 clarified:** Each destination executes retention independently, but **all destinations of a job share the same policy**. Independent execution does not mean destination-specific configuration.
3. **D33–D34 extended by G7:** Required convergence failures and required unavailable destinations count toward partial success after a successful backup; the backup outcome itself remains independently successful.
4. **D58 refined by L2:** Job notifications follow the job's own completion/claim relinquishment; later shared-resource physical release has separate persistent outcome and notification. Do not hold an otherwise completed job open for unrelated users of the same resource.
5. **D74 refined by L5:** Source validation is after successful preparation only; it is skipped if a pre-hook fails.
6. **D11/D38 scoped by G1:** The fixed-source/no-failover rule applies to **normal replication**. History convergence may read from other repositories without being a mid-run normal-replication failover.

## 8. Outstanding policy and technical design register

### Open product policy (not confirmed)

- **D106:** Fairness/admission of repository waiters (FIFO with compatibility, maximum throughput, priorities, or other). Explicitly paused.
- Clarify any genuinely ambiguous semantics uncovered by architecture/tests through explicit new decisions, rather than inferred policy.

### Architecture/technical validation (not product-policy reopening)

- Restic version support and verified operation-specific repository lock compatibility matrix.
- Restic copy semantics, snapshot identity/equivalence and expected-snapshot confirmation across repositories, including source disappearance.
- Restic retention grouping/metadata equivalence across copies; identical policy does not guarantee identical histories after unavailable/failed operations.
- Cross-process storage and repository claim persistence, atomicity, deadlock avoidance, ownership transfer, termination confirmation and recovery.
- Storage acquisition versus repository claim acquisition ordering, cancellation, timeout budgets, starvation avoidance and crash recovery.
- Shared-resource lifecycle record schema, release failure attribution, CLI/history representation, and notification context.
- Exact operation and job outcome schema, cancellation classification, and structured error codes.
- Module/provider contracts (including future non-Restic engines such as Borg/rclone), configuration schema and migration/versioning strategy: **not yet selected**.
- CLI commands and flags, admin identity-approval workflow, source/hook env contract, and secret provider contract.
- Backup, replica, convergence, retention, hook, resource and admin operation acceptance/failure-injection test suite.

## 9. Acceptance criteria (product-level)

1. With primary unavailable and first secondary usable, backup succeeds to secondary; when primary returns, its fallback-originated snapshots converge to all available repositories.
2. Destination-only snapshots survive normal replication and convergence; retention may remove them afterward according to the common job policy.
3. A failed transfer does not undo successful transfers; a subsequent independent run can catch up; partial job status is emitted when required convergence is incomplete.
4. Missing required source or pre-hook failure prevents Restic backup; cleanup runs when preparation began.
5. Concurrent jobs sharing storage acquire once, coordinate safely across processes, and release only after the final claim; each job can complete independently of another job's use of the same resource.
6. A stale persisted ownership record never by itself authorizes releasing a mount; physical identity/state and operation termination are revalidated.
7. Unknown config fields, repository ID mismatch, changed verified storage identity, or unauthorized repository init fail safely with actionable reporting.
8. Retention executes after replication/convergence with the same job policy at each available destination and independently reported outcomes.
9. Individual job admission and batch admission respect concurrency limits; repository conflicts wait with applicable cancellation/timeouts; same-job overlaps reject.
10. CLI status and history distinguish full/partial/failed backup job outcomes from independent hook, retention, notification and shared-resource lifecycle failures.

## 10. Recommended next phase

Design the **architecture**, beginning with bounded contexts and state machines: Job Execution, Storage Lifecycle Coordinator, Repository Coordinator, Backup Engine Adapter, Replication/Convergence Planner, Hooks, Retention/Verification, Persistence/Reporting. These are **proposed discussion boundaries**, not approved modules or interfaces. Before implementation, validate that a generic engine abstraction can support Restic-specific snapshot/copy/retention semantics without falsely claiming that fundamentally different tools (e.g. rclone) share the same contracts.

**No implementation authorization is implied by this specification.**
