"""Internal scoped worker. It waits for its supervisor before executing a command."""

from contextlib import ExitStack, contextmanager
from dataclasses import asdict
import base64
import json
import grp
from pathlib import Path
import pwd
import os
import selectors
import subprocess
import sys

from .process_identity import ProcessIdentity

REQUEST_LIMIT = 1024 * 1024
OUTPUT_LIMIT = 8 * 1024 * 1024


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


def directory_identity(descriptor):
    metadata = os.fstat(descriptor)
    info = Path(f"/proc/self/fdinfo/{descriptor}").read_text().splitlines()
    mount_id = next(line.split()[1] for line in info if line.startswith("mnt_id:"))
    return {"device": metadata.st_dev, "inode": metadata.st_ino, "mount_id": mount_id}


@contextmanager
def repository_argv(argv, repository, flag="--repo"):
    if repository is None:
        yield argv, ()
        return
    index = argv.index(flag) + 1
    if argv[index] != repository["path"]:
        raise ValueError("Repository argument differs from the verified directory.")
    descriptor = os.open(repository["path"], os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if directory_identity(descriptor) != repository["identity"]:
            raise ValueError("Verified repository directory changed before launch.")
        command = list(argv)
        command[index] = f"/proc/self/fd/{descriptor}"
        yield command, (descriptor,)
    finally:
        os.close(descriptor)


def credentials(user, group):
    kwargs = {}
    if user is not None:
        account = pwd.getpwuid(user) if isinstance(user, int) else pwd.getpwnam(user)
        kwargs["user"] = account.pw_uid
        kwargs["group"] = account.pw_gid
    if group is not None:
        kwargs["group"] = group if isinstance(group, int) else grp.getgrnam(group).gr_gid
    if kwargs.get("user", os.geteuid()) == os.geteuid() and kwargs.get("group", os.getegid()) == os.getegid():
        return {}
    if kwargs:
        kwargs["extra_groups"] = []
    return kwargs


def main():
    reply({"worker": asdict(ProcessIdentity.capture(os.getpid()))})
    request = sys.stdin.buffer.readline(REQUEST_LIMIT + 1)
    if not request.endswith(b"\n") or len(request) > REQUEST_LIMIT:
        return 1
    try:
        data = json.loads(request)
        with child_input(data.get("stdin")) as command_input:
            try:
                with ExitStack() as stack:
                    argv, descriptors = stack.enter_context(repository_argv(data["argv"], data.get("repository")))
                    argv, source = stack.enter_context(repository_argv(argv, data.get("source_repository"), "--from-repo"))
                    command = subprocess.Popen(
                        argv, cwd=data["cwd"], env=data.get("env"), stdin=command_input,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                        pass_fds=(*descriptors, *source), **credentials(data.get("user"), data.get("group")),
                    )
            except (OSError, ValueError, TypeError, KeyError, IndexError, StopIteration):
                reply({"not_started": True})
                sys.stdin.buffer.read(1)
                return 0
            with command:
                reply({"process": asdict(ProcessIdentity.capture(command.pid))})
                outputs, truncated = capture(command)
                reply({
                    "exit_code": command.wait(),
                    **{name: base64.b64encode(value).decode("ascii") for name, value in outputs.items()},
                    "truncated": truncated,
                })
                # Keep the scope present while the supervisor records results.
                sys.stdin.buffer.read(1)
        return 0
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
