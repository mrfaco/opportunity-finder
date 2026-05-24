from __future__ import annotations

import math

from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Avg, Max, OuterRef, Subquery
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from agents.orchestrator import start_run
from clusters import clustering
from clusters.models import (
    Cluster,
    ClusterItem,
    ClusterMergeProposal,
    ClusterSplitProposal,
    ClusterStatus,
    ProposalStatus,
)

# Half-life (in days) of the recency factor. A cluster last seen 7 days ago
# weighs ~½ of one seen today; 14 days ago weighs ~¼.
_RECENCY_HALF_LIFE_DAYS = 7.0

# How many top-ranked rows to visually highlight as the recommended picks.
_HIGHLIGHT_TOP_N = 3


@admin.register(Cluster)
class ClusterAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "status",
        "size",
        "classifier_score",
        "first_seen_at",
        "last_seen_at",
        "last_investigated_at",
    )
    list_filter = ("status", "sources", "category_tags")
    search_fields = ("id", "title", "summary")
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "first_seen_at",
        "last_seen_at",
        "last_refined_at",
        "last_investigated_at",
        "investigation_count",
        "merge_history",
        "centroid_embedding",
    )

    # ------------------------------------------------------------------
    # Triage dashboard — ranks un-investigated clusters so a human can
    # decide which one is worth burning an investigation budget on.
    # ------------------------------------------------------------------
    def triage_view(self, request):
        show_all = request.GET.get("show") == "all"

        # Annotate aggregates the agent doesn't already cache on Cluster.
        # ``first_item_title`` falls back to the highest-confidence item's
        # title when the cluster has no human-assigned title yet (true for
        # most clusters until refinement runs).
        top_item_title = (
            ClusterItem.objects.filter(cluster=OuterRef("pk"))
            .order_by("-classifier_confidence")
            .values("title")[:1]
        )
        qs = Cluster.active.annotate(
            avg_conf=Avg("items__classifier_confidence"),
            max_conf=Max("items__classifier_confidence"),
            first_item_title=Subquery(top_item_title),
        )
        if not show_all:
            qs = qs.filter(
                status__in=[ClusterStatus.PENDING, ClusterStatus.INVESTIGATING],
                last_investigated_at__isnull=True,
            )

        now = timezone.now()
        rows = []
        for c in qs:
            size = c.size
            avg_conf = c.avg_conf or 0.0
            max_conf = c.max_conf or 0.0
            if c.last_seen_at:
                days_since = (now - c.last_seen_at).total_seconds() / 86400.0
            else:
                # No items yet: rank as infinitely stale so it sinks.
                days_since = math.inf
            if days_since == math.inf:
                recency = 0.0
            else:
                recency = math.exp(-days_since / _RECENCY_HALF_LIFE_DAYS)
            priority: float = math.log2(1 + size) * avg_conf * recency
            rows.append(
                {
                    "cluster": c,
                    "title": c.title or c.first_item_title or "<untitled cluster>",
                    "size": size,
                    "avg_conf": avg_conf,
                    "max_conf": max_conf,
                    "days_since": days_since if days_since != math.inf else None,
                    "last_seen_at": c.last_seen_at,
                    "status": c.status,
                    "priority": priority,
                }
            )
        rows.sort(key=lambda r: float(r["priority"]), reverse=True)  # type: ignore[arg-type]
        for i, row in enumerate(rows):
            row["highlight"] = i < _HIGHLIGHT_TOP_N

        context = {
            **self.admin_site.each_context(request),
            "title": "Cluster triage",
            "rows": rows,
            "show_all": show_all,
        }
        return render(request, "admin/clusters/triage.html", context)

    # ------------------------------------------------------------------
    # Investigate action — POST handler invoked by each row in the triage
    # table. Calls the live orchestrator, then redirects to the existing
    # trajectory viewer so the user watches progress on the agent run
    # they just launched.
    # ------------------------------------------------------------------
    def investigate_cluster_view(self, request, cluster_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        cluster = get_object_or_404(Cluster, pk=cluster_id)
        run_id = start_run(cluster.id, "investigation")
        messages.success(
            request,
            f"Investigation kicked off for cluster {cluster.id}. Run {run_id}.",
        )
        return HttpResponseRedirect(f"/admin/agents/run/{run_id}/trajectory/")


@admin.register(ClusterItem)
class ClusterItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "source",
        "title",
        "classifier_verdict",
        "classifier_confidence",
        "posted_at",
        "cluster",
    )
    list_filter = ("source", "classifier_verdict")
    search_fields = ("title", "raw_text", "source_item_id", "author")
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "added_to_cluster_at",
        "assigned_at",
        "embedding",
    )


