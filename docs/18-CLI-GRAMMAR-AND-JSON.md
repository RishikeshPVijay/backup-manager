# CLI grammar, output contract, exit codes

Every command accepts `--config PATH` (default `/etc/backup-manager/config.toml`), `--json`, `--quiet` (human mode), `--timeout-seconds N` where applicable. Global flags may appear before the subcommand. All commands accept `--help`. IDs are required positional arguments unless specified. No implicit selection of multiple repositories for destructive commands.

| Command | Required args | Optional flags | Mutates |
|---|---|---|---|
| `config validate` | none | `--job ID`, `--all` | no |
| `job list` | none | `--enabled-only` | no |
| `job show` | `JOB_ID` | none | no |
| `job run` | `JOB_ID` | `--dry-run`, `--preflight` | yes except dry-run |
| `batch run` | one or more `JOB_ID` or `--all` | `--dry-run`, `--preflight` | yes except dry-run |
| `storage list` | none | none | no |
| `storage inspect` | `STORAGE_ID` | `--preflight` | optional lifecycle |
| `storage recover` | `STORAGE_ID` | `--yes` | yes |
| `repository init` | `JOB_ID DEST_ID` | `--yes` | yes |
| `repository register` | `JOB_ID DEST_ID` | `--yes` | yes |
| `repository inspect` | `JOB_ID DEST_ID` | `--preflight` | no or lifecycle |
| `repository unlock` | `JOB_ID DEST_ID` | `--yes` | yes |
| `repository approve-replacement` | `JOB_ID DEST_ID` | `--expected-id ID --yes` | yes |
| `retention run` | `JOB_ID` | `--destination ID` | yes |
| `retention preview` | `JOB_ID` | `--destination ID` | no |
| `verify run` | `JOB_ID` | `--destination ID` | reads repository |
| `history list` | none | `--job ID --limit N` | no |
| `history show` | `RUN_ID` | none | no |
| `operation list` | none | `--active-only` | no |
| `operation inspect` | `OPERATION_ID` | none | no |
| `operation cancel` | `OPERATION_ID` | `--yes` | yes |
| `state init` | none | `--yes` | yes |
| `state check` | none | none | no |
| `state backup` | `OUTPUT_PATH` | none | yes (backup file) |
| `state recover` | `BACKUP_PATH` | `--yes` | yes |
| `doctor` | none | `--preflight` | no by default |
| `version` | none | none | no |

`--yes` bypasses confirmation only; never safety checks. Interactive destructive command confirmation requires typing the fully qualified target ID; non-TTY without `--yes` fails invalid. `--dry-run` cannot be combined with `--preflight`. `--all` and explicit batch IDs are mutually exclusive. `--limit` positive, max 1000. `--timeout-seconds` positive, never overrides configured hard safety budgets upward. JSON result matches `schemas/cli-result.schema.json`, one object to stdout, no progress chatter. Example:

```json
{"schema_version":1,"command":"job run","status":"partial","operation_id":"run-123","data":{"job_id":"immich","backup":"succeeded","replication":"incomplete","retention":"succeeded"},"errors":[{"code":"DESTINATION_UNAVAILABLE","message":"Replica unavailable","context":{"destination_id":"replica"}}]}
```

Stable exit codes: 0 full; 2 partial; 3 failed; 4 invalid; 5 busy; 6 cancelled; 7 unsupported; 8 unsafe; 9 internal. Batch precedence failure > partial > full. Never use exit 0 when a required convergence destination is unavailable. JSON error context must be secret-redacted and may not contain command environment or raw provider exceptions.
