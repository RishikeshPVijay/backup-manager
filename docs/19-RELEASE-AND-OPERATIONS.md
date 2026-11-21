# Release, installation, recovery and operational safety

Initial platform: Linux x86_64/aarch64, Python 3.12–3.13, system Restic exact certified release(s), SQLite local state. Package with `pyproject.toml` PEP 621 console entry point; dependency lockfile for CI and reproducible builds. `pipx`/virtualenv supported; systemd timers or cron are external schedulers. Run under dedicated least-privileged service account where mounts and hooks permit. Document Linux capabilities/privileges needed per storage provider. No public API or network listener.

Install: install package, install certified Restic, create state/secrets directories with restrictive permissions, write global/job config, validate, run explicit `state init`, explicitly register/init each repo, run `doctor`, then `job run --dry-run` and `--preflight`. Never initialize repo implicitly.

Upgrade: stop scheduled admissions, drain active operations, make consistent SQLite backup and manifest, verify config/schema compatibility, run transactional migrations, verify state, restart scheduling. If an active operation is uncertain, do not migrate destructively; require recovery. Downgrade is unsupported unless a documented compatible migration exists.

Disaster recovery: restore state backup only with explicit `state recover`, compare instance ID/generation, reconcile storage/repository identity and process state, verify all repository IDs, regenerate inventories, preserve uncertain historical outcomes, require operator approval for older generation. No implicit stale-lock removal. If state is unrecoverable, provide a documented manual registration workflow without claiming historical evidence can be reconstructed.

Operator runbooks: unavailable primary, wrong repo ID, wrong password, failed acquisition, orphaned mount, unconfirmed process termination, native Restic lock, interrupted copy, missing state DB, migration failure, version certification failure, corrupted snapshot inventory. Each runbook must state safe observations, forbidden automatic actions, and explicit operator remediation.

Release evidence: build hash, dependency lock hash, exact Restic version+binary fingerprint, supported repo formats, certification report, test matrix, migration tests, crash injection results, security scan results, platform details, outstanding limitations. No 'production ready' label until all critical evidence exists.
