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
