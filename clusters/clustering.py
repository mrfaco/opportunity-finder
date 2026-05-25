"""Online clustering + nightly refinement helpers.

The online stage is exact and deterministic: at ingestion, embed an item,
query the nearest active-cluster centroid within the recency window, and
either join (similarity >= JOIN_THRESHOLD) or open a new singleton.

The refinement stage is run nightly by a Celery beat task. It (a) recomputes
centroids from members (correcting drift from running-average updates),
(b) reassigns orphan items to better-fitting clusters, (c) queues merge
proposals for human review when two centroids are close and an LLM judge
agrees they describe the same need, and (d) queues split proposals when a
cluster is large and internally diverse.

All LLM-driven steps in this module are stubs in v1 — see TODOs.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Iterable

import numpy as np
from django.conf import settings
from django.utils import timezone
from pgvector.django import CosineDistance

from clusters.embeddings import embed_text
from clusters.models import Cluster, ClusterItem, ClusterStatus


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------
def compute_embedding(text: str) -> list[float]:
    """Embed text into a 1024-dim vector for clustering.

    The clustering-facing entry point. Delegates to ``clusters.embeddings``
    (Voyage AI) so callers in this module never touch the embedding provider
    directly.
    """
    return embed_text(text)


# ---------------------------------------------------------------------------
# Online stage
# ---------------------------------------------------------------------------
def _candidate_clusters():
    cutoff = timezone.now() - timedelta(days=settings.CLUSTER_RECENCY_DAYS)
    return Cluster.objects.filter(
        status__in=[
            ClusterStatus.PENDING,
            ClusterStatus.INVESTIGATING,
            ClusterStatus.INVESTIGATED,
        ],
        last_seen_at__gte=cutoff,
    ).exclude(centroid_embedding__isnull=True)


def _nearest_cluster(
    embedding: list[float],
) -> tuple[Cluster | None, float]:
    """Return the (cluster, cosine_similarity) of the nearest active centroid.

    pgvector exposes cosine *distance*; we convert to similarity = 1 - distance.
    """
    qs = (
        _candidate_clusters()
        .annotate(_dist=CosineDistance("centroid_embedding", embedding))
        .order_by("_dist")
    )
    nearest = qs.first()
    if nearest is None:
        return None, 0.0
    return nearest, 1.0 - float(nearest._dist)


def assign_item_to_cluster(item: ClusterItem) -> Cluster:
    """Online assignment: nearest centroid above threshold, else new singleton.

    Mutates ``item`` (sets ``cluster``, ``assigned_at``, ``added_to_cluster_at``)
    but does NOT save it. Callers persist after assignment.
    """
    threshold = settings.CLUSTER_JOIN_THRESHOLD
    nearest, similarity = _nearest_cluster(item.embedding)
    now = timezone.now()

    if nearest is not None and similarity >= threshold:
        new_size = nearest.size + 1
        old_centroid = np.array(nearest.centroid_embedding)
        new_centroid = (old_centroid * nearest.size + np.array(item.embedding)) / new_size
        nearest.centroid_embedding = new_centroid.tolist()
        nearest.size = new_size
        nearest.last_seen_at = max(nearest.last_seen_at or item.posted_at, item.posted_at)
        if item.source not in nearest.sources:
            nearest.sources = [*nearest.sources, item.source]
        nearest.save(
            update_fields=["centroid_embedding", "size", "last_seen_at", "sources", "updated_at"]
        )
        item.cluster = nearest
        item.added_to_cluster_at = now
        item.assigned_at = now
        return nearest

    # Seed the singleton's title from the item's own title. A 1-item cluster
    # has nothing to summarize that the item title doesn't already say, and
    # burning a Haiku call to restate it would be wasteful. When the cluster
    # grows past size=1, the nightly refinement task regenerates via the
    # cluster_summary prompt — see ``clusters/summarizer.py``.
    new_cluster = Cluster.objects.create(
        status=ClusterStatus.PENDING,
        size=1,
        first_seen_at=item.posted_at,
        last_seen_at=item.posted_at,
        sources=[item.source],
        centroid_embedding=item.embedding,
        classifier_score=item.classifier_confidence,
        title=item.title or None,
        last_titled_size=1,
    )
    item.cluster = new_cluster
    item.added_to_cluster_at = now
    item.assigned_at = now
    return new_cluster


# ---------------------------------------------------------------------------
# Refinement stage
# ---------------------------------------------------------------------------
def recompute_centroid(cluster: Cluster) -> None:
    """Recompute centroid from the cluster's current members.

    Corrects drift accumulated by the running-average updates used online.
    """
    embeddings = list(cluster.items.values_list("embedding", flat=True))
    if not embeddings:
        return
    arr = np.array([list(e) for e in embeddings])
    centroid = arr.mean(axis=0)
    centroid /= np.linalg.norm(centroid) + 1e-12
    cluster.centroid_embedding = centroid.tolist()
    cluster.size = len(embeddings)
    cluster.last_refined_at = timezone.now()
    cluster.save(update_fields=["centroid_embedding", "size", "last_refined_at", "updated_at"])


def find_orphan_items(cluster: Cluster) -> list[ClusterItem]:
    """Return items in ``cluster`` better-assigned elsewhere by more than the margin.

    For each item, compare its similarity to its current cluster's centroid
    against the best-matching other active cluster. If
    ``other_similarity > current_similarity + CLUSTER_REASSIGN_MARGIN``,
    the item is an orphan.
    """
    margin = settings.CLUSTER_REASSIGN_MARGIN
    orphans: list[ClusterItem] = []
    # ``centroid_embedding`` comes back from pgvector as a numpy array, and
    # ``if arr`` raises ValueError because numpy refuses to reduce a multi-
    # element array to a single bool. Compare against None explicitly.
    if cluster.centroid_embedding is None:
        return orphans
    current_centroid = np.array(cluster.centroid_embedding)
    for item in cluster.items.all():
        item_emb = np.array(item.embedding)
        current_sim = float(np.dot(current_centroid, item_emb))
        other, other_sim = _nearest_cluster(item.embedding)
        if other is None or other.id == cluster.id:
            continue
        if other_sim > current_sim + margin:
            orphans.append(item)
    return orphans


def find_merge_candidates() -> list[tuple[Cluster, Cluster, float]]:
    """Return pairs of active clusters above the merge threshold with overlapping tags.

    The actual LLM-judge confirmation step is downstream (see
    ``llm_judge_merge``); this function only narrows the candidate set.
    """
    threshold = settings.CLUSTER_MERGE_THRESHOLD
    candidates: list[tuple[Cluster, Cluster, float]] = []
    active = list(Cluster.active.exclude(centroid_embedding__isnull=True))
    for i, a in enumerate(active):
        a_emb = np.array(a.centroid_embedding)
        for b in active[i + 1 :]:
            if a.category_tags and b.category_tags:
                if not set(a.category_tags) & set(b.category_tags):
                    continue
            b_emb = np.array(b.centroid_embedding)
            sim = float(np.dot(a_emb, b_emb))
            if sim >= threshold:
                candidates.append((a, b, sim))
    return candidates


def _mean_pairwise_distance(embeddings: Iterable[list[float]]) -> float:
    arr = np.array([list(e) for e in embeddings])
    if len(arr) < 2:
        return 0.0
    # Mean pairwise cosine distance for normalized vectors.
    sims = arr @ arr.T
    n = len(arr)
    # Exclude the diagonal.
    total = sims.sum() - np.trace(sims)
    mean_sim = total / (n * (n - 1))
    return float(1.0 - mean_sim)


def find_split_candidates() -> list[Cluster]:
    """Return clusters with size + variance above the configured thresholds."""
    size_thr = settings.SPLIT_SIZE_THRESHOLD
    var_thr = settings.SPLIT_VARIANCE_THRESHOLD
    candidates: list[Cluster] = []
    for cluster in Cluster.active.filter(size__gte=size_thr):
        embeddings = list(cluster.items.values_list("embedding", flat=True))
        if _mean_pairwise_distance(embeddings) >= var_thr:
            candidates.append(cluster)
    return candidates


# ---------------------------------------------------------------------------
# LLM judges — stubs
# ---------------------------------------------------------------------------
def llm_judge_merge(cluster_a: Cluster, cluster_b: Cluster) -> tuple[bool, float, str]:
    """Ask a cheap-model LLM whether two clusters describe the same user need.

    Returns ``(verdict, confidence, reasoning)``.

    TODO(v1-followup): implement using Haiku with a binary
    "same underlying user need, yes/no" prompt. Inputs to the judge should
    include each cluster's summary + top 3 key items.
    """
    raise NotImplementedError(
        "TODO(v1-followup): wire up Anthropic Haiku judge for merge proposals"
    )


def llm_judge_split(
    cluster: Cluster,
    sub_clusters: list[list[ClusterItem]],
) -> tuple[bool, float, str]:
    """Ask a cheap-model LLM whether a proposed split is sensible.

    TODO(v1-followup): implement using Haiku. Input: each sub-cluster's
    representative items. The judge confirms whether they describe genuinely
    distinct needs vs. surface variation on a single need.
    """
    raise NotImplementedError(
        "TODO(v1-followup): wire up Anthropic Haiku judge for split proposals"
    )
