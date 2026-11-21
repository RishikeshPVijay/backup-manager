# CLI specification v1

Executable `backup-manager`. Commands: `config validate`, `job list|show|run`, `batch run`, `storage list|inspect|recover`, `repository init|register|inspect|unlock|approve-replacement`, `retention run|preview`, `verify run`, `history list|show`, `operation list|inspect|cancel`, `state check|backup|recover`, `doctor`, `version`. Explicit target IDs for destructive actions. `--json` yields stable schema-versioned JSON object with `schema_version`, `command`, `status`, `operation_id`, `data`, `errors[]`; errors have stable code/message/context with secrets redacted. Human output default. No prompts for ordinary scheduled work. `--yes` on explicitly authorized admin commands only, never bypassing safety invariants. `--dry-run` side-effect-free; `--preflight` separate operational check with lifecycle effects. No silent repair by `doctor`.

## Exit code baseline

0 full success; 2 partial success; 3 job/operation failed; 4 configuration or invocation invalid; 5 resource conflict/busy; 6 cancelled; 7 unsupported Restic/plugin compatibility; 8 unsafe state/recovery required; 9 internal error. Batch aggregate failure > partial > full, with invalid per-job config reported as failed job and valid jobs continuing. Exact command-specific argument grammar and JSON examples must be derived and versioned before release; tests enforce codes and schema.

## Representative syntax

`backup-manager job run immich --json`; `backup-manager batch run --json`; `backup-manager repository register immich primary`; `backup-manager state check`; `backup-manager doctor --json`. Administrative unlock requires explicit repository ID, confirmed safe state, and audit record. Never automatically invoke unlock.
