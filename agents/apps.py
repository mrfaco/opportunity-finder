from django.apps import AppConfig


class AgentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "agents"

    def ready(self) -> None:
        # Register tool stubs on app start so the registry is populated
        # before any orchestrator call. Deferred by design — Django's
        # ready() hook is the correct place for import-for-side-effect.
        from agents.tools import stubs  # noqa: F401,PLC0415
