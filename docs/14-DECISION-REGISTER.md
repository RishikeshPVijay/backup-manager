# Decision register and precedence

Confirmed product policy: D1–D105, R1–R10, G1–G7 (G2 retention-aware prefilter superseded), L1–L6 in `01-PRD.md`. D106 repository fairness remains PAUSED. Later explicit user clarification: common job-level retention configuration, complete normal replication and convergence before retention, no pre-copy retention filtering.

Confirmed architecture:
A1 Python entry-point storage/secret plugins, job-local destination secret refs.
A2 capability-based storage: inspect/resolve_identity mandatory, acquire/release optional.
A3 SQLite transactions plus OS process locks; no daemon.
A4 core-supervised subprocess executor.
A5 isolated supervised worker for side-effecting storage lifecycle.
A6 tested exact Restic release compatibility profiles.
A7 system-installed configurable Restic binary.
A8 persistent, revalidated snapshot provenance index, Restic authoritative.
A9 serialize Restic copy per destination repository across processes.
A10 incremental replanning of pending transfers, frozen started attempt source sets.
A11 no same-run replacement after started transfer failure.
A12 successful supervised Restic process launch defines started boundary; uncertain launch fail-closed until reconciled.

Assistant-selected handoff defaults (user authorized choosing recommended options): versioned provider API, strict versioned TOML, local SQLite WAL, fail-closed missing/corrupt/rolled-back DB, explicit state recovery, exact Restic certification, conservative native lock matrix pending tests, CLI exit codes, Linux-first Python packaging, explicit bounded deadlines, no strict FIFO fairness, acceptance traceability, Codex execution protocol. These are design defaults, NOT empirically validated Restic/OS facts. If an assistant-selected default contradicts confirmed product policy, confirmed policy wins.
