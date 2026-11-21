# Codex implementation roadmap

Use dependency-ordered milestones, each with tests, docs, and checkpoint commit. Do not claim complete without acceptance evidence.

I1 foundation: packaging, CLI skeleton, strict config schemas, typed errors, CI, fixture harness.
I2 plugin interfaces, file/env secrets, built-in filesystem storage, supervised lifecycle workers.
I3 SQLite migrations, resource/repository claim sets, owner fencing, process supervisor and crash recovery.
I4 Restic adapter and version certification runner; certify first exact supported release before dependent convergence implementation.
I5 job orchestration, hooks, source checks, snapshot confirmation, job cancellation and history.
I6 provenance, equivalence, normal replication, convergence planner, A9–A12 scheduling and outcomes.
I7 retention, verification, admin commands, notifications, dry-run, state recovery and doctor.
I8 hardening, failure injection, packaging, docs, CI, security audit.
I9 final acceptance: full traceability matrix and end-to-end evidence.

## Codex rules

At each milestone: implement → test → document decisions → run regression → checkpoint. If Restic experiment disproves a proposed algorithm, do not silently redefine requirements: implement safe failure and log a blocker with evidence. Preserve D106 paused status. No broad scope creep (UI, remote agent, generic engines). `AGENTS.md` is entry point.
