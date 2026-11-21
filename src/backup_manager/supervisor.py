"""Bounded supervisor for subprocesses in Linux user systemd scopes."""

import base64
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
from time import monotonic
from uuid import UUID

from .launch_journal import LaunchReceipt, fence_uncertain, record_intent, record_launched, record_scope_ready, record_terminated
from .process_identity import Presence, ProcessIdentity
from .scope_worker import OUTPUT_LIMIT, REQUEST_LIMIT
from .state import state_write

CGROUP_ROOT = Path("/sys/fs/cgroup")
FRAME_LIMIT = 4 * OUTPUT_LIMIT
STOP_TIMEOUT = 8


class ScopeCancellation(Exception):
    """The coordinator requested the scoped operation stop."""


class SupervisionUncertain(Exception):
    """The caller must retain claims and reconcile the durable launch intent."""


@dataclass(frozen=True)
class ScopedResult:
    exit_code: int | None
    stdout: bytes = field(repr=False)
    stderr: bytes = field(repr=False)
    truncated: bool
    containment: str
    stop_reason: str | None = None


class Frames:
    def __init__(self, stream):
        self.stream = stream
        self.pending = bytearray()

    def read(self, deadline, cancelled=None):
        with selectors.DefaultSelector() as selector:
            selector.register(self.stream, selectors.EVENT_READ)
            delimiter = self.pending.find(b"\n")
            while delimiter < 0:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError
                if cancelled is not None and cancelled():
                    raise ScopeCancellation
                wait = min(remaining, 0.1 if cancelled is not None else 1)
                if not selector.select(wait):
                    continue
                chunk = os.read(self.stream.fileno(), 65536)
                if not chunk or len(self.pending) + len(chunk) > FRAME_LIMIT:
                    raise SupervisionUncertain("Worker frame was missing or exceeded its limit.")
                previous = len(self.pending)
                self.pending.extend(chunk)
                delimiter = self.pending.find(b"\n", previous)
        line = self.pending[:delimiter]
        del self.pending[:delimiter + 1]
        return json.loads(line)


def send_request(stream, request, deadline):
    descriptor = stream.fileno()
    os.set_blocking(descriptor, False)
    remaining = memoryview(request)
    with selectors.DefaultSelector() as selector:
        selector.register(stream, selectors.EVENT_WRITE)
        while remaining:
            wait = deadline - monotonic()
            if wait <= 0:
                raise TimeoutError
            if not selector.select(min(wait, 1)):
                continue
            try:
                sent = os.write(descriptor, remaining)
            except BlockingIOError:
                continue
            remaining = remaining[sent:]


def cgroup_for(worker, unit):
    if worker.presence() is not Presence.SAME:
        raise SupervisionUncertain("Could not verify the scoped worker identity.")
    contents = Path(f"/proc/{worker.pid}/cgroup").read_text().splitlines()
    path = next((line[3:] for line in contents if line.startswith("0::")), "")
    cgroup = Path(path)
    if not cgroup.is_absolute() or cgroup.name != unit or ".." in cgroup.parts:
        raise SupervisionUncertain("Could not verify the expected worker scope.")
    location = CGROUP_ROOT / cgroup.relative_to("/")
    events = (location / "cgroup.events").read_text().splitlines()
    if "populated 1" not in events or worker.presence() is not Presence.SAME:
        raise SupervisionUncertain("Could not verify live containment.")
    return location


def empty_cgroup(location):
    try:
        return "populated 0" in (location / "cgroup.events").read_text().splitlines()
    except FileNotFoundError:
        # A previously observed cgroup cannot be removed while populated.
        return CGROUP_ROOT.is_mount() and not location.exists()
    except OSError:
        return False


