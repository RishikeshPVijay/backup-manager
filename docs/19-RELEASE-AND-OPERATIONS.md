# Release, installation, recovery and operational safety

Initial platform: Linux x86_64/aarch64, Python 3.12–3.13, system Restic exact certified release(s), SQLite local state. Package with `pyproject.toml` PEP 621 console entry point; dependency lockfile for CI and reproducible builds. `pipx`/virtualenv supported; systemd timers or cron are external schedulers. Run under dedicated least-privileged service account where mounts and hooks permit. Document Linux capabilities/privileges needed per storage provider. No public API or network listener.

Install: install package, install certified Restic, create state/secrets directories with restrictive permissions, write global/job config, validate, run explicit `state init`, explicitly register/init each repo, run `doctor`, then `job run --dry-run` and `--preflight`. Never initialize repo implicitly.

Upgrade: stop scheduled admissions, drain active operations, make consistent SQLite backup and manifest, verify config/schema compatibility, run transactional migrations, verify state, restart scheduling. If an active operation is uncertain, do not migrate destructively; require recovery. Downgrade is unsupported unless a documented compatible migration exists.

Disaster recovery: restore state backup only with explicit `state recover`, compare instance ID/generation, reconcile storage/repository identity and process state, verify all repository IDs, regenerate inventories, preserve uncertain historical outcomes, require operator approval for older generation. No implicit stale-lock removal. If state is unrecoverable, provide a documented manual registration workflow without claiming historical evidence can be reconstructed.

Operator runbooks: unavailable primary, wrong repo ID, wrong password, failed acquisition, orphaned mount, unconfirmed process termination, native Restic lock, interrupted copy, missing state DB, migration failure, version certification failure, corrupted snapshot inventory. Each runbook must state safe observations, forbidden automatic actions, and explicit operator remediation.

Release evidence: build hash, dependency lock hash, exact Restic version+binary fingerprint, supported repo formats, certification report, test matrix, migration tests, crash injection results, security scan results, platform details, outstanding limitations. No 'production ready' label until all critical evidence exists.

## Retention and operator commands

A successful backup runs the configured common retention policy after normal
replication and convergence. Use `retention preview JOB_ID --destination ID`
to evaluate native retention rules against an already mounted, registered
repository. The preview leaves state unchanged. `retention run JOB_ID` applies
the policy independently across configured destinations. Set `retention.prune`
to opt in to a separate prune after successful forget. Use `verify run JOB_ID`
to run Restic checks on a separate schedule. Both commands accept
`--destination ID`. Use `repository unlock JOB_ID DEST_ID --yes` to explicitly
run Restic stale-lock removal after verifying the registered repository ID.

Use `job run JOB_ID --dry-run` for configuration-only planning or
`job run JOB_ID --preflight` for acquisition and registered identity checks.
`doctor` checks the state ledger and exact Restic binary profile.
`doctor --preflight` additionally checks configured jobs with storage lifecycle
operations.

Configure external completion commands using `[[notifications.job]]` and
`[[notifications.storage]]` in the global config. They use the hook command
schema. Each command receives structured context in
`BACKUP_MANAGER_EVENT_JSON` and labels in `BACKUP_MANAGER_EVENT_KIND` and
`BACKUP_MANAGER_ENTITY_ID`. Notification attempts have independent outcomes.
Storage notifications are associated with the corresponding physical release.

Recovery requires the backup file and its sibling `.manifest.json`. The
implementation checks the independent witness, validates the copy, and requires
reconciliation of all registered repository identities and visible inventory.
Storage release ownership is reset conservatively. An older backup requires
proof from the surviving live ledger that newer claims and operations are
settled. If that ledger is unavailable, preserve the original instance files
and use a separately initialized instance for manual registration after
independently proving all old processes are terminated and checking the mounts.
Register existing repositories explicitly and retain the old state and backup
files for investigation. A newly registered instance starts with new history.

## Install and upgrade checklist

1. Install the wheel and its locked runtime dependencies in a dedicated virtual
   environment. For a source checkout use `python -m pip install -r
   requirements-dev.lock` followed by `python -m pip install --no-deps
   dist/*.whl`. The wheel entry point also works in a pipx installation.
2. Verify the Restic executable SHA-256 against the packaged profile. Set the
   executable to its absolute system path. The current certification covers
   the registered Linux x86-64 and ARM64 fingerprints. A different build
   requires its own exact certification before use.
3. Run under a dedicated account with a functioning user systemd manager and
   delegated cgroup v2 access for scopes. Confirm `systemctl --user show-environment`
   in the same account and scheduling environment. Use a user timer or cron
   with access to that manager. Give the account read access to backup sources
   and write access to the intended repositories. The built-in on-demand
   provider requires authorization for its mount and unmount operations.
   Pre-mounted storage can use the provider's external lifecycle without mount
   authority. Changing a hook's user or group requires suitable OS privileges.
4. Create separate protected directory trees for the local database and its
   generation witness. Use owner-only permissions for credentials and state
   files. Copy the example config and job files, replacing all illustrative
   paths and references. Run `config validate --all`, then `state init --yes`.
   Register existing repositories explicitly, run `doctor`, then test each
   job with `--dry-run` and `--preflight`.
