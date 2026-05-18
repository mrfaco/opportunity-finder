"""Cluster substrate.

Clusters group items that describe the same underlying user need. The online
stage assigns at ingestion time using nearest-centroid; the nightly refinement
recomputes centroids, reassigns orphans, and queues merge/split proposals for
human review through admin.

Embeddings are 1024-dim. The actual embedding model is a TODO — see
``clusters.clustering``.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from pgvector.django import HnswIndex, VectorField

from core.models import TimestampedModel

EMBEDDING_DIM = 1024


class ClusterStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    INVESTIGATING = "investigating", "Investigating"
    INVESTIGATED = "investigated", "Investigated"
    DISCARDED = "discarded", "Discarded"
    MERGED_INTO = "merged_into", "Merged into"
    SPLIT = "split", "Split"


class Source(models.TextChoices):
    HACKER_NEWS = "hacker_news", "Hacker News"
    GITHUB = "github", "GitHub"
    STACK_OVERFLOW = "stack_overflow", "Stack Overflow"
    PRODUCT_HUNT = "product_hunt", "Product Hunt"
    REDDIT = "reddit", "Reddit"
    OTHER = "other", "Other"


class ClassifierVerdict(models.TextChoices):
    OPPORTUNITY = "opportunity", "Opportunity"
    NOT_OPPORTUNITY = "not_opportunity", "Not opportunity"


class ProposalStatus(models.TextChoices):
    PENDING_REVIEW = "pending_review", "Pending review"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    APPLIED = "applied", "Applied"
    SUPERSEDED = "superseded", "Superseded"


class ActiveClusterManager(models.Manager):
    """Default manager filters out clusters that are no longer live."""

    def get_queryset(self) -> models.QuerySet:
        return (
            super()
            .get_queryset()
            .exclude(status__in=[ClusterStatus.MERGED_INTO, ClusterStatus.SPLIT])
        )


class Cluster(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    status = models.CharField(
        max_length=32, choices=ClusterStatus.choices, default=ClusterStatus.PENDING
    )
    size = models.PositiveIntegerField(default=0)

    first_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)

    sources = ArrayField(models.CharField(max_length=32), default=list, blank=True)

    centroid_embedding = VectorField(dimensions=EMBEDDING_DIM, null=True, blank=True)

    title = models.TextField(null=True, blank=True)
    summary = models.TextField(null=True, blank=True)

    category_tags = ArrayField(models.CharField(max_length=64), default=list, blank=True)

    classifier_score = models.FloatField(default=0.0)

    investigation_count = models.PositiveIntegerField(default=0)
    last_investigated_at = models.DateTimeField(null=True, blank=True)
    last_refined_at = models.DateTimeField(null=True, blank=True)

    merged_into_cluster = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        related_name="absorbed_clusters",
        on_delete=models.SET_NULL,
    )
    split_from_cluster = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        related_name="split_children",
        on_delete=models.SET_NULL,
    )
    merge_history = models.JSONField(default=list, blank=True)

    objects = models.Manager()
    active = ActiveClusterManager()

    class Meta:
        indexes = [
            HnswIndex(
                name="cluster_centroid_hnsw",
                fields=["centroid_embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
            models.Index(fields=["status", "last_seen_at"]),
        ]

    def __str__(self) -> str:
        return self.title or f"Cluster {self.id}"


class ClusterItem(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    cluster = models.ForeignKey(
        Cluster, related_name="items", on_delete=models.CASCADE, db_index=True
    )

    source = models.CharField(max_length=32, choices=Source.choices)
    source_item_id = models.CharField(max_length=256, db_index=True)
    url = models.URLField(max_length=1024)
    title = models.TextField(null=True, blank=True)
    author = models.CharField(max_length=256, null=True, blank=True)
    posted_at = models.DateTimeField()

    raw_text = models.TextField()
    snippet = models.CharField(max_length=200)

    classifier_verdict = models.CharField(max_length=32, choices=ClassifierVerdict.choices)
    classifier_confidence = models.FloatField()

    embedding = VectorField(dimensions=EMBEDDING_DIM)

    added_to_cluster_at = models.DateTimeField()
    assigned_at = models.DateTimeField()

    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            HnswIndex(
                name="cluster_item_emb_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
            models.Index(fields=["source", "source_item_id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "source_item_id"],
                name="cluster_item_source_unique",
            ),
        ]

    def __str__(self) -> str:
        return self.title or self.snippet[:80]


class ClusterMergeProposal(models.Model):
    """A pair of clusters the refinement step flagged as potential duplicates.

    The LLM judge populates the verdict; humans approve or reject in admin.
    Approving applies the merge: items move to the surviving cluster, centroid
    is recomputed, both clusters' statuses are updated, and ``merge_history``
    captures the event.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    cluster_a = models.ForeignKey(
        Cluster, related_name="merge_proposals_a", on_delete=models.CASCADE
    )
    cluster_b = models.ForeignKey(
        Cluster, related_name="merge_proposals_b", on_delete=models.CASCADE
    )

    centroid_similarity = models.FloatField()
    llm_judge_verdict = models.BooleanField(null=True, blank=True)
    llm_judge_confidence = models.FloatField(null=True, blank=True)
    llm_judge_reasoning = models.TextField(null=True, blank=True)

    status = models.CharField(
        max_length=32, choices=ProposalStatus.choices, default=ProposalStatus.PENDING_REVIEW
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="merge_proposals_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(null=True, blank=True)


class ClusterSplitProposal(models.Model):
    """A cluster the refinement step flagged as internally too diverse."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    cluster = models.ForeignKey(Cluster, related_name="split_proposals", on_delete=models.CASCADE)
    # {sub_cluster_id: [item_id, item_id, ...]}
    sub_cluster_assignments = models.JSONField(default=dict)
    internal_variance = models.FloatField()

    llm_judge_verdict = models.BooleanField(null=True, blank=True)
    llm_judge_confidence = models.FloatField(null=True, blank=True)
    llm_judge_reasoning = models.TextField(null=True, blank=True)

    status = models.CharField(
        max_length=32, choices=ProposalStatus.choices, default=ProposalStatus.PENDING_REVIEW
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="split_proposals_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(null=True, blank=True)
