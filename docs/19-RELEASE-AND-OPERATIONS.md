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
