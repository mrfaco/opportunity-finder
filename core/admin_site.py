"""Custom admin site that hosts the operational dashboards.

Django's ``ModelAdmin.get_urls`` attaches custom URLs *under* the model's
prefix (``/admin/<app>/<model>/...``). We want a handful of pages to live
directly under their app namespace (``/admin/agents/prompts/`` etc.), so we
hang them off the admin site itself.
"""

from __future__ import annotations

from django.urls import path
from unfold.sites import UnfoldAdminSite


class PainMinerAdminSite(UnfoldAdminSite):
    # site_header / site_title come from settings.UNFOLD
    index_title = "Operational dashboards"

    def index(self, request, extra_context=None):
        """Land on the cluster triage queue instead of the model directory.

        The triage table is what the operator actually wants to see when
        they open the admin — a ranked list of clusters worth investigating.
        The full app/model directory is still reachable from the sidebar
        on every admin page.
        """
        from clusters.admin import ClusterAdmin  # noqa: PLC0415
        from clusters.models import Cluster  # noqa: PLC0415

        return ClusterAdmin(Cluster, self).triage_view(request)

    def get_urls(self):
        # Deferred by necessity: this module is imported to *construct* the
        # admin site, while agents/admin.py and ingestion/admin.py register
        # against it. Importing them at module level would be circular.
        from agents.admin import AgentRunAdmin  # noqa: PLC0415
        from agents.models import AgentRun  # noqa: PLC0415
        from clusters.admin import ClusterAdmin  # noqa: PLC0415
        from clusters.models import Cluster  # noqa: PLC0415
        from ideation.admin import IdeationAdmin  # noqa: PLC0415
        from ideation.models import Ideation  # noqa: PLC0415
        from ingestion.admin import FilterEvalSetAdmin, IngestionCheckpointAdmin  # noqa: PLC0415
        from ingestion.models import FilterEvalSet, IngestionCheckpoint  # noqa: PLC0415
        from investigations.admin import InvestigationAdmin  # noqa: PLC0415
        from investigations.models import Investigation  # noqa: PLC0415

        # Reuse the views already defined on the ModelAdmins so we don't
        # duplicate logic between the model namespace and the app namespace.
        agents_admin = AgentRunAdmin(AgentRun, self)
        clusters_admin = ClusterAdmin(Cluster, self)
        ideation_admin = IdeationAdmin(Ideation, self)
        ingestion_admin = FilterEvalSetAdmin(FilterEvalSet, self)
        ingestion_ops_admin = IngestionCheckpointAdmin(IngestionCheckpoint, self)
        investigations_admin = InvestigationAdmin(Investigation, self)

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
                "clusters/triage/",
                self.admin_view(clusters_admin.triage_view),
                name="clusters-triage",
            ),
            path(
                "clusters/cluster/<uuid:cluster_id>/investigate/",
                self.admin_view(clusters_admin.investigate_cluster_view),
                name="clusters-investigate",
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
            path(
                "ingestion/operations/",
                self.admin_view(ingestion_ops_admin.operations_view),
                name="ingestion-operations",
            ),
            path(
                "ingestion/operations/<str:source>/ingest/",
                self.admin_view(ingestion_ops_admin.trigger_ingest_view),
                name="ingestion-trigger-ingest",
            ),
            path(
                "ingestion/operations/<str:source>/backfill/",
                self.admin_view(ingestion_ops_admin.trigger_backfill_view),
                name="ingestion-trigger-backfill",
            ),
            path(
                "investigations/latest/",
                self.admin_view(investigations_admin.latest_view),
                name="investigations-latest",
            ),
            path(
                "investigations/<uuid:investigation_id>/promote/",
                self.admin_view(investigations_admin.promote_from_latest_view),
                name="investigations-promote-from-latest",
            ),
            path(
                "ideation/latest/",
                self.admin_view(ideation_admin.latest_view),
                name="ideation-latest",
            ),
        ]
        return custom + super().get_urls()


site = PainMinerAdminSite(name="painminer_admin")
