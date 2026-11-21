"""Public status and redaction-safe diagnostics."""

from dataclasses import asdict, dataclass, field
from enum import Enum


class Status(Enum):
    FULL = ("full", 0)
    PARTIAL = ("partial", 2)
    FAILED = ("failed", 3)
    INVALID = ("invalid", 4)
    BUSY = ("busy", 5)
    CANCELLED = ("cancelled", 6)
    UNSUPPORTED = ("unsupported", 7)
    UNSAFE = ("unsafe", 8)
    ERROR = ("error", 9)


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    context: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Result:
    status: Status = Status.FULL
    data: dict = field(default_factory=dict)
    errors: tuple[Diagnostic, ...] = ()
    operation_id: str | None = None

    def payload(self, command: str) -> dict:
        return {
            "schema_version": 1,
            "command": command,
            "status": self.status.value[0],
            "operation_id": self.operation_id,
            "data": self.data,
            "errors": [asdict(error) for error in self.errors],
        }


class InvalidInvocation(Exception):
    """The parser rejected command syntax."""
