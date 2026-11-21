"""Read-only planning and mounted-storage inspection."""

import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
from time import monotonic

from .config import EXECUTION_DEFAULTS
from .errors import Diagnostic, Result, Status
from .job_hooks import repository_password
from .job_repository import inspect_repository, registration
from .jobs import destinations, seconds_deadline
from .providers import discover
from .restic import Binary, Repository, Restic, ResticEvidenceError, ResticUnsupported
from .scope_worker import OUTPUT_LIMIT, check_executable, repository_argv
from .secrets import SecretUnavailable
from .state import StateUnsafe, check, checked_record, connect, state_lock
from .storage_coordinator import StorageLease, context
from .supervisor import ScopedResult


def locations(config):
    return Path(config["state"]["path"]), Path(config["state"]["anchor_path"])


def dry_run(job):
    return Result(data={
        "mode": "dry-run", "job_id": job["job"]["id"],
        "destinations": [name for name, _ in destinations(job)],
        "sources": [{"path": item["path"], "required": item.get("required", True)} for item in job["sources"]],
        "phases": ["select", "pre", "backup", "post", "cleanup", "replication", "convergence", "retention"],
    })


def read_command(invocation, deadline, lock=None, binding=None):
    with (
        repository_argv(invocation.argv, binding) as (argv, descriptors),
        check_executable(invocation.argv[0], invocation.executable_sha256) as executable,
    ):
        descriptors = (*descriptors, *executable.pop("pass_fds", ()))
        process = subprocess.Popen(
            argv, env=invocation.env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            pass_fds=(*descriptors, *((lock,) if lock is not None else ())), **executable,
        )
    outputs = {0: bytearray(), 1: bytearray()}
    truncated = False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, 0)
            selector.register(process.stderr, selectors.EVENT_READ, 1)
            while selector.get_map():
                left = deadline - monotonic()
                if left <= 0:
                    raise TimeoutError
                for key, _ in selector.select(min(left, 1)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output = outputs[key.data]
                    remaining = OUTPUT_LIMIT - len(output)
                    output.extend(chunk[:remaining])
                    truncated |= len(chunk) > remaining
        code = process.wait(timeout=max(0, deadline - monotonic()))
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        raise
    finally:
        process.stdout.close()
        process.stderr.close()
    return ScopedResult(code, bytes(outputs[0]), bytes(outputs[1]), truncated, "read-only")


class MountedInspector:
    def __init__(self, config):
        self.config = config
        self.providers = discover("storage")

    def observe(self, name, deadline):
        item = self.config["storage"][name]
        provider = self.providers[item["provider"]]
        observed = asyncio.run(provider.inspect(item["config"], context(deadline)))
        return observed, observed.identity


def read_adapter(config, deadline, lock=None):
    binary = Binary.identify(config.get("restic", {}).get("executable", "restic"))
    return Restic.certified(binary, read_command(Restic.version(binary), deadline / 1_000_000_000, lock))


def observed_repository(database, inspector, adapter, job_id, name, target, deadline, lock):
    expected = registration(database, job_id, name)
    if expected is None or (expected.storage_id, expected.path) != (target["storage"], Path(target["repository"])):
        raise StateUnsafe("Repository registration is unavailable.")
    row = database.execute(
        "SELECT coordination_id, expected_identity FROM storage_resources WHERE resource_id = ?", (expected.storage_id,),
    ).fetchone()
    if row is None:
        raise StateUnsafe("Storage resource is unregistered.")
    coordination, physical = row
    if database.execute(
        """SELECT 1 FROM storage_operations JOIN storage_resources USING(resource_id)
           WHERE coordination_id = ? AND state != 'TERMINATED'""", (coordination,),
    ).fetchone() or database.execute(
        """SELECT 1 FROM repository_claims JOIN repository_claim_sets USING(claim_set_id)
           WHERE repository_key = ? AND state != 'RELEASED'""", (expected.key,),
    ).fetchone():
        raise StateUnsafe("Conflicting work prevents read-only inspection.")
    _, identity = inspector.observe(expected.storage_id, deadline)
    if physical is not None and (not identity.verified_physical or identity.physical_identity != physical):
        raise StateUnsafe("Storage identity differs from registration.")
    if physical is None and inspector.config["storage"][expected.storage_id].get("coordination_id") != coordination:
        raise StateUnsafe("Storage coordination identity differs from registration.")
    binding = inspect_repository(inspector, StorageLease("", expected.storage_id, coordination), expected.path, deadline)
    password = repository_password(discover("secrets"), target["password"], deadline)
    repo = Repository(expected.path, password)
    result = read_command(adapter.identity(repo), deadline / 1_000_000_000, lock, binding)
    identity = adapter.repository_identity(result)
    if identity.id != expected.restic_id:
        raise StateUnsafe("Repository ID has changed.")
    return expected, repo, binding, identity


def inspect_repositories(config, job, action, target, timeout=None):
    path, anchor = locations(config)
    deadline = seconds_deadline(min(timeout or float("inf"), config.get("execution", {}).get(
        "operation_timeout_seconds", EXECUTION_DEFAULTS["operation_timeout_seconds"],
    )))
    rows = []
    try:
        with state_lock(path, anchor, exclusive=True, wait=5) as lock:
            checked_record(path, anchor)
            with closing(connect(path, "ro&immutable=1")) as database:
                adapter = read_adapter(config, deadline, lock)
                inspector = MountedInspector(config)
                for name, settings in destinations(job):
                    if target is not None and name != target:
                        continue
                    _, repo, binding, identity = observed_repository(
                        database, inspector, adapter, job["job"]["id"], name, settings, deadline, lock,
                    )
                    data = {"destination_id": name, "repository_id": identity.id, "format": identity.version}
                    if action == "preview":
                        preview = read_command(
                            adapter.forget(repo, job["retention"], preview=True),
                            deadline / 1_000_000_000, lock, binding,
                        )
                        if preview.exit_code != 0 or preview.truncated:
                            raise ResticEvidenceError("Retention preview did not complete.")
                        groups = json.loads(preview.stdout)
                        if not isinstance(groups, list):
                            raise ResticEvidenceError("Retention preview returned invalid groups.")
                        data["groups"] = [
                            {key: [item["id"] for item in (group.get(key) or ())] for key in ("keep", "remove")}
                            for group in groups
                        ]
                    rows.append(data)
        return Result(data={"destinations": rows})
    except ResticUnsupported:
        return Result(Status.UNSUPPORTED, errors=(Diagnostic("RESTIC_UNSUPPORTED", "No exact certification profile matches Restic."),))
    except (StateUnsafe, ResticEvidenceError, SecretUnavailable, OSError, ValueError, TypeError, KeyError, TimeoutError, subprocess.TimeoutExpired):
        return Result(Status.UNSAFE, {"destinations": rows},
                      (Diagnostic("INSPECTION_INCOMPLETE", "Read-only repository verification could not complete safely."),))


def doctor(config, timeout=None):
    path, anchor = locations(config)
    data = {}
    try:
        data["state"] = check(path, anchor)
        adapter = read_adapter(config, seconds_deadline(min(timeout or 10, 10)))
        data["restic"] = {"fingerprint": adapter.binary.sha256, "version": adapter.profile.version_line}
        return Result(data=data)
    except ResticUnsupported:
        return Result(Status.UNSUPPORTED, data, (Diagnostic("RESTIC_UNSUPPORTED", "No exact certification profile matches Restic."),))
    except (StateUnsafe, ResticEvidenceError, OSError, ValueError, TimeoutError, subprocess.TimeoutExpired):
        return Result(Status.UNSAFE, data, (Diagnostic("DOCTOR_UNSAFE", "State or Restic verification requires attention."),))
