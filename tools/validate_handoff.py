#!/usr/bin/env python3
"""Static package consistency checks. Does NOT certify runtime behavior."""
from pathlib import Path
import csv,json,sqlite3,tomllib,sys
import hashlib
try:
 import yaml
 import jsonschema
except ImportError:
 raise SystemExit('Install validation dependencies: pip install PyYAML jsonschema')
root=Path(__file__).resolve().parents[1]
for name in ['global-config','job-config','plugin-manifest','cli-result']:
 s=json.loads((root/'schemas'/f'{name}.schema.json').read_text())
 jsonschema.Draft202012Validator.check_schema(s)
for name,schema in [('config','global-config'),('immich','job-config'),('documents','job-config')]:
 data=tomllib.loads((root/'examples'/f'{name}.toml').read_text())
 s=json.loads((root/'schemas'/f'{schema}.schema.json').read_text())
 jsonschema.validate(data,s)
conn=sqlite3.connect(':memory:');conn.executescript((root/'db/001_initial.sql').read_text());conn.execute('PRAGMA foreign_key_check');conn.close()
scenarios=yaml.safe_load((root/'tests/acceptance/scenarios.yaml').read_text())['scenarios']
ids={x['id'] for x in scenarios}
assert len(ids)==len(scenarios)==40
assert all(x['status']=='not_implemented' and x['assertions'] for x in scenarios)
with (root/'contracts/requirements-traceability.csv').open() as f: rows=list(csv.DictReader(f))
assert len(rows)==140, len(rows)
assert all(set(r['scenario_ids'].split(';')) <= ids for r in rows)
print(f'Static handoff validation passed: 4 schemas, 3 TOML examples, SQLite DDL, {len(rows)} requirement rows, {len(scenarios)} scenario specifications.')
print('No runtime tests or Restic certification executed.')

# A profile is an explicit package decision backed by a completed report.

registry = json.loads((root / "schemas/restic-profiles.json").read_text())
assert registry["schema_version"] == 1
assert len({profile["binary_sha256"] for profile in registry["profiles"]}) == len(registry["profiles"])
for profile in registry["profiles"]:
    report_bytes = (root / profile["report"]).read_bytes()
    assert hashlib.sha256(report_bytes).hexdigest() == profile["report_sha256"]
    report = json.loads(report_bytes)
    assert report["passed"] and report["unchanged_inputs"]
    assert report["restic_binary_sha256"] == profile["binary_sha256"]
    assert report["restic_version"] == profile["version_line"]
    assert all(case["status"] == "pass" and case["fixture_removed"] for case in report["cases"])
    for version in profile["repository_formats"]:
        assert {"identity-json-lineage", "native-locks-and-interruption"} <= {
            case["test_id"] for case in report["cases"] if case["repository_format"] == version
        }
print(f"Checked {len(registry['profiles'])} installed Restic profile report reference(s).")