def stop_scope(unit):
    try:
        stopped = subprocess.run(
            ["systemctl", "--user", "stop", unit], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=STOP_TIMEOUT,
            check=False,
        )
        return stopped.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def execute(intent, argv, *, deadline, on_launch, cwd=None, env=None, stdin_payload=None, cancelled=None, on_scope_ready=None, user=None, group=None, repository=None, source_repository=None, executable_sha256=None):
    """Run after the caller commits intent. The callback must durably record the receipt."""
    unit = f"backup-manager-{UUID(intent.execution_id).hex}.scope"
    request = json.dumps({
        "argv": list(argv), "cwd": os.fspath(cwd) if cwd else None,
        "env": env, "user": user, "group": group, "repository": repository,
        "source_repository": source_repository, "executable_sha256": executable_sha256,
        "stdin": base64.b64encode(stdin_payload).decode("ascii") if stdin_payload is not None else None,
    }).encode() + b"\n"
    if len(request) > REQUEST_LIMIT or monotonic() >= deadline:
        raise SupervisionUncertain("Command request is too long or its deadline has expired.")
    try:
        wrapper = subprocess.Popen(
            ["systemd-run", "--user", "--scope", "--quiet", f"--unit={unit}",
             "--property=KillMode=control-group", "--property=TimeoutStopSec=2s",
             sys.executable, "-m", "backup_manager.scope_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise SupervisionUncertain("Could not establish a scope launch.") from error

    location = None
    launched = False
    try:
        frames = Frames(wrapper.stdout)
        worker = ProcessIdentity(**frames.read(deadline)["worker"])
        location = cgroup_for(worker, unit)
        containment = str(location.relative_to(CGROUP_ROOT))
        namespace = os.readlink("/proc/self/ns/cgroup")
        if on_scope_ready is not None:
            on_scope_ready(LaunchReceipt(intent.supervisor_id, containment, worker, namespace))
        send_request(wrapper.stdin, request, deadline)
        response = frames.read(deadline)
        if response.get("not_started") is True:
            return ScopedResult(None, b"", b"", False, containment, "not_started")
        process = ProcessIdentity(**response["process"])
        if process.boot_id != worker.boot_id or process.pid_namespace != worker.pid_namespace:
            raise SupervisionUncertain("The launched process identity differs from the scoped worker.")
        on_launch(LaunchReceipt(intent.supervisor_id, containment, process, namespace))
        launched = True
        result = frames.read(deadline, cancelled)
        return_value = ScopedResult(
            int(result["exit_code"]), base64.b64decode(result["stdout"], validate=True),
            base64.b64decode(result["stderr"], validate=True), bool(result["truncated"]),
            str(location.relative_to(CGROUP_ROOT)),
        )
    except ScopeCancellation:
        # The finally block verifies containment termination before returning it.
        return_value = ScopedResult(None, b"", b"", False, containment, "cancelled")
    except TimeoutError as error:
        if not launched:
            raise SupervisionUncertain("The launch deadline expired before a receipt was verified.") from error
        return_value = ScopedResult(None, b"", b"", False, containment, "timeout")
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise SupervisionUncertain("Scope command did not return a verified result.") from error
    finally:
        try:
            stop_scope(unit)
        finally:
            wrapper.stdin.close()
            wrapper.stdout.close()
            try:
                wrapper.wait(timeout=STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                wrapper.kill()
                wrapper.wait(timeout=STOP_TIMEOUT)
        if location is None or not empty_cgroup(location):
            raise SupervisionUncertain("Could not confirm the scope was emptied.")
    return return_value


def run_journaled(path, anchor, operation_id, owner_id, argv, *, deadline, cwd=None, env=None, stdin_payload=None, authorize=None, cancelled=None, user=None, group=None, repository=None, claim_operation_id=None, source_repository=None, executable_sha256=None):
    """Run a declared operation and durably journal the supervised transitions."""
    with state_write(path, anchor, wait=5) as database:
        existing = database.execute(
            "SELECT 1 FROM command_executions WHERE operation_id = ?", (operation_id,),
        ).fetchone()
        authorized = existing is None and (authorize is None or authorize(database))
        intent = record_intent(
            database, operation_id, owner_id, scope_gate=True, claim_operation_id=claim_operation_id,
        ) if authorized else None
    if intent is None:
        raise SupervisionUncertain("The operation no longer authorizes launch.")

    def persist(receipt):
        with state_write(path, anchor, wait=5) as database:
            record_launched(database, intent, receipt)

    def persist_scope(receipt):
        with state_write(path, anchor, wait=5) as database:
            record_scope_ready(database, intent, receipt)

    try:
        result = execute(
            intent, argv, deadline=deadline, on_launch=persist, cwd=cwd, env=env,
            stdin_payload=stdin_payload, cancelled=cancelled, on_scope_ready=persist_scope,
            user=user, group=group, repository=repository, source_repository=source_repository,
            executable_sha256=executable_sha256,
        )
    except SupervisionUncertain:
        with state_write(path, anchor, wait=5) as database:
            fence_uncertain(database, intent)
        raise
    with state_write(path, anchor, wait=5) as database:
        record_terminated(database, intent, result)
    return result
