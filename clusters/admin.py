from __future__ import annotations

from django.contrib import admin, messages
from django.db import transaction
from django.utils import timezone

from clusters import clustering
from clusters.models import (
    Cluster,
    ClusterItem,
    ClusterMergeProposal,
    ClusterSplitProposal,
    ClusterStatus,
    ProposalStatus,
)


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
