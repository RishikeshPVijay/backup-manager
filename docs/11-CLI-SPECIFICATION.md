# CLI specification v1

Executable `backup-manager`. Commands: `config validate`, `job list|show|run`, `batch run`, `storage list|inspect|recover`, `repository init|register|inspect|unlock|approve-replacement`, `retention run|preview`, `verify run`, `history list|show`, `operation list|inspect|cancel`, `state check|backup|recover`, `doctor`, `version`. Explicit target IDs for destructive actions. `--json` yields stable schema-versioned JSON object with `schema_version`, `command`, `status`, `operation_id`, `data`, `errors[]`; errors have stable code/message/context with secrets redacted. Human output default. No prompts for ordinary scheduled work. `--yes` on explicitly authorized admin commands only, never bypassing safety invariants. `--dry-run` side-effect-free; `--preflight` separate operational check with lifecycle effects. No silent repair by `doctor`.

## Exit code baseline

0 full success; 2 partial success; 3 job/operation failed; 4 configuration or invocation invalid; 5 resource conflict/busy; 6 cancelled; 7 unsupported Restic/plugin compatibility; 8 unsafe state/recovery required; 9 internal error. Batch aggregate failure > partial > full, with invalid per-job config reported as failed job and valid jobs continuing. Exact command-specific argument grammar and JSON examples must be derived and versioned before release; tests enforce codes and schema.

## Representative syntax

`backup-manager job run immich --json`; `backup-manager batch run --json`; `backup-manager repository register immich primary`; `backup-manager state check`; `backup-manager doctor --json`. Administrative unlock requires explicit repository ID, confirmed safe state, and audit record. Never automatically invoke unlock.

## I5 job operations

Initialize the application state explicitly. Create the Restic repository through an explicit administrative action and use `repository register JOB_ID DEST_ID --yes` to bind that existing repository. Registration checks the Restic ID at the configured location, refuses a duplicate or replacement, and cannot initialize a repository. A legacy row without path binding can be explicitly registered again only after proving its previously approved ID.

`job run JOB_ID` admits a job immediately or returns busy. `batch run JOB_ID ...` freezes all requested job files before admission, waits for global job slots in invocation order, and reports per-job results. Same-job overlaps return busy in either mode. `operation list --active-only` reports active job run IDs. `operation cancel RUN_ID --yes` requests cancellation of an active job. The invocation stops new ordinary work and attempts bounded cleanup after preparation. `history list` and `history show RUN_ID` expose the independently recorded phase outcomes.

Backup runs through the certified Restic profile. Retention, dry-run, and operational preflight remain deferred until I7.

## I6 multi-destination outcomes

Multi-destination `job run` now performs normal replication and history convergence after cleanup. It reports full success when every required destination is available and both transfer phases pass, or partial when backup is confirmed and a required destination or transfer is incomplete. Transfer phase entries and their attempt IDs appear in history. Retention remains deferred to I7.
