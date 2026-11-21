# Open validation gates and delegated implementation details

No additional user product decisions required to author the baseline. Do NOT confuse this with production readiness.

VALIDATION_REQUIRED V-001: certify first exact Restic version, copy lineage/original/rewrites/transitive copies; V-002 native lock compatibility per operation/options; V-003 crash-safe launch handshake and child containment; V-004 physical storage ownership recovery on supported Linux configurations; V-005 Restic repository format and chunker-parameter compatibility; V-006 JSON output schema per certified version. Failed or inconclusive critical validation blocks release; report evidence.

DELEGATED: exact Python minimum supported release (pin in CI), CLI parser library, SQLite migration framework, IPC protocol, indexes, packaging tooling, bounded wait mechanism, notification event envelope, provider-specific nested config schemas. All delegated choices require tests and documentation and may not change confirmed behavior.

PAUSED: D106 strict repository wait fairness. Use starvation diagnostics and bounded waits, no FIFO guarantee.

SPECIFICATION AUTHORING FOLLOW-UP: Some files are architecture baselines rather than complete field-level normative contracts. Codex must fill in command grammar, complete schemas, state transitions, and acceptance fixtures *before* claiming milestone completion; ambiguous product behavior is a blocker, not license to invent. Review traceability across D1–D105 and AT scenarios. Do not claim certification without executed test reports.
