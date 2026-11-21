"""Discovery and verification of installed provider implementations."""

from importlib.metadata import entry_points

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .config import validator
from .linux_storage import LinuxFilesystem
from .secrets import EnvironmentSecret, FileSecret


class ProviderError(Exception):
    """An installed provider cannot be used."""


def discover(kind, builtins=()):
    providers = {}
    if kind == "secrets":
        builtins = (FileSecret, EnvironmentSecret, *builtins)
    elif kind == "storage":
        builtins = (LinuxFilesystem, *builtins)
    for factory in builtins:
        register(providers, kind, factory, None)
    for entry in entry_points(group=f"backup_manager.{kind}"):
        try:
            factory = entry.load()
        except Exception:
            raise ProviderError("Could not load an installed provider.") from None
        register(providers, kind, factory, entry.name)
    return providers


def register(providers, kind, factory, expected_name):
    required = {"resolve"} if kind == "secrets" else {"inspect", "resolve_identity"}
    allowed = required if kind == "secrets" else required | {"acquire", "release"}
    try:
        provider = factory()
        capabilities = list(getattr(provider, "capabilities", required))
        manifest = {
            "provider_id": provider.provider_id,
            "api_version": provider.api_version,
            "kind": kind,
            "capabilities": capabilities,
            "config_schema": provider.config_schema,
        }
        if not validator("plugin-manifest").is_valid(manifest):
            raise ProviderError("Provider manifest is invalid or incompatible.")
        Draft202012Validator.check_schema(provider.config_schema)
        implemented = {name for name in allowed if callable(getattr(provider, name, None))}
        if set(capabilities) != implemented or not required <= implemented:
            raise ProviderError("Provider capabilities do not match its implementation.")
        name = provider.provider_id
        if name in providers or (expected_name is not None and name != expected_name):
            raise ProviderError("Duplicate or mismatched provider identifier.")
        providers[name] = provider
    except ProviderError:
        raise
    except SchemaError:
        raise ProviderError("Provider configuration schema is invalid.") from None
    except Exception:
        raise ProviderError("Could not initialize an installed provider.") from None
