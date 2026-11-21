"""Disposable provider used only by integration fixtures."""

import asyncio
from pathlib import Path

from backup_manager.plugin_api import LifecycleResult, StorageIdentity, StorageInspection, StorageState


class FixtureProvider:
    provider_id = "fixture_storage"
    api_version = "1.0.0"
    capabilities = ("inspect", "resolve_identity", "acquire", "release")
    config_schema = {
        "type": "object", "additionalProperties": False, "required": ["root"],
        "properties": {"root": {"type": "string"}},
    }

    async def inspect(self, config, context):
        root = Path(config["root"])
        mounted = (root / "mounted").exists()
        identity = StorageIdentity("fixture-disk", "fixture-disk", {"mount": "fixture"} if mounted else {}, True)
        return StorageInspection(
            StorageState.AVAILABLE if mounted else StorageState.UNAVAILABLE,
            mounted, str(root), identity, None,
        )

    async def resolve_identity(self, config, context):
        return (await self.inspect(config, context)).identity

    async def acquire(self, config, context, commands):
        root = Path(config["root"])
        with (root / "actions").open("a") as stream:
            stream.write("acquire\n")
        await asyncio.sleep(1)
        (root / "mounted").touch(exist_ok=False)
        observed = await self.inspect(config, context)
        return LifecycleResult(observed, True, observed.identity.evidence)

    async def release(self, config, context, commands, ownership_evidence):
        root = Path(config["root"])
        observed = await self.inspect(config, context)
        assert observed.mounted and observed.identity.evidence == ownership_evidence
        with (root / "actions").open("a") as stream:
            stream.write("release\n")
        (root / "mounted").unlink()
        return LifecycleResult(await self.inspect(config, context), True, {})
