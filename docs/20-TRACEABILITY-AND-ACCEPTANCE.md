# Requirement traceability and test execution

`contracts/requirements-traceability.csv` enumerates all 140 confirmed D/R/G/L/A identifiers individually and assigns a preliminary acceptance scenario group. This is an index, not a claim that each individual requirement already has a sufficient assertion. Before release Codex MUST refine each row to precise assertion IDs and attach observed evidence; grouping alone is insufficient for signoff. G2's earlier retention-filter behavior is superseded by the common-retention clarification and must not be implemented.

`tests/acceptance/scenarios.yaml` lists 40 mandatory scenarios with preconditions, expected behavior and required evidence artifacts. They are specification cases, **not runnable tests**. Codex must implement them using disposable repositories and isolated SQLite state; preserve command, output, exit code, event trace and cleanup evidence. Mark a case passing only when executed with evidence. Critical safety tests cannot be waived or declared 'manual' merely because automation is difficult.

Evidence manifest per run: `{test_id, git_commit, environment, restic_version, restic_binary_sha256, command, expected, observed, exit_code, report_paths, passed}`. All required fields must be populated. CI must fail on missing, failed, or inconclusive critical tests. This document is a contract for implementation, not a test report.

## Executing the release gate

Run `python tools/run_acceptance.py --output /private/local/acceptance-results`
from a checkout with the installed wheel, the certified Restic executable on
PATH, and a usable user systemd manager. The output directory must be new.
The runner allocates disposable fixture directories, captures command logs,
keeps redacted CLI results and selected SQLite events, and writes
`scenarios.json` and `release-gate.json`. Each probe runs once even when
several scenarios use its assertions.

`tests/acceptance/assertions.yaml` maps the 40 scenario IDs to runnable probes
and identifies missing assertions or artifacts. A zero exit from a supporting
probe is insufficient when a scenario still lists a gap. A probe subset is
available for diagnosis and always leaves the overall gate closed. The
dedicated Linux workflow publishes artifacts even when the acceptance step
fails, so reviewers can distinguish runtime failures from incomplete evidence.
The broader workflow separately covers kernel mount fixtures.
