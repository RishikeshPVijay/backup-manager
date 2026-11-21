# Plugin contracts

## Discovery and trust

Discover installed `backup_manager.storage` and `backup_manager.secrets` Python entry points. Provider IDs stable lowercase tokens; reject duplicate IDs, incompatible API major version and unknown configuration fields. No runtime package installation. Plugins are trusted code; process isolation is fault containment, not a security boundary.

## Storage provider v1

Required: `inspect(config, context) -> Inspection` (current accessibility, mountpoint, observed identity, ownership hints); `resolve_identity(config, context) -> Identity` (canonical coordination ID, verified physical ID or explicit stable ID, confidence). Optional capabilities: `acquire(config, context) -> AcquisitionResult`, `release(config, context) -> ReleaseResult`. Core owns claims, owner generation, deadlines, policy, persistence, identity approval and release eligibility. Inspect/resolve must be read-only; side-effecting acquire/release run in supervised worker. Providers must use injected `CommandExecutor` for child commands, and must not manipulate coordination DB or spawn unmanaged children.

## Secret provider v1

`resolve(reference, context) -> SecretMaterial` with redaction-safe error types; initial providers `file` and `environment`. Secret material must not be logged, serialized to DB, or placed in argv. Prefer Restic password-file/command mechanisms with secure temporary FD or restricted file; validate version-specific support. Secret providers are not allowed to log secret values. File secrets reject symlinks and unsafe permissions by default, with documented override only if security review permits.

## Provider manifest

`provider_id`, `api_version`, `kind`, `capabilities`, `config_schema`. Validate capability declarations against implemented methods. Core checks worker operation termination and physical state independently; provider success is not proof of resource ownership.

## Extension example

See `examples/custom-storage-plugin/` for a minimal reference provider. It is intentionally non-mutating and not suitable for mounting devices.
