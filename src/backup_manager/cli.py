"""Backup Manager command line."""

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import sys

from .config import IDENTIFIER, load_global, load_job
from .errors import Diagnostic, InvalidInvocation, Result, Status
from . import state

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


def dispatch(args):
    if args.group == "version":
        return Result(data={"version": version("backup-manager")})
    if args.group == "config":
        return config_validate(args)
    if args.group == "state" and args.action in {"init", "check"}:
        config = load_global(getattr(args, "config", DEFAULT_CONFIG))
        if config.errors:
            return Result(Status.INVALID, errors=config.errors)
        path = Path(config.data["state"]["path"])
        anchor = Path(config.data["state"]["anchor_path"])
        if args.action == "init" and not args.yes:
            if not sys.stdin.isatty():
                raise InvalidInvocation
            print(f"Type {path} to confirm initialization.", file=sys.stderr)
            if sys.stdin.readline().rstrip("\n") != str(path):
                raise InvalidInvocation
        try:
            data = state.initialize(path, anchor) if args.action == "init" else state.check(path, anchor)
        except state.StateUnsafe as error:
            return Result(Status.UNSAFE, errors=(Diagnostic("STATE_UNSAFE", str(error)),))
        return Result(data=data)
    if args.group == "batch" and bool(args.jobs) == args.all:
        raise InvalidInvocation
    if getattr(args, "limit", None) and args.limit > 1000:
        raise InvalidInvocation
    return Result(Status.UNSUPPORTED, errors=(
        Diagnostic("NOT_IMPLEMENTED", "Command is unavailable in the current milestone."),
    ))


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
        if "--quiet" not in argv and not result.errors:
            print(result.data.get("version", result.status.value[0]))
    return result.status.value[1]


if __name__ == "__main__":
    sys.exit(main())
