# Backup Manager

Backup Manager is a Linux command-line application for local Restic backups,
replication, convergence, and retention across registered repositories. It
uses SQLite for coordination and a user systemd manager for supervised commands.
See [implementation status](IMPLEMENTATION-STATUS.md) for tested gates and
[the operations guide](docs/19-RELEASE-AND-OPERATIONS.md) for installation,
upgrade, and recovery.

Use a Python 3.12 or 3.13 virtual environment and the exact Restic binary
listed in [the installed profile registry](schemas/restic-profiles.json).
The registry contains certified Linux x86-64 and ARM64 binaries for repository formats 1 and 2. An
operator must initialize state and explicitly register or initialize each
repository. The examples under `examples/` provide configuration templates.
Put each job TOML in a `jobs` directory next to the global TOML, naming the file
after the job ID.

## Build and test

```sh
python -m pip install -r requirements-dev.lock
python tools/validate_handoff.py
python tools/build_release.py --epoch "$(git show -s --format=%ct HEAD)" --output dist
python -m pip install --no-deps dist/*.whl
python -m unittest discover -s tests/unit -v
backup-manager --json version
```

Run `backup-manager --config /path/to/config.toml config validate --all`
before scheduling a job. Read [the acceptance contract](docs/20-TRACEABILITY-AND-ACCEPTANCE.md)
for the remaining release evidence requirements.
