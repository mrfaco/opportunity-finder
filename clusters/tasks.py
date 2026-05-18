"""Celery tasks for cluster maintenance."""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from . import clustering
from .models import (
    Cluster,
    ClusterMergeProposal,
    ClusterSplitProposal,
    ProposalStatus,
)

logger = logging.getLogger(__name__)


@shared_task
def refine_clusters_nightly() -> dict[str, int]:
    """Nightly maintenance pass.

    1. Recompute centroids (corrects drift from running-average updates).
    2. Reassign orphan items.
    3. Queue merge proposals (LLM judge stubbed in v1).
    4. Queue split proposals (LLM judge stubbed in v1).
    5. Regenerate titles/summaries for clusters whose size changed materially
       (stubbed in v1 — requires a cheap-model summarizer).
    """
    stats = {
        "centroids_recomputed": 0,
        "orphans_reassigned": 0,
        "merge_proposals_queued": 0,
        "split_proposals_queued": 0,
        "titles_regenerated_skipped": 0,
    }

    # 1. Recompute centroids.
    for cluster in Cluster.active.all():
        clustering.recompute_centroid(cluster)
        stats["centroids_recomputed"] += 1

    # 2. Reassign orphans.
    for cluster in Cluster.active.all():
        for orphan in clustering.find_orphan_items(cluster):
            with transaction.atomic():
                old_cluster = orphan.cluster
                new_cluster, _ = clustering._nearest_cluster(orphan.embedding)
                if new_cluster is None or new_cluster.id == old_cluster.id:
                    continue
                orphan.cluster = new_cluster
                orphan.assigned_at = timezone.now()
                orphan.save(update_fields=["cluster", "assigned_at", "updated_at"])
                clustering.recompute_centroid(old_cluster)
                clustering.recompute_centroid(new_cluster)
                stats["orphans_reassigned"] += 1

    # 3. Merge proposals. We enqueue every candidate above the threshold and
    # leave ``llm_judge_*`` fields null until the judge is implemented (see
    # NEXT_STEPS.md step 9). Humans review proposals from admin in the
    # meantime — no fallback values, no swallowed NotImplementedError.
    for a, b, sim in clustering.find_merge_candidates():
        if ClusterMergeProposal.objects.filter(
            cluster_a__in=[a, b],
            cluster_b__in=[a, b],
            status=ProposalStatus.PENDING_REVIEW,
        ).exists():
            continue
        ClusterMergeProposal.objects.create(
            cluster_a=a,
            cluster_b=b,
            centroid_similarity=sim,
        )
        stats["merge_proposals_queued"] += 1

    # 4. Split proposals. Same shape — record the candidate; HDBSCAN
    # sub-clustering and the LLM judge are NEXT_STEPS items.
    for cluster in clustering.find_split_candidates():
        if ClusterSplitProposal.objects.filter(
            cluster=cluster, status=ProposalStatus.PENDING_REVIEW
        ).exists():
            continue
        ClusterSplitProposal.objects.create(
            cluster=cluster,
            sub_cluster_assignments={},
            internal_variance=clustering._mean_pairwise_distance(
                list(cluster.items.values_list("embedding", flat=True))
            ),
        )
        stats["split_proposals_queued"] += 1

    # 5. Title/summary regeneration is intentionally stubbed.
    # TODO(v1-followup): regenerate title+summary for clusters whose size
    # changed by >=20% since last_refined_at using a cheap-model call.

    logger.info("refine_clusters_nightly complete: %s", stats)
    return stats
