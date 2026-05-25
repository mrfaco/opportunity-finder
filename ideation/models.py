"""Ideation review queue.

One ideation = one structured concept set produced by an agent run, awaiting
human review. Triggered when a human promotes an investigation; an investigation
may have many ideations over time (each with its own optional steering guidance).
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from agents.models import AgentRun
from investigations.models import Investigation


class IdeationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    AWAITING_REVIEW = "awaiting_review", "Awaiting review"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"
    STALE = "stale", "Stale"


class Ideation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    investigation = models.ForeignKey(
        Investigation,
        on_delete=models.CASCADE,
        related_name="ideations",
        db_index=True,
    )

    status = models.CharField(
        max_length=24,
        choices=IdeationStatus.choices,
        default=IdeationStatus.DRAFT,
    )
    # Optional human steering supplied on re-ideate. Empty on the first run.
    guidance = models.TextField(blank=True, default="")

    output = models.JSONField(default=dict)
    output_schema_version = models.CharField(max_length=16, default="1.0")

    primary_run = models.ForeignKey(
        AgentRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="primary_ideations",
    )

    human_decision = models.JSONField(null=True, blank=True)
    decided_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ideations_decided",
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["investigation", "-created_at"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["-created_at"]
