"""Normative shape contracts; implementations live in backup_manager, not here.
No external I/O is permitted during module import. Python 3.12+.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Literal

API_VERSION = '1.0'
class StorageState(str, Enum):
    AVAILABLE='available'; UNAVAILABLE='unavailable'; UNCERTAIN='uncertain'

@dataclass(frozen=True)
class ExecutionContext:
    operation_id: str
    deadline_monotonic_ns: int
    cancellation_token: str
    correlation_id: str

@dataclass(frozen=True)
class StorageIdentity:
    coordination_id: str
    physical_identity: str | None
    evidence: Mapping[str, str]
    verified_physical: bool

@dataclass(frozen=True)
class StorageInspection:
    state: StorageState
    mounted: bool
    mountpoint: str | None
    identity: StorageIdentity
    externally_owned: bool | None
    details: Mapping[str, str] = field(default_factory=dict)

@dataclass(frozen=True)
class LifecycleResult:
    inspection: StorageInspection
    changed_physical_state: bool
    ownership_evidence: Mapping[str, str]

class CommandExecutor(Protocol):
    async def run(self, argv: tuple[str,...], *, context: ExecutionContext,
                  env: Mapping[str,str] | None = None,
                  cwd: str | None = None) -> 'CommandResult': ...

@dataclass(frozen=True)
class CommandResult:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    termination_confirmed: bool

class StorageProvider(Protocol):
    provider_id: str
    api_version: str
    config_schema: Mapping[str, Any]
    capabilities: frozenset[str]
    async def inspect(self, config: Mapping[str,Any], context: ExecutionContext) -> StorageInspection: ...
    async def resolve_identity(self, config: Mapping[str,Any], context: ExecutionContext) -> StorageIdentity: ...

class AcquirableStorage(StorageProvider, Protocol):
    async def acquire(self, config: Mapping[str,Any], context: ExecutionContext,
                      commands: CommandExecutor) -> LifecycleResult: ...

class ReleasableStorage(StorageProvider, Protocol):
    async def release(self, config: Mapping[str,Any], context: ExecutionContext,
                      commands: CommandExecutor, ownership_evidence: Mapping[str,str]) -> LifecycleResult: ...

@dataclass(frozen=True)
class SecretRef:
    provider: str
    config: Mapping[str,Any]

class SecretProvider(Protocol):
    provider_id: str
    api_version: str
    config_schema: Mapping[str, Any]
    async def resolve(self, reference: SecretRef, context: ExecutionContext) -> bytes: ...

# Resolved secret bytes are transient and must not be persisted or logged.
# Workers are trusted code; supervisor is responsible for lifecycle/termination.
# No plugin may acquire coordinator claims or write coordinator tables directly.
