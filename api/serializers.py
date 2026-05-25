"""DRF serializers for the public API.

Read serializers expose the existing model fields verbatim (preserving
``schema_version`` strings on JSON payloads). Write serializers validate
inputs for the small number of action endpoints — only the fields the
caller actually supplies.
"""

from __future__ import annotations

from rest_framework import serializers

from clusters.models import Cluster, ClusterMergeProposal, ProposalStatus
from ideation.models import Ideation
from ingestion import tasks as ingestion_tasks
from ingestion.models import IngestionCheckpoint
from investigations.models import Investigation, InvestigationStatus, StaleReason

# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


class IngestRunRequestSerializer(serializers.Serializer):
    source = serializers.ChoiceField(choices=sorted(ingestion_tasks.ADAPTERS.keys()))


class BackfillRequestSerializer(serializers.Serializer):
    source = serializers.ChoiceField(choices=sorted(ingestion_tasks.ADAPTERS.keys()))
    # Positive int — keep loud-fails per AGENTS §1; no default, no silent clamp.
    days = serializers.IntegerField(min_value=1, max_value=365)


class TaskQueuedResponseSerializer(serializers.Serializer):
    task_id = serializers.CharField()
    status = serializers.CharField(default="queued")


class IngestionCheckpointSerializer(serializers.ModelSerializer):
    class Meta:
        model = IngestionCheckpoint
        fields = (
            "source",
            "last_item_posted_at",
            "last_run_at",
            "items_seen",
            "opportunities_found",
        )


class TaskRunSerializer(serializers.Serializer):
    """Read-only shape over ``django_celery_results.TaskResult`` rows.

    We don't serialize the model directly because we only want a subset of
    its fields and the ``result`` column is large.
    """

    task_id = serializers.CharField()
    task_name = serializers.CharField()
    status = serializers.CharField()
    date_created = serializers.DateTimeField()
    date_done = serializers.DateTimeField(allow_null=True)
    task_args = serializers.CharField(allow_blank=True)
    task_kwargs = serializers.CharField(allow_blank=True)
    traceback = serializers.CharField(allow_blank=True, allow_null=True)


class IngestedItemSerializer(serializers.Serializer):
    """Read-only shape over ``ClusterItem`` for the ingestion-items endpoint.

    Defined as a plain Serializer (not ModelSerializer) so we can flatten the
    cluster id alongside the item without nesting a cluster object.
    """

    id = serializers.UUIDField()
    cluster_id = serializers.UUIDField()
    source = serializers.CharField()
    source_item_id = serializers.CharField()
    title = serializers.CharField(allow_null=True, allow_blank=True)
    url = serializers.CharField()
    posted_at = serializers.DateTimeField()
    assigned_at = serializers.DateTimeField()
    snippet = serializers.CharField()
    classifier_verdict = serializers.CharField()
    classifier_confidence = serializers.FloatField()


# ---------------------------------------------------------------------------
# Clusters
# ---------------------------------------------------------------------------


class ClusterSerializer(serializers.ModelSerializer):
    """Trimmed cluster shape — no centroid embedding (1024 floats is noise)."""

    class Meta:
        model = Cluster
        fields = (
            "id",
            "status",
            "size",
            "title",
            "summary",
            "sources",
            "category_tags",
            "first_seen_at",
            "last_seen_at",
            "last_investigated_at",
            "investigation_count",
            "classifier_score",
        )


# ---------------------------------------------------------------------------
# Cluster merge proposals
# ---------------------------------------------------------------------------


class ClusterMergeProposalListSerializer(serializers.ModelSerializer):
    """Compact list-view of a merge proposal — judge verdict surfaced.

    Includes the two clusters' titles so the operator can scan without
    drilling into each cluster.
    """

    cluster_a_id = serializers.UUIDField(read_only=True)
    cluster_b_id = serializers.UUIDField(read_only=True)
    cluster_a_title = serializers.SerializerMethodField()
    cluster_b_title = serializers.SerializerMethodField()

    class Meta:
        model = ClusterMergeProposal
        fields = (
            "id",
            "status",
            "created_at",
            "cluster_a_id",
            "cluster_b_id",
            "cluster_a_title",
            "cluster_b_title",
            "centroid_similarity",
            "llm_judge_verdict",
            "llm_judge_confidence",
            "reviewed_at",
        )

    def get_cluster_a_title(self, obj: ClusterMergeProposal) -> str | None:
        return obj.cluster_a.title

    def get_cluster_b_title(self, obj: ClusterMergeProposal) -> str | None:
        return obj.cluster_b.title


