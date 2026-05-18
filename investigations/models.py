"""Investigation review queue.

One investigation = one brief produced by an agent run, awaiting human triage.
Humans promote, reject, or mark stale through admin.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models

from agents.models import AgentRun
from clusters.models import Cluster


class InvestigationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    AWAITING_REVIEW = "awaiting_review", "Awaiting review"
    PROMOTED = "promoted", "Promoted"
    REJECTED = "rejected", "Rejected"
    STALE = "stale", "Stale"
    SUPERSEDED = "superseded", "Superseded"


class StaleReason(models.TextChoices):
    PROMPT_CHANGED = "prompt_changed", "Prompt changed"
    CLUSTER_CHANGED = "cluster_changed", "Cluster changed"
    AGE = "age", "Age"
    MANUAL = "manual", "Manual"


class BuildStatus(models.TextChoices):
    NOT_PURSUED = "not_pursued", "Not pursued"
    RESEARCHING_FURTHER = "researching_further", "Researching further"
    BUILDING = "building", "Building"
    SHIPPED = "shipped", "Shipped"
    ABANDONED = "abandoned", "Abandoned"


class Investigation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    cluster = models.ForeignKey(
        Cluster, on_delete=models.CASCADE, related_name="investigations", db_index=True
    )
    status = models.CharField(
        max_length=24,
        choices=InvestigationStatus.choices,
        default=InvestigationStatus.AWAITING_REVIEW,
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    finalized_at = models.DateTimeField(null=True, blank=True)

    primary_run = models.ForeignKey(
        AgentRun, on_delete=models.CASCADE, related_name="primary_investigations"
    )
    supporting_run_ids = ArrayField(models.UUIDField(), default=list, blank=True)

    brief = models.JSONField()
    brief_schema_version = models.CharField(max_length=16, default="1.0")
    cluster_snapshot = models.JSONField()

    human_decision = models.JSONField(null=True, blank=True)
    decided_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="investigations_decided",
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    superseded_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="supersedes",
    )

    stale_reason = models.CharField(
        max_length=24, choices=StaleReason.choices, null=True, blank=True
    )
    stale_marked_at = models.DateTimeField(null=True, blank=True)

    build_status = models.CharField(
        max_length=24, choices=BuildStatus.choices, null=True, blank=True
    )
    build_status_updated_at = models.DateTimeField(null=True, blank=True)
    build_notes = models.TextField(null=True, blank=True)

    in_eval_set = models.BooleanField(default=False)
    eval_set_notes = models.TextField(null=True, blank=True)
