# Backup Manager — Codex implementation handoff

This is a **specification baseline**, not a built application. Begin with `AGENTS.md`, `docs/14-DECISION-REGISTER.md`, `docs/01-PRD.md`, `docs/02-SYSTEM-ARCHITECTURE.md`, and `docs/13-IMPLEMENTATION-ROADMAP.md`.

Authoritative confirmed product decisions are preserved in `docs/01-PRD.md`. Architecture A1–A12 and assistant-selected defaults are tracked separately in `docs/14-DECISION-REGISTER.md`. `docs/SPECIFICATION-STATUS.md` tracks validation gates. Exact Restic version compatibility and crash safety are not certified by this package.

`tools/restic-copy-poc.sh` is an exploratory fixture. `tests/acceptance/scenarios.yaml` seeds required test cases. All examples are illustrative and must be validated against implemented schemas before use.

## v1.0 specification freeze addendum
This package contains **design-complete reference contracts**, not a completed Backup Manager executable. See docs/16–20, `contracts/plugin_api_v1.py`, `db/001_initial.sql`, and `schemas/cli-result.schema.json`. Run `python tools/validate_handoff.py` after installing PyYAML and jsonschema to check static consistency. Codex must implement and execute all acceptance scenarios and certify a real Restic release before claiming readiness.

## Foundation development

Use Python 3.12 or 3.13. Build and test against the installed wheel so the CLI and its bundled schemas are exercised together.

```sh
python -m pip install -r requirements-dev.lock
python tools/validate_handoff.py
python -m build --no-isolation
python -m pip install --no-deps dist/*.whl
python -m unittest discover -s tests/unit -v
backup-manager --json version
```

For a local configuration check, put individual job files in a `jobs` directory next to the global TOML file. The validator uses the job ID as the filename stem, following the layout of the example job files. For example, `backup-manager --config ./config.toml config validate --job immich` reads `./jobs/immich.toml`. Use `--all` to check every TOML file in that directory. The foundation validates the static configuration and reports unavailable commands with exit 7. See [implementation status](IMPLEMENTATION-STATUS.md) for gate evidence.
