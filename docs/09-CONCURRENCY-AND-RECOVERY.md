# Cross-process coordination and recovery

SQLite BEGIN IMMEDIATE atomically grants all repository claims or none; no transactions during IO/wait. Storage acquisition precedes repository claims; no acquiring new storage while holding repo claims. Canonical identity, owner generation and operation compatibility required. Process owner identified by boot ID and OS process start identity, not PID alone. Acquire/release worker supervised; process launch intent persisted before spawn and completion evidence after; uncertainty blocks conflicts.

Resource states shared across independent CLI processes without daemon. Concurrent storage acquisition joins a single attempt; timeout of one waiter withdraws only that participant. Last withdrawal cancels unused acquire; new waiter during release waits. Release only when last claim relinquished and manager ownership verified. Lifecycle release results persist and notify separately from job result.

Crash recovery reconciles SQLite journal, OS process/supervisor state, actual mounts and Restic repository identity. Interrupted work not auto retried. Stale claim removal only after definitive termination. Restic native locks not auto removed. If DB missing/corrupt/rollback, fail closed and require explicit state recovery. `doctor` is read-only diagnostic.

## Fairness and deadlines

D106 strict repository fairness remains paused. Implement cancellation-aware bounded wait with progress checks and starvation diagnostics; no strict FIFO guarantee. Global admitted-job limit counts waiting. Effective operation deadline min(job, per-operation) except separately bounded cleanup and release safety budgets. Persist timeout/termination uncertainties; do not assume resource released on timeout.

The I3 supervisor checkpoint uses a dedicated transient user systemd scope. It journals intent first and checks worker identity and cgroup membership before sending the command. It records launch and exit with separate durable transitions. An absent or invalid reply fences the owner; confirmed empty containment and command exit are required for a normal terminal record. On systems without usable scope containment this path fails closed. Storage claim release and recovery are separate coordinator steps.
