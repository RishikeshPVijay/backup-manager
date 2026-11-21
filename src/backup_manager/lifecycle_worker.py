"""Internal stdio protocol for a lifecycle worker under a core supervisor."""

import asyncio
from dataclasses import asdict
import json
import os
import sys
from time import monotonic_ns

from jsonschema import Draft202012Validator

from .plugin_api import CommandResult, ExecutionContext
from .providers import discover

OUTPUT_LIMIT = 64 * 1024


async def bounded_read(stream):
    output = bytearray()
    while chunk := await stream.read(8192):
        output.extend(chunk[:OUTPUT_LIMIT - len(output)])
    return bytes(output)


class WorkerCommands:
    """Children inherit the worker's containment and supervisor's deadline."""

    async def run(self, argv, *, context, env=None, cwd=None):
        remaining = (context.deadline_monotonic_ns - monotonic_ns()) / 1_000_000_000
        if remaining <= 0:
            return CommandResult(None, b"", b"", False)
        try:
            async with asyncio.timeout(remaining):
                process = await asyncio.create_subprocess_exec(
                    *argv, stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    env=env, cwd=cwd,
                )
                stdout, stderr = await asyncio.gather(
                    bounded_read(process.stdout), bounded_read(process.stderr),
                )
                return CommandResult(await process.wait(), stdout, stderr, True)
        except (OSError, TimeoutError):
            # The supervisor must reconcile the entire containment before
            # permitting another operation, even when the worker has exited.
            return CommandResult(None, b"", b"", False)


async def execute(request):
    provider = discover("storage")[request["provider"]]
    operation = request["operation"]
    if operation not in {"acquire", "release"} or operation not in provider.capabilities:
        raise ValueError("Unsupported worker operation.")
    config = request["config"]
    Draft202012Validator(provider.config_schema).validate(config)
    context = ExecutionContext(**request["context"])
    if monotonic_ns() >= context.deadline_monotonic_ns:
        raise TimeoutError
    commands = WorkerCommands()
    if operation == "acquire":
        result = await provider.acquire(config, context, commands)
    else:
        result = await provider.release(config, context, commands, request["ownership_evidence"])
    return asdict(result)


def main():
    # Only a supervisor may use this internal protocol. A reply is advisory.
    # In particular, worker exit does not establish containment termination.
    if os.getsid(0) != os.getpid():
        return 1
    try:
        raw = sys.stdin.buffer.read(OUTPUT_LIMIT + 1)
        if len(raw) > OUTPUT_LIMIT:
            return 1
        response = asyncio.run(execute(json.loads(raw)))
        encoded = json.dumps(response)
        if len(encoded.encode()) > OUTPUT_LIMIT:
            return 1
        print(encoded)
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
