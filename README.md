# Backup Manager — Codex implementation handoff

This is a **specification baseline**, not a built application. Begin with `AGENTS.md`, `docs/14-DECISION-REGISTER.md`, `docs/01-PRD.md`, `docs/02-SYSTEM-ARCHITECTURE.md`, and `docs/13-IMPLEMENTATION-ROADMAP.md`.

Authoritative confirmed product decisions are preserved in `docs/01-PRD.md`. Architecture A1–A12 and assistant-selected defaults are tracked separately in `docs/14-DECISION-REGISTER.md`. `docs/SPECIFICATION-STATUS.md` tracks validation gates. Exact Restic version compatibility and crash safety are not certified by this package.

`tools/restic-copy-poc.sh` is an exploratory fixture. `tests/acceptance/scenarios.yaml` seeds required test cases. All examples are illustrative and must be validated against implemented schemas before use.

## v1.0 specification freeze addendum
This package contains **design-complete reference contracts**, not a completed Backup Manager executable. See docs/16–20, `contracts/plugin_api_v1.py`, `db/001_initial.sql`, and `schemas/cli-result.schema.json`. Run `python tools/validate_handoff.py` after installing PyYAML and jsonschema to check static consistency. Codex must implement and execute all acceptance scenarios and certify a real Restic release before claiming readiness.
