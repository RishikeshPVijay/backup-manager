"""Export selected observations from isolated, already-asserted test state."""

import json
import os
from pathlib import Path


def record(database, assertion, result):
    filename = os.environ.get("BACKUP_MANAGER_ACCEPTANCE_TRACE")
    if filename:
        events = database.execute("SELECT entity_id, type, occurred_at FROM events ORDER BY rowid").fetchall()
        with Path(filename).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"assertion": assertion, "result": result, "events": events}) + "\n")
