# Cross-process coordination and recovery

SQLite BEGIN IMMEDIATE atomically grants all repository claims or none; no transactions during IO/wait. Storage acquisition precedes repository claims; no acquiring new storage while holding repo claims. Canonical identity, owner generation and operation compatibility required. Process owner identified by boot ID and OS process start identity, not PID alone. Acquire/release worker supervised; process launch intent persisted before spawn and completion evidence after; uncertainty blocks conflicts.

Resource states shared across independent CLI processes without daemon. Concurrent storage acquisition joins a single attempt; timeout of one waiter withdraws only that participant. Last withdrawal cancels unused acquire; new waiter during release waits. Release only when last claim relinquished and manager ownership verified. Lifecycle release results persist and notify separately from job result.

Crash recovery reconciles SQLite journal, OS process/supervisor state, actual mounts and Restic repository identity. Interrupted work not auto retried. Stale claim removal only after definitive termination. Restic native locks not auto removed. If DB missing/corrupt/rollback, fail closed and require explicit state recovery. `doctor` is read-only diagnostic.

## Fairness and deadlines

D106 strict repository fairness remains paused. Implement cancellation-aware bounded wait with progress checks and starvation diagnostics; no strict FIFO guarantee. Global admitted-job limit counts waiting. Effective operation deadline min(job, per-operation) except separately bounded cleanup and release safety budgets. Persist timeout/termination uncertainties; do not assume resource released on timeout.

The I3 supervisor checkpoint uses a dedicated transient user systemd scope. It journals intent first and checks worker identity and cgroup membership before sending the command. It records launch and exit with separate durable transitions. An absent or invalid reply fences the owner; confirmed empty containment and command exit are required for a normal terminal record. On systems without usable scope containment this path fails closed. Storage claim release and recovery are separate coordinator steps.

Shared acquisition uses a persisted reservation that an independent dispatch process adopts before launching the provider. Each waiting invocation holds a separate claim. The final withdrawal requests cancellation. The dispatcher confirms scope termination and checks physical state. A successful acquisition that races cancellation reserves release in the same transaction, then dispatches it with a fresh release deadline. Other users cannot join a release already in progress.

An interrupted launch can be reconciled using a persisted, still-closed command gate, or a proved-empty scope in the recorded boot and cgroup namespace. The reconciler preserves interrupted execution history and keeps conflicts fenced while evidence is inconclusive. The confirmed `storage recover` command re-inspects an idle resource before clearing reconciled storage operations. It preserves orphaned mounts and removes automatic release authority from their old ownership records. Repository compatibility remains exclusive until the Restic profile supplies certified rules.

I5 adds job-level admission to the same SQLite state. A job holds its slot through resource waits and cleanup. After admission, the runner reserves the selected repository for the backup sequence. Each repository command is linked to that claim. The scope worker verifies and opens the repository directory immediately before launching Restic, and Restic uses the inherited directory descriptor. The runner also rechecks the physical storage observation and repository ID before and after backup.

When a launched command times out and its recorded scope is proved empty, its termination is recorded with a timeout reason. If proof is missing, claims remain fenced. A stale run with unresolved execution keeps its admission fence. Verified scope recovery marks it interrupted and permits a later fresh run. Crash recovery does not run the old invocation's hooks.
