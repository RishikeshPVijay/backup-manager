"""Bounded supervisor for subprocesses in Linux user systemd scopes."""

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
from time import monotonic
from uuid import UUID

from .launch_journal import LaunchReceipt, fence_uncertain, record_intent, record_launched, record_terminated
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
    stdout: bytes
    stderr: bytes
    truncated: bool
    containment: str


class Frames:
    def __init__(self, stream):
        self.stream = stream
        self.pending = bytearray()

    def read(self, deadline, cancelled=None):
        with selectors.DefaultSelector() as selector:
            selector.register(self.stream, selectors.EVENT_READ)
            while b"\n" not in self.pending:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError
                if cancelled is not None and cancelled():
                    raise ScopeCancellation
                wait = min(remaining, 0.1) if cancelled is not None else remaining
                if not selector.select(wait):
                    continue
                chunk = os.read(self.stream.fileno(), 8192)
                if not chunk or len(self.pending) + len(chunk) > FRAME_LIMIT:
                    raise SupervisionUncertain("Worker frame was missing or exceeded its limit.")
                self.pending.extend(chunk)
        line, rest = self.pending.split(b"\n", 1)
        self.pending = bytearray(rest)
        return json.loads(line)


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


def execute(intent, argv, *, deadline, on_launch, cwd=None, env=None, stdin_payload=None, cancelled=None):
    """Run after the caller commits intent. The callback must durably record the receipt."""
    unit = f"backup-manager-{UUID(intent.execution_id).hex}.scope"
    request = json.dumps({
        "argv": list(argv), "cwd": os.fspath(cwd) if cwd else None,
        "stdin": base64.b64encode(stdin_payload).decode("ascii") if stdin_payload is not None else None,
    }).encode() + b"\n"
    if len(request) > REQUEST_LIMIT or monotonic() >= deadline:
        raise SupervisionUncertain("Command request is too long or its deadline has expired.")
    try:
        wrapper = subprocess.Popen(
            ["systemd-run", "--user", "--scope", "--quiet", f"--unit={unit}",
             "--property=KillMode=control-group", "--property=TimeoutStopSec=2s",
             sys.executable, "-m", "backup_manager.scope_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
        )
    except OSError as error:
        raise SupervisionUncertain("Could not establish a scope launch.") from error

    location = None
    try:
        frames = Frames(wrapper.stdout)
        worker = ProcessIdentity(**frames.read(deadline)["worker"])
        location = cgroup_for(worker, unit)
        wrapper.stdin.write(request)
        wrapper.stdin.flush()
        process = ProcessIdentity(**frames.read(deadline)["process"])
        if process.boot_id != worker.boot_id or process.pid_namespace != worker.pid_namespace:
            raise SupervisionUncertain("The launched process identity differs from the scoped worker.")
        on_launch(LaunchReceipt(intent.supervisor_id, str(location.relative_to(CGROUP_ROOT)), process))
        result = frames.read(deadline, cancelled)
        return_value = ScopedResult(
            int(result["exit_code"]), base64.b64decode(result["stdout"], validate=True),
            base64.b64decode(result["stderr"], validate=True), bool(result["truncated"]),
            str(location.relative_to(CGROUP_ROOT)),
        )
    except ScopeCancellation:
        # The finally block verifies containment termination before returning it.
        return_value = ScopedResult(None, b"", b"", False, str(location.relative_to(CGROUP_ROOT)))
    except (OSError, TimeoutError, ValueError, TypeError, KeyError) as error:
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


def run_journaled(path, anchor, operation_id, owner_id, argv, *, deadline, cwd=None, env=None, stdin_payload=None, authorize=None, cancelled=None):
    """Run a declared operation and durably journal the supervised transitions."""
    with state_write(path, anchor) as database:
        authorized = authorize is None or authorize(database)
        intent = record_intent(database, operation_id, owner_id) if authorized else None
    if intent is None:
        raise SupervisionUncertain("The operation no longer authorizes launch.")

    def persist(receipt):
        with state_write(path, anchor) as database:
            record_launched(database, intent, receipt)

    try:
        result = execute(
            intent, argv, deadline=deadline, on_launch=persist, cwd=cwd, env=env,
            stdin_payload=stdin_payload, cancelled=cancelled,
        )
    except SupervisionUncertain:
        with state_write(path, anchor) as database:
            fence_uncertain(database, intent)
        raise
    with state_write(path, anchor) as database:
        record_terminated(database, intent, result)
    return result
