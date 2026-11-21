# Execution algorithms and recovery decision rules

## A. Convergence planner
Inputs: immutable run configuration, registered repository generations, complete fresh inventories of available repositories, certified equivalence resolver, terminal attempt outcomes, deadline. If required source enumeration fails, fail affected phase without interpreting missing snapshots as deleted. Construct logical union from complete available inventories. For each available destination, generate requirements for each equivalence class absent there. Unavailable configured destinations contribute incomplete outcome. Choose a source only where validated snapshot representation exists; do not invent equivalence. Group requirements into transfers with frozen source-local snapshot IDs. Order claim requests by canonical repository key. Preserve successful transfer results. After each transfer, refresh affected inventory and replan *only pending* requirements; started failed transfers block their assigned requirements for this run. Stop on no new progress, cancellation, or deadline. Never shrink union on failed inventory refresh.

## B. Copy executor
1. Persist attempt PLANNED and frozen requirements. Acquire verified storage claims; then atomic repository claim set.
2. Revalidate source IDs and destination generation. Prelaunch failures release claims safely and may replan pending requirements.
3. Persist LAUNCH_INTENT. Invoke supervised Restic with exact explicit IDs, source/destination credentials via non-argv mechanism.
4. Record LAUNCHED receipt. If uncertain, mark LAUNCH_UNCERTAIN; no replacement in same run and block conflicting work until termination confirmed.
5. Observe exit. Nonzero/terminated -> FAILED and block assigned requirements. Exit zero -> enumerate destination and verify certified logical representation. Verification failure -> UNVERIFIED (not success), block assigned requirements.
6. On verified success, persist evidence and SUCCEEDED atomically. Release repository claims only after termination proof, then relinquish storage. Independent failures retained.

## C. Crash recovery
A crash does not resume work. On new invocation first validate state integrity and generation, then inspect owner boot/start identity, supervised child containment, mounts, repository identities and claims. If process may still be alive, do not reuse conflicting claims; request controlled termination only when authorized and possible, and require proof. If launch was uncertain, never conclude 'not launched' from missing LAUNCHED record alone. If terminated and repository may contain partial work, preserve it and mark attempt INTERRUPTED/UNVERIFIED; later independent run recomputes inventory. Recover storage ownership only with physical verification; preserve externally owned mounts. Never delete native Restic locks. Unresolved safety uncertainty requires `doctor` and explicit recovery.

## D. Retention
After normal replication and convergence, for each currently available destination apply identical job-level retention independently. Execute forget and, if policy requests, prune as separate recorded operations with certified repository compatibility and exclusive claims. Retention failure does not undo backup/replication successes; attempt remaining destinations. Do not delete destination-only history except via retention policy. A failed or unavailable destination is reported separately.

## E. Notifications and shared release
When a job relinquishes its last claims, persist job outcome and issue job completion notification without waiting for other jobs sharing storage. When final shared-storage user exits, independently execute release and record lifecycle result; emit lifecycle notification. A later release failure must not mutate prior job result.

## F. Non-goals
No auto retry, no auto unlock, no auto repo init, no rollback of successful copies, no mandatory daemon, no generic backup engine abstraction. D106 fairness remains paused.