class ClusterMergeProposalDetailSerializer(ClusterMergeProposalListSerializer):
    """Detail view — adds judge reasoning, summaries, and review notes."""

    cluster_a_summary = serializers.SerializerMethodField()
    cluster_b_summary = serializers.SerializerMethodField()

    class Meta:
        model = ClusterMergeProposal
        fields = ClusterMergeProposalListSerializer.Meta.fields + (
            "cluster_a_summary",
            "cluster_b_summary",
            "llm_judge_reasoning",
            "review_notes",
        )

    def get_cluster_a_summary(self, obj: ClusterMergeProposal) -> str | None:
        return obj.cluster_a.summary

    def get_cluster_b_summary(self, obj: ClusterMergeProposal) -> str | None:
        return obj.cluster_b.summary


class MergeProposalRejectRequestSerializer(serializers.Serializer):
    review_notes = serializers.CharField(required=False, allow_blank=True, default="")


class MergeProposalStatusFilterSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=ProposalStatus.choices, required=False)


# ---------------------------------------------------------------------------
# Investigations
# ---------------------------------------------------------------------------


class InvestigationStartRequestSerializer(serializers.Serializer):
    cluster_id = serializers.UUIDField()


class InvestigationRunQueuedSerializer(serializers.Serializer):
    """Response shape for POST /investigations/runs/.

    The investigation row materializes only after the loop finishes, so we
    return the AgentRun id (what the worker is currently grinding on) and
    the cluster id (caller's index into history).
    """

    run_id = serializers.UUIDField()
    cluster_id = serializers.UUIDField()
    status = serializers.CharField(default="queued")


class InvestigationListSerializer(serializers.ModelSerializer):
    cluster_id = serializers.UUIDField(read_only=True)
    primary_run_id = serializers.UUIDField(read_only=True)
    headline = serializers.SerializerMethodField()
    confidence = serializers.SerializerMethodField()

    class Meta:
        model = Investigation
        fields = (
            "id",
            "cluster_id",
            "primary_run_id",
            "status",
            "headline",
            "confidence",
            "build_status",
            "in_eval_set",
            "created_at",
            "updated_at",
            "finalized_at",
            "decided_at",
            "stale_reason",
        )

    def get_headline(self, obj: Investigation) -> str | None:
        return (obj.brief or {}).get("headline")

    def get_confidence(self, obj: Investigation) -> float | None:
        return (obj.brief or {}).get("confidence")


class InvestigationDetailSerializer(serializers.ModelSerializer):
    cluster_id = serializers.UUIDField(read_only=True)
    primary_run_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Investigation
        fields = (
            "id",
            "cluster_id",
            "primary_run_id",
            "supporting_run_ids",
            "status",
            "brief",
            "brief_schema_version",
            "cluster_snapshot",
            "human_decision",
            "decided_at",
            "stale_reason",
            "stale_marked_at",
            "build_status",
            "build_status_updated_at",
            "build_notes",
            "in_eval_set",
            "eval_set_notes",
            "created_at",
            "updated_at",
            "finalized_at",
        )


class RejectRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class StaleRequestSerializer(serializers.Serializer):
    stale_reason = serializers.ChoiceField(choices=StaleReason.choices, default=StaleReason.MANUAL)


class InvestigationStatusFilterSerializer(serializers.Serializer):
    """Used only to validate ``?status=`` query params; not a payload shape."""

    status = serializers.ChoiceField(choices=InvestigationStatus.choices, required=False)


# ---------------------------------------------------------------------------
# Ideations
# ---------------------------------------------------------------------------


class IdeationListSerializer(serializers.ModelSerializer):
    investigation_id = serializers.UUIDField(read_only=True)
    primary_run_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = Ideation
        fields = (
            "id",
            "investigation_id",
            "primary_run_id",
            "status",
            "guidance",
            "decided_at",
            "created_at",
        )


class IdeationDetailSerializer(serializers.ModelSerializer):
    investigation_id = serializers.UUIDField(read_only=True)
    primary_run_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = Ideation
        fields = (
            "id",
            "investigation_id",
            "primary_run_id",
            "status",
            "guidance",
            "output",
            "output_schema_version",
            "human_decision",
            "decided_at",
            "created_at",
            "updated_at",
        )
