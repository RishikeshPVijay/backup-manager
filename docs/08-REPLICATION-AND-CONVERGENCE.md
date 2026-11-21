# Replication and convergence

## Normal replication

After backup success and cleanup, re-evaluate destinations; source is fixed to backup-receiving repo for whole normal phase. Enumerate complete source history and copy explicit frozen snapshot IDs to each available other destination. Confirm expected logical representation at target. Independent target outcomes, no automatic retry, no rollback. If source disappears, fail affected remaining copies without switching normal source.

## Convergence

After normal replication, enumerate complete snapshot histories of available repos. Resolve validated logical equivalence using Restic-native semantics + revalidated persistent provenance. Compute logical union, plan additive transfers to every available required destination. Preserve divergent histories; do not prefilter by retention. Copy-source and copy-destination claims acquired atomically, storage first. Parallel copies to distinct destinations permitted; serialize copies to same destination (A9). Freeze each attempt's source snapshot IDs; A10 incrementally replan only pending work after refreshed observations. A12 process launch is start boundary; A11 failed launched attempt blocks its assigned snapshot-to-destination requirements for that run even if alternate source appears. Preserve unrelated successes. Unavailable required destinations or unresolved equivalence yield partial job if backup succeeded.

## Retention

Only after both transfer phases, apply one job-level policy independently at each available destination. A snapshot copied moments earlier may be forgotten immediately. Forget/prune errors independent; never use retention to make replication look converged. Separately scheduled/admin retention respects same repository coordination.

## Evidence and failure

Do not infer absence from incomplete enumeration; do not trust stale index; never conflate snapshot ID equality across repos with logical equivalence. An attempt requires successful Restic exit AND destination confirmation. Attempt result, requirement result, and phase result distinct. No automatic resumption of interrupted copy; future independent run may benefit from already written Restic data.
