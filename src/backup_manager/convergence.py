"""Restic native representation checks and additive requirement planning."""

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cached_property
import hashlib
import json
import re

from .restic import ResticEvidenceError, Snapshot, parse_snapshots, string_array

TIMESTAMP = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class NativeSnapshot(Snapshot):
    username: str = ""
    uid: int = 0
    gid: int = 0
    excludes: tuple[str, ...] = ()

    @cached_property
    def attributes(self):
        match = TIMESTAMP.fullmatch(self.time)
        if not match:
            raise ValueError("Restic snapshot time is not RFC 3339.")
        whole, fraction, offset = match.groups()
        epoch_time = datetime.fromisoformat(whole + offset).astimezone(timezone.utc).isoformat()
        return (
            epoch_time, (fraction or "").ljust(9, "0"), self.tree, self.hostname,
            self.username, self.uid, self.gid, self.excludes, len(self.paths), len(self.tags),
        )


def parse_native_inventory(result):
    """Parse the additional metadata compared by the certified copy command."""
    inventory = parse_snapshots(result)
    try:
        metadata = {row["id"]: row for row in json.loads(result.stdout)}
        snapshots = []
        for item in inventory:
            row = metadata[item.id]
            username = row.get("username", "")
            uid, gid = row.get("uid", 0), row.get("gid", 0)
            if (
                not isinstance(username, str) or type(uid) is not int or type(gid) is not int
                or item.original == "0" * 64
            ):
                raise ValueError
            native = NativeSnapshot(
                **vars(item), username=username, uid=uid, gid=gid,
                excludes=string_array(row.get("excludes", [])),
            )
            # Validate the complete native signature before publishing a view.
            native.attributes
            snapshots.append(native)
        return tuple(snapshots)
    except (ValueError, TypeError, KeyError, OverflowError):
        raise ResticEvidenceError("Inventory lacks valid Restic copy-equivalence metadata.") from None


def logical_identity(snapshot):
    return digest((
        snapshot.original or snapshot.id, snapshot.attributes,
        sorted(set(snapshot.paths)), sorted(set(snapshot.tags)),
    ))


def representation_index(inventory):
    index = {}
    for snapshot in inventory:
        for key in {snapshot.id, snapshot.original} - {None}:
            index.setdefault(key, []).append(snapshot)
    return index


def representation(source, index):
    # Restic accepts a destination entry by either its local ID or
    # its original ID. Only a source's own lineage is propagated by copy.
    return next((
        candidate for candidate in index.get(source.original or source.id, ())
        if candidate.attributes == source.attributes
        and set(source.paths) <= set(candidate.paths)
        and set(source.tags) <= set(candidate.tags)
    ), None)


@dataclass(frozen=True)
class Requirement:
    logical: str
    destination_key: str
    destination_generation: int

    @property
    def id(self):
        return digest((self.logical, self.destination_key, self.destination_generation))


@dataclass(frozen=True)
class Transfer:
    source: str
    destination: str
    snapshots: tuple[NativeSnapshot, ...]
    requirements: tuple[Requirement, ...]


def transfer(source, destination, registration, snapshots):
    snapshots = tuple(snapshots)
    requirements = tuple(dict.fromkeys(
        Requirement(logical_identity(snapshot), registration.key, registration.generation)
        for snapshot in snapshots
    ))
    return Transfer(source, destination, snapshots, requirements)


def plan_union(inventories, registrations, union, blocked, rejected, completed=frozenset()):
    """Plan one group from currently validated representations.

    The union only grows here. A vanished representation remains a requirement
    and is reported incomplete if there is no fresh viable source.
    """
    views = {}
    for name, items in inventories.items():
        view = {}
        for item in items:
            logical = logical_identity(item)
            union[logical] = item
            view.setdefault(logical, []).append(item)
        views[name] = view
    pending = []
    groups = {}
    for destination, inventory in inventories.items():
        present = representation_index(inventory)
        registered = registrations[destination]
        for logical in sorted(union):
            if representation(union[logical], present) is not None:
                continue
            requirement = Requirement(logical, registered.key, registered.generation)
            if requirement.id in blocked:
                continue
            pending.append(requirement)
            if requirement.id in completed:
                continue
            assigned = next((
                (name, snapshot)
                for name, view in views.items() if name != destination
                for snapshot in view.get(logical, ())
                if (name, requirement.id, snapshot.id) not in rejected
            ), None)
            if assigned is not None:
                source, snapshot = assigned
                groups.setdefault((source, destination), []).append((snapshot, requirement))
    if not groups:
        return None, tuple(pending)
    (source, destination), assigned = next(iter(groups.items()))
    snapshots, requirements = zip(*assigned)
    return Transfer(source, destination, snapshots, requirements), tuple(pending)


def verify_transfer(plan, source_inventory, destination_inventory):
    current = {snapshot.id: snapshot for snapshot in source_inventory}
    represented = representation_index(destination_inventory)
    return tuple(
        (snapshot.id, destination.id)
        for snapshot in plan.snapshots
        if current.get(snapshot.id) == snapshot
        and (destination := representation(snapshot, represented)) is not None
    )
