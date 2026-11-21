# Implementation status

Baseline v1.0. I1 foundation is implemented. I2 is in progress. I3–I9 are pending. Mandatory acceptance scenarios remain 0 executed and 0 passed out of 40. Restic certification, runtime migrations, and process/storage crash recovery have no execution evidence.

## I1 foundation checklist

- [x] D1 (foundation scope), Linux PEP 621 package and console command.
- [x] D35 (CLI foundation), typed statuses, exit code mapping and schema-compatible JSON result.
- [x] D49 (static subset), strict TOML structure, multiple diagnostics, destination identifier/path checks, and redaction of offending values.
- [x] D50 (snapshot primitive), deep immutable configuration detached from later edits. Batch admission integration is pending.
- [x] D47 (configuration boundary), malformed TOML and validation diagnostics suppress raw values. Runtime secret handling is pending.
- [x] Foundation CI and isolated unit fixtures.
- [x] D56, validate approved job timeout overrides.
- [x] D52, validate approved per-job history limits.
- [x] D22/R3/G6, validate approved explicit prune selection.
- [ ] D49, integrate provider configuration schema validation and complete acceptance coverage when providers and runtime exist.

### Evidence

Local Linux x86_64, CPython 3.13.5, 2026-11-21 UTC. Changes built on baseline revision `fe47883592b37341af0fe5b03678d1c9328d5dd8`.

| Command | Exit | Observation |
|---|---:|---|
| `/tmp/bm-dev/bin/python tools/validate_handoff.py` | 0 | Passed before and after changes. Four schemas, three examples, reference DDL, 140 requirements and 40 scenario definitions verified statically. |
| `/tmp/bm-dev/bin/python -m build --no-isolation --outdir /tmp/bm-i1-dist` | 0 | Built sdist and built wheel from that sdist. Build log at `/tmp/bm-i1-build.log`. |
| `/tmp/bm-dev/bin/python -m pip install --no-deps --force-reinstall /tmp/bm-i1-dist/*.whl` | 0 | Installed wheel in the isolated development environment. |
| `/tmp/bm-dev/bin/python -m unittest discover -s tests/unit -v` | 0 | Eight passed against the installed wheel. Report at `/tmp/bm-i1-tests.log`. |
| `/tmp/bm-dev/bin/backup-manager --json version` | 0 | One versioned JSON result for installed package 0.1.0. |

Wheel SHA-256 `7ac8c8719af32a28eeb2a28deab9eb15bee13f659f0b6b9ab619adc53c8bb9f9`.
Source distribution SHA-256 `f5e481cf31d7a10bfa875f2470a5dbb0e2459ff9f570187324e9944062e0f88f`.
Python 3.12 and hosted CI were not run locally. The installed Restic reports version 0.18.0. The version query is environment inventory only.

### I1 configuration clarification checkpoint

The user confirmed the three configuration decisions recorded in docs/14. Static handoff, sdist/wheel build, wheel installation, and the ten-test foundation suite all exited 0 after the changes. Logs are at `/tmp/bm-i1-clarifications-build.log` and `/tmp/bm-i1-clarifications-tests.log`. The runtime enforcement of retention, history deletion, and deadlines belongs to later milestones.

The CLI uses argparse, packages the checked-in schemas with setuptools, and locates individual job configuration at `jobs/<job-id>.toml` relative to the global configuration file.

## I2 plugin and storage checkpoint

- [x] A1/D47, shipped provider API, discovery with manifest and capabilities checks, file and environment providers.
- [x] A2/D86/D91, read-only Linux mount inspection primitive with block-device identity verification. Positive verification on real block devices remains pending.
- [ ] A5/D82/D84/D92/D93/D94/L3, supervised side-effecting lifecycle path and tested ownership and termination guarantees.

The installed wheel passed 14 unit tests on Linux x86_64 with Python 3.13.5. Static handoff validation, package build and wheel installation exited 0. The new assertions cover duplicate providers, secret file permissions, final symlinks and FIFOs, absent environment secrets, and rejection of an unmounted directory.

### Lifecycle gate blocker

On Linux 6.12.101+deb13-amd64, `python3 tests/experiments/linux_unmount_identity.py` ran in a disposable user and mount namespace. It pinned a manager fixture mount with `O_PATH`, stacked a second fixture mount over it, and passed the saved descriptor path to `umount2`. The syscall returned zero and removed the **second** mount. The pinned manager fixture remained mounted until fixture cleanup. This rules out both a check followed by pathname unmount and the tested descriptor-path substitution as ownership-preserving release approaches under concurrent mount replacement.

The built-in provider exposes read-only capabilities pending a release mechanism tied to the verified mount instance, or an approved operating restriction that rules out concurrent external mount changes. No runtime mount or unmount command is wired. D92 and D94 prohibit using the observed behavior in production. I2 remains open and the dependency gate holds I3–I9.
