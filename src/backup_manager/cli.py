"""Backup Manager command line."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from importlib.metadata import version
import json
import signal
from pathlib import Path
import sys
from threading import Event
from time import monotonic_ns

from .config import EXECUTION_DEFAULTS, IDENTIFIER, load_global, load_job
from .errors import Diagnostic, InvalidInvocation, Result, Status
from . import state
from .storage_coordinator import StorageCoordinator
from .job_history import phase_rows, request_cancel, run_rows
from .jobs import JobRunner, destinations, register_destination

DEFAULT_CONFIG = Path("/etc/backup-manager/config.toml")


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse errors may quote arbitrary input.
        raise InvalidInvocation from None


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return number


def identifier(value):
    if not IDENTIFIER.fullmatch(value):
        raise argparse.ArgumentTypeError("invalid identifier")
    return value


def common_flags(parser):
    parser.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--timeout-seconds", type=positive, default=argparse.SUPPRESS)


def build_parser():
    parser = Parser(prog="backup-manager", allow_abbrev=False)
    common_flags(parser)
    root = parser.add_subparsers(dest="group", required=True)
    for group, commands in {
        "config": ("validate",),
        "job": ("list", "show", "run"),
        "batch": ("run",),
        "storage": ("list", "inspect", "recover"),
        "repository": ("init", "register", "inspect", "unlock", "approve-replacement"),
        "retention": ("run", "preview"),
        "verify": ("run",),
        "history": ("list", "show"),
        "operation": ("list", "inspect", "cancel"),
        "state": ("init", "check", "backup", "recover"),
    }.items():
        parent = root.add_parser(group, allow_abbrev=False)
        common_flags(parent)
        leaves = parent.add_subparsers(dest="action", required=True)
        for action in commands:
            leaf = leaves.add_parser(action, allow_abbrev=False)
            common_flags(leaf)
            configure_command(leaf, group, action)
    doctor = root.add_parser("doctor", allow_abbrev=False)
    common_flags(doctor)
    doctor.add_argument("--preflight", action="store_true")
    common_flags(root.add_parser("version", allow_abbrev=False))
    return parser


def configure_command(parser, group, action):
    command = group, action
    if command == ("config", "validate"):
        selection = parser.add_mutually_exclusive_group()
        selection.add_argument("--job", type=identifier)
        selection.add_argument("--all", action="store_true")
    if command == ("job", "list"):
        parser.add_argument("--enabled-only", action="store_true")
    if command in {("job", "show"), ("job", "run")} or group in {"repository", "retention", "verify"}:
        parser.add_argument("job", type=identifier)
    if group == "batch":
        parser.add_argument("jobs", nargs="*", type=identifier)
        parser.add_argument("--all", action="store_true")
    if command in {("job", "run"), ("batch", "run")}:
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--preflight", action="store_true")
    if group == "storage" and action != "list":
        parser.add_argument("storage", type=identifier)
    if group == "repository":
        parser.add_argument("destination", type=identifier)
        if action == "approve-replacement":
            parser.add_argument("--expected-id", required=True)
    if command in {("storage", "inspect"), ("repository", "inspect")}:
        parser.add_argument("--preflight", action="store_true")
    if group in {"retention", "verify"}:
        parser.add_argument("--destination", type=identifier)
    if command == ("history", "list"):
        parser.add_argument("--job", type=identifier)
        parser.add_argument("--limit", type=positive)
    if command == ("history", "show"):
        parser.add_argument("run_id")
    if group == "operation":
        if action == "list":
            parser.add_argument("--active-only", action="store_true")
        else:
            parser.add_argument("operation_id")
    if group == "state" and action in {"backup", "recover"}:
        parser.add_argument("path", type=Path)
    if command in {
        ("storage", "recover"), ("repository", "init"), ("repository", "register"),
        ("repository", "unlock"), ("repository", "approve-replacement"),
        ("operation", "cancel"), ("state", "init"), ("state", "recover"),
    }:
        parser.add_argument("--yes", action="store_true")


def config_validate(args):
    config_path = getattr(args, "config", DEFAULT_CONFIG)
    global_config = load_global(config_path)
    errors = list(global_config.errors)
    jobs_directory = config_path.parent / "jobs"
    if args.job:
        paths = [jobs_directory / f"{args.job}.toml"]
    elif args.all:
        try:
            paths = sorted(
                path for path in jobs_directory.iterdir() if path.suffix == ".toml"
            )
        except OSError:
            return Result(Status.INVALID, errors=tuple(errors) + (
                Diagnostic("JOBS_READ", "Could not read the jobs directory."),
            ))
    else:
        paths = []
    for path in paths:
        errors.extend(load_job(path, global_config).errors)
    return Result(Status.INVALID if errors else Status.FULL, errors=tuple(errors))


def confirm(args, target):
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise InvalidInvocation
    print(f"Type {target} to confirm.", file=sys.stderr)
    if sys.stdin.readline().rstrip("\n") != target:
        raise InvalidInvocation


def dispatch(args):
    if args.group == "version":
        return Result(data={"version": version("backup-manager")})
    if args.group == "config":
        return config_validate(args)
    if args.group == "state" and args.action in {"init", "check", "backup"}:
        config = load_global(getattr(args, "config", DEFAULT_CONFIG))
        if config.errors:
            return Result(Status.INVALID, errors=config.errors)
        path = Path(config.data["state"]["path"])
        anchor = Path(config.data["state"]["anchor_path"])
        if args.action == "init":
            confirm(args, str(path))
        try:
            if args.action == "init":
                data = state.initialize(path, anchor)
            elif args.action == "backup":
                data = state.backup_state(path, anchor, args.path.absolute())
            else:
                data = state.check(path, anchor)
        except state.StateUnsafe as error:
            return Result(Status.UNSAFE, errors=(Diagnostic("STATE_UNSAFE", str(error)),))
        return Result(data=data)
    if args.group == "storage" and args.action == "recover":
        config = load_global(getattr(args, "config", DEFAULT_CONFIG))
        if config.errors:
            return Result(Status.INVALID, errors=config.errors)
        if args.storage not in config.data["storage"]:
            return Result(Status.INVALID, errors=(Diagnostic("UNKNOWN_STORAGE", "Storage identifier is not configured."),))
        confirm(args, args.storage)
        execution = config.data.get("execution", {})
        budgets = (execution.get("acquire_timeout_seconds"), execution.get("release_timeout_seconds"))
        if any(budget is None for budget in budgets):
            return Result(Status.INVALID, errors=(Diagnostic("STORAGE_BUDGET", "Storage recovery requires configured lifecycle budgets."),))
        timeout = min(budgets[0], getattr(args, "timeout_seconds", budgets[0]))
        try:
            coordinator = StorageCoordinator(
                Path(config.data["state"]["path"]), Path(config.data["state"]["anchor_path"]),
                config.data["storage"], acquire_seconds=budgets[0], release_seconds=budgets[1],
            )
            return Result(data=coordinator.recover_storage(
                args.storage, deadline=monotonic_ns() + timeout * 1_000_000_000,
            ))
        except (state.StateUnsafe, TimeoutError):
            return Result(Status.UNSAFE, errors=(Diagnostic("STORAGE_RECOVERY", "Storage remains fenced pending reconciliation."),))
    if args.group == "batch" and bool(args.jobs) == args.all:
        raise InvalidInvocation
    if getattr(args, "limit", None) and args.limit > 1000:
        raise InvalidInvocation
    if (
        args.group == "job" or args.group in {"history", "operation"}
        or args.group == "batch" or (args.group, getattr(args, "action", None)) == ("repository", "register")
    ) and not (getattr(args, "dry_run", False) or getattr(args, "preflight", False)):
        return job_command(args)
    return Result(Status.UNSUPPORTED, errors=(
        Diagnostic("NOT_IMPLEMENTED", "Command is unavailable in the current milestone."),
    ))


@contextmanager
def cancellation_signals():
    flag = Event()
    previous = {}

    def stop(_signal, _frame):
        flag.set()

    try:
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, stop)
        yield flag.is_set
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def job_files(path):
    return sorted(item for item in (path.parent / "jobs").iterdir() if item.suffix == ".toml")


def state_locations(config):
    return Path(config["state"]["path"]), Path(config["state"]["anchor_path"])


def execute_job(global_config, job, *, wait, timeout, interrupted, admission_done=lambda: None):
    try:
        if job.errors:
            return Result(Status.INVALID, errors=job.errors)
        if not job.data["job"].get("enabled", True):
            return Result(Status.INVALID, errors=(Diagnostic("JOB_DISABLED", "The job is disabled."),))
        runner = JobRunner(global_config, job.data, timeout=timeout, interrupted=interrupted)
        return runner.execute(wait=wait, admission_done=admission_done)
    except state.StateBusy:
        return Result(Status.BUSY, errors=(Diagnostic("STATE_BUSY", "The state coordinator is busy."),))
    except (state.StateUnsafe, OSError):
        return Result(Status.UNSAFE, errors=(Diagnostic("STATE_UNSAFE", "The job state requires reconciliation."),))
    except Exception:
        return Result(Status.ERROR, errors=(Diagnostic("JOB_INTERNAL_ERROR", "The job could not complete safely."),))
    finally:
        admission_done()


def execute_batch(config, jobs, *, timeout, interrupted):
    maximum = config.get("execution", {}).get("max_jobs", EXECUTION_DEFAULTS["max_jobs"])
    completed = []
    with ThreadPoolExecutor(max_workers=min(maximum, len(jobs))) as executor:
        for job_id, document in jobs:
            ready = Event()
            future = executor.submit(
                execute_job, config, document, wait=True, timeout=timeout,
                interrupted=interrupted, admission_done=ready.set,
            )
            completed.append((job_id, future))
            ready.wait()
        outcomes = [(job_id, future.result()) for job_id, future in completed]
    status = Status.FAILED if any(outcome.status not in {Status.FULL, Status.PARTIAL} for _, outcome in outcomes) else (
        Status.PARTIAL if any(outcome.status is Status.PARTIAL for _, outcome in outcomes) else Status.FULL
    )
    return Result(status, data={
        "jobs": [{"job_id": job_id, **outcome.payload("job run")} for job_id, outcome in outcomes],
    })


def job_command(args):
    path = getattr(args, "config", DEFAULT_CONFIG)
    global_document = load_global(path)
    if global_document.errors:
        return Result(Status.INVALID, errors=global_document.errors)
    config = global_document.data
    timeout = getattr(args, "timeout_seconds", None)
    if args.group in {"history", "operation"}:
        return history_command(args, config)
    if (args.group, args.action) == ("job", "list") or args.group == "batch":
        try:
            files = job_files(path) if args.group == "job" or args.all else [
                path.parent / "jobs" / f"{job_id}.toml" for job_id in args.jobs
            ]
        except OSError:
            return Result(Status.INVALID, errors=(Diagnostic("JOBS_READ", "Could not read the jobs directory."),))
        documents = [(file.stem, load_job(file, global_document)) for file in files]
        if args.group == "job":
            entries = [
                {"job_id": job_id, "enabled": doc.data.get("job", {}).get("enabled", True), "valid": not doc.errors}
                for job_id, doc in documents
                if not args.enabled_only or doc.data.get("job", {}).get("enabled", True)
            ]
            return Result(data={"jobs": entries})
        if args.all:
            documents = [(key, doc) for key, doc in documents if doc.errors or doc.data["job"].get("enabled", True)]
        if not documents or len({item[0] for item in documents}) != len(documents):
            raise InvalidInvocation
        with cancellation_signals() as interrupted:
            return execute_batch(config, documents, timeout=timeout, interrupted=interrupted)
    job = load_job(path.parent / "jobs" / f"{args.job}.toml", global_document)
    if job.errors:
        return Result(Status.INVALID, errors=job.errors)
    if (args.group, args.action) == ("job", "show"):
        return Result(data={
            "job_id": args.job, "enabled": job.data["job"].get("enabled", True),
            "destination_ids": [name for name, _ in destinations(job.data)],
            "source_count": len(job.data["sources"]),
        })
    if args.group == "repository":
        if args.destination not in dict(destinations(job.data)):
            raise InvalidInvocation
        confirm(args, f"{args.job}/{args.destination}")
        with cancellation_signals() as interrupted:
            try:
                return register_destination(config, job.data, args.destination, timeout=timeout, interrupted=interrupted)
            except (state.StateUnsafe, OSError):
                return Result(Status.UNSAFE, errors=(Diagnostic("STATE_UNSAFE", "Registration requires safe state."),))
    with cancellation_signals() as interrupted:
        return execute_job(config, job, wait=False, timeout=timeout, interrupted=interrupted)


def history_command(args, config):
    path, anchor = state_locations(config)
    try:
        if (args.group, args.action) == ("operation", "cancel"):
            confirm(args, args.operation_id)
            with state.state_write(path, anchor, wait=5) as database:
                accepted = request_cancel(database, args.operation_id)
            if not accepted:
                return Result(Status.INVALID, errors=(Diagnostic("INACTIVE_OPERATION", "No active job matches this operation."),))
            return Result(data={"cancel_requested": True}, operation_id=args.operation_id)
        run_id = getattr(args, "run_id", getattr(args, "operation_id", None))
        with state.state_read(path, anchor, wait=5) as database:
            rows = run_rows(
                database, job_id=getattr(args, "job", None), run_id=run_id,
                limit=getattr(args, "limit", None) or 100, active_only=getattr(args, "active_only", False),
            )
            if run_id is not None and rows:
                rows[0]["phases"] = phase_rows(database, run_id)
        if run_id is not None and not rows:
            return Result(Status.INVALID, errors=(Diagnostic("RUN_NOT_FOUND", "The job run does not exist."),))
        return Result(data=rows[0] if run_id else {"runs": rows}, operation_id=run_id)
    except state.StateUnsafe:
        return Result(Status.UNSAFE, errors=(Diagnostic("STATE_UNSAFE", "History state requires reconciliation."),))


def print_human(result):
    if "runs" in result.data:
        for row in result.data["runs"]:
            print(row["run_id"], row["job_id"], row["state"], row["overall_outcome"] or "-")
    elif "job_id" in result.data and ("backup" in result.data or "state" in result.data):
        data = result.data
        print(data["job_id"], data.get("overall_outcome") or result.status.value[0],
              "backup", data.get("backup", data.get("backup_outcome")) or "-")
        for phase in data.get("phases", ()):
            print(phase["phase"], phase["target_id"] or "", phase["status"], phase["error_code"] or "")
    elif "jobs" in result.data:
        for item in result.data["jobs"]:
            print(item["job_id"], item.get("status", "enabled" if item.get("enabled") else "disabled"))
    elif not result.errors:
        print(result.data.get("version", result.status.value[0]))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    json_output = "--json" in argv
    command = "backup-manager"
    try:
        args = build_parser().parse_args(argv)
        command = " ".join(filter(None, (args.group, getattr(args, "action", None))))
        result = dispatch(args)
    except InvalidInvocation:
        result = Result(Status.INVALID, errors=(
            Diagnostic("INVALID_INVOCATION", "Invalid command arguments. See --help."),
        ))
    if json_output:
        print(json.dumps(result.payload(command)))
    else:
        for error in result.errors:
            context = " ".join(f"{key}={value}" for key, value in error.context.items())
            print(f"{error.code} {error.message} {context}".rstrip(), file=sys.stderr)
        if "--quiet" not in argv:
            print_human(result)
    return result.status.value[1]


if __name__ == "__main__":
    sys.exit(main())