@admin.register(ClusterMergeProposal)
class ClusterMergeProposalAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "cluster_a",
        "cluster_b",
        "centroid_similarity",
        "llm_judge_verdict",
        "status",
        "created_at",
    )
    list_filter = ("status", "llm_judge_verdict")
    actions = ["approve_and_apply", "reject"]
    readonly_fields = (
        "id",
        "created_at",
        "cluster_a",
        "cluster_b",
        "centroid_similarity",
        "llm_judge_verdict",
        "llm_judge_confidence",
        "llm_judge_reasoning",
    )

    @admin.action(description="Approve & apply selected merge proposals")
    def approve_and_apply(self, request, queryset):
        applied = 0
        for proposal in queryset.filter(status=ProposalStatus.PENDING_REVIEW):
            with transaction.atomic():
                survivor, absorbed = proposal.cluster_a, proposal.cluster_b
                absorbed.items.update(cluster=survivor, assigned_at=timezone.now())
                clustering.recompute_centroid(survivor)
                survivor.merge_history = [
                    *survivor.merge_history,
                    {
                        "absorbed_cluster_id": str(absorbed.id),
                        "applied_at": timezone.now().isoformat(),
                        "centroid_similarity": proposal.centroid_similarity,
                        "proposal_id": str(proposal.id),
                    },
                ]
                survivor.save(update_fields=["merge_history", "updated_at"])
                absorbed.status = ClusterStatus.MERGED_INTO
                absorbed.merged_into_cluster = survivor
                absorbed.save(update_fields=["status", "merged_into_cluster", "updated_at"])
                proposal.status = ProposalStatus.APPLIED
                proposal.reviewed_by = request.user
                proposal.reviewed_at = timezone.now()
                proposal.save(update_fields=["status", "reviewed_by", "reviewed_at"])
                applied += 1
        messages.success(request, f"Applied {applied} merge(s).")

    @admin.action(description="Reject selected merge proposals")
    def reject(self, request, queryset):
        count = queryset.filter(status=ProposalStatus.PENDING_REVIEW).update(
            status=ProposalStatus.REJECTED,
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )
        messages.success(request, f"Rejected {count} merge(s).")


@admin.register(ClusterSplitProposal)
class ClusterSplitProposalAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "cluster",
        "internal_variance",
        "llm_judge_verdict",
        "status",
        "created_at",
    )
    list_filter = ("status", "llm_judge_verdict")
    actions = ["approve_and_apply", "reject"]
    readonly_fields = (
        "id",
        "created_at",
        "cluster",
        "sub_cluster_assignments",
        "internal_variance",
        "llm_judge_verdict",
        "llm_judge_confidence",
        "llm_judge_reasoning",
    )

    @admin.action(description="Approve & apply selected split proposals")
    def approve_and_apply(self, request, queryset):
        # TODO(v1-followup): implement once sub_cluster_assignments is populated
        # by HDBSCAN. For now we only mark approved so the audit trail exists.
        count = queryset.filter(status=ProposalStatus.PENDING_REVIEW).update(
            status=ProposalStatus.APPROVED,
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )
        messages.warning(
            request,
            f"Marked {count} split(s) as approved. Actual application is "
            "blocked until HDBSCAN sub-cluster assignment lands "
            "(TODO v1-followup).",
        )

    @admin.action(description="Reject selected split proposals")
    def reject(self, request, queryset):
        count = queryset.filter(status=ProposalStatus.PENDING_REVIEW).update(
            status=ProposalStatus.REJECTED,
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )
        messages.success(request, f"Rejected {count} split(s).")
