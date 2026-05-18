from django.apps import AppConfig


class AgentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "agents"

    def ready(self) -> None:
        # Register tool stubs on app start so the registry is populated
        # before any orchestrator call.
        from .tools import stubs  # noqa: F401
