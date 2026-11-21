"""Built-in secret providers."""

import os
import stat

from .plugin_api import API_VERSION


class SecretUnavailable(Exception):
    """Secret resolution failed without exposing the reference or value."""


class FileSecret:
    provider_id = "file"
    api_version = API_VERSION
    config_schema = {
        "type": "object", "additionalProperties": False, "required": ["path"],
        "properties": {"path": {"type": "string", "pattern": "^/"}},
    }

    async def resolve(self, reference, context):
        fd = None
        try:
            fd = os.open(
                reference.config["path"],
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            )
            details = os.fstat(fd)
            if (
                not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid()
                or details.st_mode & 0o077
            ):
                raise SecretUnavailable("Unsafe secret file.")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                value = stream.read(65537)
            if not value or len(value) > 65536:
                raise SecretUnavailable("Invalid secret file size.")
            return value
        except (OSError, ValueError, KeyError):
            raise SecretUnavailable("Could not read secret file.") from None
        finally:
            if fd is not None:
                os.close(fd)


class EnvironmentSecret:
    provider_id = "environment"
    api_version = API_VERSION
    config_schema = {
        "type": "object", "additionalProperties": False, "required": ["variable"],
        "properties": {"variable": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]*$"}},
    }

    async def resolve(self, reference, context):
        value = os.environb.get(os.fsencode(reference.config["variable"]))
        if not value:
            raise SecretUnavailable("Secret environment variable is unavailable.")
        return value
