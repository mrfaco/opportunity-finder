"""Custom admin site that hosts the operational dashboards.

Django's ``ModelAdmin.get_urls`` attaches custom URLs *under* the model's
prefix (``/admin/<app>/<model>/...``). We want a handful of pages to live
directly under their app namespace (``/admin/agents/prompts/`` etc.), so we
hang them off the admin site itself.
"""

from __future__ import annotations

from django.contrib.admin import AdminSite
from django.urls import path


class PainMinerAdminSite(AdminSite):
    site_header = "Pain-Miner Admin"
    site_title = "Pain-Miner"
    index_title = "Operational dashboards"

    def get_urls(self):
        # Deferred by necessity: this module is imported to *construct* the
        # admin site, while agents/admin.py and ingestion/admin.py register
        # against it. Importing them at module level would be circular.
        from agents.admin import AgentRunAdmin  # noqa: PLC0415
        from agents.models import AgentRun  # noqa: PLC0415
        from ingestion.admin import FilterEvalSetAdmin  # noqa: PLC0415
        from ingestion.models import FilterEvalSet  # noqa: PLC0415

        # Reuse the views already defined on the ModelAdmins so we don't
        # duplicate logic between the model namespace and the app namespace.
        agents_admin = AgentRunAdmin(AgentRun, self)
        ingestion_admin = FilterEvalSetAdmin(FilterEvalSet, self)

        custom = [
            path(
                "agents/prompts/",
                self.admin_view(agents_admin.prompt_inspector_view),
                name="agents-prompt-inspector",
            ),
            path(
                "agents/cost-dashboard/",
                self.admin_view(agents_admin.cost_dashboard_view),
                name="agents-cost-dashboard",
            ),
            path(
                "agents/run/<uuid:run_id>/trajectory/",
                self.admin_view(agents_admin.trajectory_view),
                name="agents-trajectory",
            ),
            path(
                "ingestion/filter-labeling/",
                self.admin_view(ingestion_admin.labeling_view),
                name="ingestion-filter-labeling",
            ),
            path(
                "ingestion/filter-eval-history/",
                self.admin_view(ingestion_admin.eval_history_view),
                name="ingestion-filter-eval-history",
            ),
        ]
        return custom + super().get_urls()


site = PainMinerAdminSite(name="painminer_admin")