5. To upgrade, stop scheduled admissions and drain active operations. Preserve
   an independent copy of the database and witness for investigation, and
   install the new package in a separate environment. From its source archive
   run `python tools/upgrade_state.py --config /path/to/config.toml --backup
   /private/local/backup.sqlite3` as the service account. The helper validates
   the existing schema and refuses unsettled coordination. It writes a backup
   plus manifest before the migration transaction. Check state and run
   `doctor` using the new package before restarting scheduling.
6. If an upgrade fails, keep admissions stopped and preserve the live
   database, witness, sidecars, and backup. Use the recovery runbook.
   Downgrading across a changed schema is unsupported. Reinstalling an older
   wheel by itself cannot roll back state.

## Incident runbooks

| Symptom | Safe observations | Actions to avoid | Explicit remediation |
|---|---|---|---|
| Primary unavailable or acquisition failed | Inspect the job phases, provider health, device identity and mount topology. | Automatic remount after a disconnect, retries of started operations, initialization. | Restore the expected device or connection. Use preflight to verify availability before a fresh run. |
| Repository ID mismatch | Compare the registered and independently observed IDs. Verify the storage identity separately. | Approval based on path or password alone. | Restore the expected repository or use `repository approve-replacement` with the verified full ID. |
| Wrong or missing password | Check the secret reference, service account environment, or file ownership and mode. | Printing the credential or changing repository identity. | Correct the reference or delivery of the secret and run a new preflight. |
| Orphaned or external mount | Inspect resource ownership records, current mount ID, nesting and active processes. | Automatic unmount or moving mount trees. | After verifying process termination and physical ownership, reconcile with `storage recover`. Handle an external mount through its owner. |
| Unconfirmed process termination | Inspect the operation, journal and its recorded scope, boot ID and PID start identity. | Clearing claims by PID alone or assuming an absent receipt proves no launch. | Use the recorded supervisor identity for controlled termination when authorized, then confirm scope emptiness before reconciliation. |
| Native Restic lock | Check the registered repository and look for active Restic processes or manager claims. | Automatic unlock or bypassing native locks. | Wait for active work. For stale locks, use the explicit `repository unlock` command after verifying the repository. |
| Interrupted copy | Read journal evidence, the attempt outcome and available repository inventories. | Resuming the old run or rolling back successful copies. | Reconcile process and storage state. A new run computes a fresh plan from complete inventories. |
| Missing, corrupt, or rolled-back database | Inspect the independent witness, SQLite sidecars and backup manifest. | Initializing over missing state, deleting sidecars, or restoring the database and witness as a pair. | Use `state recover` with the verified backup. If current process safety cannot be proved, follow the manual registration path above once old processes are independently accounted for. |
| Migration failure | Preserve the old-schema backup and inspect migration checksums, current schema and witness. | Retrying by editing the witness or bypassing a schema mismatch. | Diagnose the mismatch offline. Use the verified recovery path when the service can prove the required state and process invariants. |
| Unsupported or failed Restic certification | Compare the executable path, digest, version and profile report. | Accepting an approximate version or widening the lock compatibility policy. | Install the certified artifact or certify the candidate with disposable repositories before selecting a new profile. |
| Corrupt or incomplete snapshot inventory | Keep phase and command diagnostics and run a separate `verify run` when safe. | Treating missing evidence as deletion or automatically repairing the repository. | Investigate with the repository owner. Retry as a fresh operation only after identity, integrity and process safety have been established. |

## Development and release verification

`tools/build_release.py` normalizes archive metadata for reproducible sdist
and wheel builds from identical inputs and a fixed epoch. The source archive
includes the reference contracts, runbooks, fixture code and certification
reports. Record both artifact hashes and the dependency lock hash.

The hosted CI matrix builds and tests Python 3.12 and 3.13 and audits the pinned
dependencies. The separate Linux safety workflow runs from the default branch on
an isolated runner labeled `backup-manager-disposable`. Provision the exact
Restic binary, a usable user systemd bus, local supported temporary storage,
and private user mount namespaces first. Run the real loop fixture separately
in a dedicated private container or VM with only disposable loop-device access.
The fixture must never receive production devices or mountpoints. Use
`tools/run_linux_regressions.py` to collect individual logs from either suite.
The acceptance matrix and release evidence manifest remain separate I9 gates.

Output redaction matches known credential bytes in complete captured streams.
Captured output with known credentials is omitted once native capture
truncates. Encoded or transformed forms can evade exact matching. Configure
trusted hooks to avoid emitting credentials under any format.

## Approving replacement storage

Stop scheduling uses of the resource and drain its claims and lifecycle work.
For the Linux filesystem provider, update the configured filesystem UUID for
the intended device and mount it at the configured mountpoint. Verify the
device independently. Read the exact provider identity from
`storage inspect STORAGE_ID --json` and run
`storage approve-replacement STORAGE_ID --expected-id PHYSICAL_ID --yes`.
The Linux provider reports the identity in the form `filesystem:UUID`.

Approval refuses unverified identities, an identity already bound to another
resource, unsettled claims or lifecycle operations, and outstanding manager
mount ownership. Clear those fences through explicit recovery of the original
binding before approving a replacement. The command changes the storage
generation and writes an audit event. A replacement that also contains a
different Restic repository requires the separate repository approval command.
