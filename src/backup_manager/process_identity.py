"""Linux process identity observations for recovery and supervision."""

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path


class Presence(Enum):
    SAME = "same"
    GONE = "gone"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProcessIdentity:
    boot_id: str
    pid: int
    start_ticks: int
    pid_namespace: str

    @classmethod
    def capture(cls, pid: int) -> "ProcessIdentity":
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        record = Path(f"/proc/{pid}/stat").read_text()
        # The comm field is parenthesized and can itself contain parentheses.
        return cls(boot_id, pid, int(record.rsplit(")", 1)[1].split()[19]), os.readlink("/proc/self/ns/pid"))

    def presence(self) -> Presence:
        """Observe this process only. A gone owner can still have live children."""
        try:
            current = self.capture(self.pid)
        except FileNotFoundError as error:
            if error.filename != f"/proc/{self.pid}/stat":
                return Presence.UNKNOWN
            try:
                if os.readlink("/proc/self/ns/pid") != self.pid_namespace:
                    return Presence.UNKNOWN
                descriptor = os.pidfd_open(self.pid)
            except ProcessLookupError:
                return Presence.GONE
            except OSError:
                return Presence.UNKNOWN
            os.close(descriptor)
            return Presence.UNKNOWN
        except (OSError, ValueError, IndexError):
            return Presence.UNKNOWN
        if current.pid_namespace != self.pid_namespace:
            return Presence.UNKNOWN
        return Presence.SAME if current == self else Presence.GONE
