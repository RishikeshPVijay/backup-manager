"""Independent, durable attempts to invoke configured notification commands."""

import json
from uuid import uuid4

from .job_hooks import hook_environment
from .launch_journal import now
from .providers import discover
from .state import state_write
from .supervisor import SupervisionUncertain, run_journaled
from time import monotonic_ns


def notify(path, anchor, owner, hooks, kind, entity, outcome, maximum):
    if not hooks:
        return
    providers = discover("secrets")
    with state_write(path, anchor, wait=5) as database:
        event_id = str(uuid4())
        database.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
            (event_id, entity, f"{kind}_notification", json.dumps(outcome), now()),
        )
    for item in hooks:
        attempt = str(uuid4())
        with state_write(path, anchor, wait=5) as database:
            database.execute("INSERT INTO notification_attempts VALUES (?, ?, 'started', NULL)", (attempt, event_id))
        end = monotonic_ns() + min(maximum, item.get("timeout_seconds", maximum)) * 1_000_000_000
        code = None
        uncertain = False
        try:
            environment, _ = hook_environment(providers, item, {
                "PHASE": "notification", "EVENT_KIND": kind, "ENTITY_ID": entity,
                "EVENT_JSON": json.dumps(outcome),
            }, end)
            result = run_journaled(
                path, anchor, attempt, owner, (item["executable"], *item.get("args", ())),
                deadline=end / 1_000_000_000, env=environment, cwd=item.get("cwd"),
                user=item.get("user"), group=item.get("group"),
            )
            if result.exit_code != 0:
                code = "NOTIFICATION_FAILED"
        except SupervisionUncertain:
            code = "NOTIFICATION_UNCERTAIN"
            uncertain = True
        except Exception:
            code = "NOTIFICATION_FAILED"
        with state_write(path, anchor, wait=5) as database:
            database.execute(
                "UPDATE notification_attempts SET status = ?, error_code = ? WHERE id = ?",
                ("failed" if code else "succeeded", code, attempt),
            )
        if uncertain:
            return
