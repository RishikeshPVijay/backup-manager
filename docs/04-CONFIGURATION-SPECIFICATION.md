# Configuration specification v1

## Files

Global `config.toml`, independent `jobs/*.toml`, secrets referenced by provider; no plaintext password fields. `schema_version = 1` mandatory. Unknown keys fail validation. Config snapshots frozen at batch admission. No implicit interpolation. Canonical path handling must avoid silently resolving an unavailable mount to a different storage device.

## Global fields

`state.path` (absolute SQLite path); `restic.executable` (default `restic`); `execution.max_jobs` positive integer; `execution.job_timeout_seconds` optional positive integer; `execution.operation_timeout_seconds`, `execution.cleanup_timeout_seconds`, `execution.release_timeout_seconds`, `execution.acquire_timeout_seconds` positive bounded integers; `logging.level`, `logging.history_days`; `storage.<id>` with `provider`, `lifecycle` (`always_on`/`on_demand`), `config`, optional explicit stable `coordination_id` for unverifiable hardware. `notifications` optional executable hook definitions. Secrets for shared storage may live in global storage provider config by reference.

## Job fields

`job.id`, `job.enabled`; `[[sources]]` with absolute `path`, `required` default true; `[destinations.primary]` and `[[destinations.secondary]]` with `id` for secondaries, `storage`, `repository`, `[...password]` provider-specific reference; `[retention]` Restic-compatible keep selectors applied uniformly; `[hooks]` arrays for `pre`, `post`, `cleanup`; optional `excludes`, `tags`, `timeouts`. Each destination repository is dedicated to that job. Secondary declaration order is fallback order. No per-destination retention override.

## Validation

Require at least one source and one destination; detect duplicate identifiers and confirmed duplicate physical resources; unknown provider/field, invalid retention selector, unsupported schema, missing required secret reference, or invalid timeout fails the affected job before side effects. At execution, revalidate actual storage and registered Restic repository ID. Required source existence checked after successful pre-hooks. `config validate` must not run hooks or acquire storage.

## Open implementation precision

Formal JSON schemas in `schemas/` describe the stable baseline. Provider-specific nested config schemas are composed at runtime. Any field not defined in the baseline must be rejected until versioned documentation/schema/test coverage is added; do not invent configuration features.

## Confirmed v1 configuration clarifications

A job may specify `timeouts.job_timeout_seconds` and `timeouts.operation_timeout_seconds` as positive integer seconds. Overrides may equal or shorten their global counterparts. Omitting an override inherits the effective global budget. Acquisition, cleanup, and release limits use their independent global settings.

`logging.history_days` and `logging.history_count` are optional positive integer limits applied to each job's history. A run becomes eligible for deletion when it exceeds either configured limit. When neither limit is configured, neither triggers deletion.

`retention.prune` is a boolean and defaults to false. A policy must contain at least one keep selector. When enabled, prune is a separate operation under the shared job policy and follows forget.

State configuration requires both `state.path` and `state.anchor_path`. Provision their parent directories before initialization, under the service account on supported local filesystems and protected from other writers. Keep the witness in a separate directory tree and outside every database restore or snapshot rollback scope. If either component is lost or generations disagree, stop scheduled admissions and preserve both components for recovery.

## I5 job and hook fields

A backup invocation uses the configured execution limits. When omitted, the effective limits are one job, 14,400 seconds per job, 7,200 seconds per operation, 120 seconds for acquisition, 300 seconds per cleanup hook, and 120 seconds for release. Job overrides and the CLI timeout can shorten the applicable ordinary budgets. Cleanup and release retain their independent budgets.

Each hook entry accepts `executable`, `args`, an absolute `cwd`, and `timeout_seconds`. The optional `env` table maps names to ordinary environment strings. The optional `secrets` table maps names to file or environment secret references. A name may appear in only one table. The `BACKUP_MANAGER_` prefix is reserved for the run ID, job ID, phase, selected destination ID, and backup outcome supplied at execution.

A hook may set `user` or `group` by OS name or numeric ID. It inherits the invoking identity by default. Changing identity requires the corresponding OS privilege. The worker clears inherited supplementary groups when switching identity. Hook results include redacted stdout and stderr, exit status, stop reason, and whether capture was truncated.

Restic repository file secrets use Restic's observed whitespace framing for password files. Environment secrets are passed unchanged. A file or environment secret used by a hook keeps its original bytes.
