# State machines and transition contracts

## Job

VALIDATING → ADMITTED → SELECTING_SOURCE → PREPARING → BACKING_UP → POST_AND_CLEANUP → NORMAL_REPLICATION → CONVERGENCE → RETENTION → FINALIZING → COMPLETED. Failed preparation/backup bypasses replication/convergence, but cleanup after preparation begins still runs. Hook, retention, notification and storage release outcomes independent. Job overall FULL if backup and all required replication/convergence satisfied, PARTIAL if backup successful but any required destination missing/failed, FAILED if backup unsuccessful. Cancel/interruption recorded distinctly and mapped to documented CLI exit status.

## Storage

IDLE → ACQUIRING → ACQUIRED → RELEASING → IDLE. Exceptional `UNAVAILABLE`, `TERMINATION_UNCERTAIN`, `RECOVERY_REQUIRED`; conflicting requests blocked while uncertain. Concurrent users join acquisition; last withdrawing participant cancels unused acquire; new requests during release wait for release completion; externally mounted storage never unmounted by manager. Shared resource release lifecycle recorded independently from job completion.

## Repository claim set

REQUESTED → WAITING → ACTIVE → RELEASING → RELEASED; `RECOVERY_REQUIRED` when process termination uncertain. Claim set admission atomic across all repositories. No partial active claim sets. Waiting never holds SQLite transaction. `COPY_DESTINATION` conflicts with itself, regardless of Restic native locks.

## Transfer

PLANNED → ACQUIRING → PREFLIGHT → LAUNCHING → RUNNING → VERIFYING → SUCCEEDED. Pre-launch plan can be superseded/replanned. Persist LAUNCH_INTENT before spawn; uncertain launch treated as potentially started until reconciled. At successful supervised process launch A12 freezes no-retry boundary. Any post-launch failure/unverified result blocks assigned requirements for remainder of run (A11). Replanning only pending/unstarted work (A10). No silent source substitution after launch. All terminal outcomes immutable historical facts.

## Requirements

MISSING → PLANNED → EXECUTING → SATISFIED or FAILED; also UNAVAILABLE/UNRESOLVED/CANCELLED. A later observation may update current inventory but cannot rewrite historical failed attempt. Distinct run requirements tracked by logical snapshot equivalence group + destination generation. Missing equivalence evidence never counts as satisfied.

## Process

CREATED → LAUNCH_INTENT → LAUNCH_CONFIRMED → RUNNING → STOP_REQUESTED → TERMINATED; UNCERTAIN and RECOVERY_REQUIRED side paths. Never reclaim active claims without positive termination evidence. Child escape requires containment evidence; fail closed if unverifiable.
