"""Internal scoped worker. It waits for its supervisor before executing a command."""

from contextlib import contextmanager
from dataclasses import asdict
import base64
import json
import os
import selectors
import subprocess
import sys

from .process_identity import ProcessIdentity

REQUEST_LIMIT = 4096
OUTPUT_LIMIT = 65536


def reply(data):
    print(json.dumps(data, separators=(",", ":")), flush=True)


def capture(process):
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = False
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            for key, _ in selector.select():
                chunk = os.read(key.fd, 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output = outputs[key.data]
                remaining = OUTPUT_LIMIT - len(output)
                output.extend(chunk[:remaining])
                truncated |= len(chunk) > remaining
    return outputs, truncated


@contextmanager
def child_input(encoded):
    if encoded is None:
        yield subprocess.DEVNULL
        return
    payload = base64.b64decode(encoded, validate=True)
    descriptor = os.memfd_create("backup-manager-worker-input", os.MFD_CLOEXEC)
    with os.fdopen(descriptor, "w+b") as stream:
        stream.write(payload)
        stream.seek(0)
        yield stream


def main():
    reply({"worker": asdict(ProcessIdentity.capture(os.getpid()))})
    request = sys.stdin.buffer.readline(REQUEST_LIMIT + 1)
    if not request.endswith(b"\n") or len(request) > REQUEST_LIMIT:
        return 1
    try:
        data = json.loads(request)
        with (
            child_input(data.get("stdin")) as command_input,
            subprocess.Popen(
                data["argv"], cwd=data["cwd"], stdin=command_input,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            ) as command,
        ):
            reply({"process": asdict(ProcessIdentity.capture(command.pid))})
            outputs, truncated = capture(command)
            reply({
                "exit_code": command.wait(),
                **{name: base64.b64encode(value).decode("ascii") for name, value in outputs.items()},
                "truncated": truncated,
            })
            # Keep the scope present while the supervisor records/reads results.
            sys.stdin.buffer.read(1)
        return 0
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
