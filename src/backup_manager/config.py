"""Strict, side-effect-free TOML reading and immutable configuration snapshots."""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
import json
from pathlib import Path, PurePosixPath
import re
import tomllib
from types import MappingProxyType

from jsonschema import Draft202012Validator

from .errors import Diagnostic

IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}")


@cache
def validator(name: str) -> Draft202012Validator:
    schema = files("backup_manager.schemas").joinpath(f"{name}.schema.json")
    return Draft202012Validator(json.loads(schema.read_text(encoding="utf-8")))


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(map(freeze, value))
    return value


@dataclass(frozen=True)
class ConfigDocument:
    data: Mapping
    errors: tuple[Diagnostic, ...]


def read_document(path: Path, schema: str) -> tuple[dict, list[Diagnostic]]:
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, UnicodeError, ValueError):
        return {}, [Diagnostic("CONFIG_READ", "Could not read valid TOML.", {"file": path.name})]

    errors = []
    for error in validator(schema).iter_errors(data):
        # Validator messages may quote arbitrary configuration values.
        field = ".".join(map(str, error.absolute_path))
        errors.append(Diagnostic(
            "CONFIG_SCHEMA", f"Configuration violates the {error.validator} rule.",
            {"file": path.name, "field": field},
        ))
    if type(data.get("schema_version")) is not int and not any(
        error.context.get("field") == "schema_version" for error in errors
    ):
        errors.append(Diagnostic("CONFIG_VERSION", "An integer schema version is required."))
    return data, errors


def load_global(path: Path) -> ConfigDocument:
    data, errors = read_document(path, "global-config")
    return ConfigDocument(freeze(data), tuple(errors))


def load_job(path: Path, global_config: ConfigDocument) -> ConfigDocument:
    data, errors = read_document(path, "job-config")
    job = data.get("job")
    if isinstance(job, dict) and job.get("id") != path.stem:
        errors.append(Diagnostic("JOB_FILE", "Job identifier must match the TOML filename stem."))
    timeouts = data.get("timeouts")
    execution = global_config.data.get("execution", {})
    if isinstance(timeouts, dict) and isinstance(execution, Mapping):
        for field in ("job_timeout_seconds", "operation_timeout_seconds"):
            override = timeouts.get(field)
            maximum = execution.get(field)
            if type(override) is int and type(maximum) is int and override > maximum:
                errors.append(Diagnostic(
                    "JOB_TIMEOUT_CAP", "Job timeout settings may only shorten global budgets.",
                    {"file": path.name, "field": f"timeouts.{field}"},
                ))
    targets = data.get("destinations")
    if isinstance(targets, dict):
        secondary = targets.get("secondary", [])
        candidates = [targets.get("primary")]
        if isinstance(secondary, list):
            candidates.extend(secondary)
        ids = set()
        paths = set()
        storage = global_config.data.get("storage", {})
        for index, target in enumerate(candidates):
            if not isinstance(target, dict):
                continue
            target_id = target.get("id", "primary" if index == 0 else None)
            if isinstance(target_id, str):
                if target_id in ids:
                    errors.append(Diagnostic("DUPLICATE_DESTINATION", "Destination IDs must be unique."))
                ids.add(target_id)
            resource = target.get("storage")
            if isinstance(resource, str) and isinstance(storage, Mapping) and resource not in storage:
                errors.append(Diagnostic("UNKNOWN_STORAGE", "Destination references undefined storage."))
            repository = target.get("repository")
            if isinstance(repository, str) and isinstance(resource, str):
                normalized = PurePosixPath(repository)
                if not normalized.is_absolute() or ".." in normalized.parts or "\x00" in repository:
                    errors.append(Diagnostic("REPOSITORY_PATH", "Repository path must be absolute and traversal-free."))
                key = resource, normalized
                if key in paths:
                    errors.append(Diagnostic("DUPLICATE_REPOSITORY", "Destinations must use distinct repository paths."))
                paths.add(key)
    return ConfigDocument(freeze(data), tuple(errors))
