"""Celery tasks for cluster maintenance."""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from clusters import clustering
from clusters.judges import judge_merge
from clusters.models import Cluster, ClusterMergeProposal, ClusterSplitProposal, ProposalStatus
from clusters.summarizer import generate_title_and_summary

logger = logging.getLogger(__name__)

# Minimum size for a cluster to be eligible for LLM-driven title generation.
# Singletons get their title set verbatim from the underlying item at cluster
# creation (see ``clusters.clustering.assign_item_to_cluster``); paying for a
# Haiku call to restate a single item's own title is wasteful.
_TITLE_MIN_SIZE = 2

# A cluster's title is regenerated only when its size drifts by at least this
# fraction since the last titling pass. Avoids re-billing for trivial growth
# (e.g. 10 → 11) but catches the meaningful shifts (e.g. 2 → 5).
_TITLE_REGEN_RATIO = 0.20


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
        "titles_regenerated": 0,
        "titles_skipped_too_small": 0,
        "titles_skipped_size_unchanged": 0,
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

    # 3. Merge proposals. For each candidate pair above the similarity
    # threshold, run the Haiku judge and persist its verdict. The judge
    # is advisory — proposals still require a human approve/apply in
    # admin — but the structured opinion lets the operator prioritize.
    # Humans see the obvious-yes proposals at the top of the queue.
    for a, b, sim in clustering.find_merge_candidates():
        if ClusterMergeProposal.objects.filter(
            cluster_a__in=[a, b],
            cluster_b__in=[a, b],
            status=ProposalStatus.PENDING_REVIEW,
        ).exists():
            continue
        verdict = judge_merge(cluster_a=a, cluster_b=b, centroid_similarity=sim)
        ClusterMergeProposal.objects.create(
            cluster_a=a,
            cluster_b=b,
            centroid_similarity=sim,
            llm_judge_verdict=verdict.verdict,
            llm_judge_confidence=verdict.confidence,
            llm_judge_reasoning=verdict.reasoning,
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

    # 5. Title/summary regeneration. Single Haiku call per eligible cluster,
    # gated on (a) multi-item membership and (b) material size drift since
    # the last titling. The size check holds nightly cost roughly constant
    # as the cluster set grows: clusters that haven't moved don't get re-
    # billed, but the moment one grows by >=20% the title catches up.
    for cluster in Cluster.active.all():
        if cluster.size < _TITLE_MIN_SIZE:
            stats["titles_skipped_too_small"] += 1
            continue
        if not _title_needs_refresh(cluster):
            stats["titles_skipped_size_unchanged"] += 1
            continue
        result = generate_title_and_summary(cluster)
        cluster.title = result.title
        cluster.summary = result.summary
        cluster.last_titled_size = cluster.size
        cluster.save(update_fields=["title", "summary", "last_titled_size", "updated_at"])
        stats["titles_regenerated"] += 1

    logger.info("refine_clusters_nightly complete: %s", stats)
    return stats


def _title_needs_refresh(cluster: Cluster) -> bool:
    """True if the cluster's title is missing or its size drift exceeds the threshold.

    ``last_titled_size`` is ``None`` for clusters that have never been titled
    via the LLM path — those always need a refresh. For ones that have, we
    measure drift relative to the larger of the two sizes to make growth
    and shrinkage symmetric (otherwise shrinking by 50% looks bigger than
    growing by 50%).
    """
    if cluster.last_titled_size is None or not cluster.title:
        return True
    larger = max(cluster.size, cluster.last_titled_size)
    if larger == 0:
        return False
    drift = abs(cluster.size - cluster.last_titled_size) / larger
    return drift >= _TITLE_REGEN_RATIO
