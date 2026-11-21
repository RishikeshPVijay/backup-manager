"""Configured hook preparation and output redaction."""

import asyncio
import os

from .plugin_api import SecretRef
from .secrets import SecretUnavailable
from .storage_coordinator import context

HOOK_LOG_LIMIT = 65536


def resolve_secret(providers, reference, deadline):
    provider = providers.get(reference["provider"])
    if provider is None:
        raise SecretUnavailable("Secret provider is unavailable.")
    config = {key: value for key, value in reference.items() if key != "provider"}
    value = asyncio.run(provider.resolve(SecretRef(reference["provider"], config), context(deadline)))
    if not value or b"\0" in value:
        raise SecretUnavailable("Secret is unavailable or invalid.")
    return value


def repository_password(providers, reference, deadline):
    material = resolve_secret(providers, reference, deadline)
    if reference["provider"] == "file":
        # Restic treats file whitespace as framing. Environment passwords
        # and hook secret bytes retain their original value.
        whitespace = " \t\n\r\v\f\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
        material = os.fsencode(os.fsdecode(material).strip(whitespace))
    if not material:
        raise SecretUnavailable("Repository password is empty.")
    return material


def hook_environment(providers, hook, context_values, deadline):
    env = {"PATH": os.defpath, "LC_ALL": "C"}
    env.update(hook.get("env", {}))
    known = []
    for key, reference in hook.get("secrets", {}).items():
        material = resolve_secret(providers, reference, deadline)
        known.append(material)
        env[key] = os.fsdecode(material)
    env.update({f"BACKUP_MANAGER_{key}": value for key, value in context_values.items()})
    return env, known


def redact_output(result, values):
    known = sorted({value for value in values if value}, key=len, reverse=True)

    def clip(data):
        if result.truncated and known:
            # A capture boundary can split a credential before redaction.
            return "[OUTPUT OMITTED: CAPTURE TRUNCATED]" if data else "", False
        for secret in known:
            data = data.replace(secret, b"[REDACTED]")
        return data[:HOOK_LOG_LIMIT].decode(errors="replace"), len(data) > HOOK_LOG_LIMIT

    stdout, out_cut = clip(result.stdout)
    stderr, err_cut = clip(result.stderr)
    return {
        "exit_code": result.exit_code, "stop_reason": result.stop_reason,
        "stdout": stdout, "stderr": stderr, "truncated": result.truncated or out_cut or err_cut,
    }
