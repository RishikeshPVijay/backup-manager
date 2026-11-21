class ExampleReadOnlyStorage:
    provider_id = "example_readonly"
    api_version = "1.0.0"
    capabilities = ("inspect", "resolve_identity")

    def inspect(self, config, context):
        # Example only: adapt to core's typed Inspection model.
        return {"available": True, "access_path": config["path"]}

    def resolve_identity(self, config, context):
        # Explicit stable identity required because this example does not verify hardware.
        return {"coordination_id": config["coordination_id"], "verified": False}
