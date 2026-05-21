"""Filter / classifier persistence + eval set + eval-run history.

Every classification we run in production becomes a row in ``FilterClassification``.
The eval system maintains a curated labeled set (``FilterEvalSet``) and runs the
classifier across it on demand, capturing precision/recall by tier and the
specific per-item disagreements.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from clusters.models import ClusterItem


class IngestionCheckpoint(models.Model):
    """Per-source watermark for incremental ingestion.

    Each ingestion run pulls only items posted after ``last_item_posted_at``
    and advances the watermark as items are successfully processed. A run that
    fails partway leaves the checkpoint at the last good item, so the next run
    resumes from there rather than re-classifying (and re-paying for) work
    already done.
    """

    source = models.CharField(max_length=32, unique=True)
    last_item_posted_at = models.DateTimeField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    items_seen = models.PositiveIntegerField(default=0)
    opportunities_found = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.source} @ {self.last_item_posted_at}"


class VerdictBand(models.TextChoices):
    HIGH_YES = "high_yes", "High yes"
    HIGH_NO = "high_no", "High no"
    UNCERTAIN = "uncertain", "Uncertain"


class DiscardReason(models.TextChoices):
    BELOW_THRESHOLD = "below_threshold", "Below threshold"
    STRUCTURAL_PREFILTER = "structural_prefilter", "Structural prefilter"
    DUPLICATE = "duplicate", "Duplicate"


class FilterClassification(models.Model):
    """One classifier verdict — keyed by prompt hash so we can slice by version."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    item = models.ForeignKey(
        ClusterItem,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="classifications",
    )

    prompt_hash = models.CharField(max_length=64, db_index=True)
    model = models.CharField(max_length=64)
    classified_at = models.DateTimeField(auto_now_add=True, db_index=True)

    is_opportunity = models.BooleanField()
    confidence = models.FloatField()
    reason = models.TextField()

    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cached_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    latency_ms = models.PositiveIntegerField(default=0)

    verdict_band = models.CharField(max_length=16, choices=VerdictBand.choices)

    discarded = models.BooleanField(default=False)
    discard_reason = models.CharField(
        max_length=32, choices=DiscardReason.choices, null=True, blank=True
    )
    human_label = models.JSONField(null=True, blank=True)


class HumanLabel(models.TextChoices):
    YES = "yes", "Yes"
    NO = "no", "No"
    AMBIGUOUS = "ambiguous", "Ambiguous"
    ADVERSARIAL = "adversarial", "Adversarial"


class HumanConfidence(models.TextChoices):
    HIGH = "high", "High"
    MEDIUM = "medium", "Medium"
    LOW = "low", "Low"


class DifficultyTier(models.TextChoices):
    CLEAR_YES = "clear_yes", "Clear yes"
    CLEAR_NO = "clear_no", "Clear no"
    AMBIGUOUS = "ambiguous", "Ambiguous"
    ADVERSARIAL = "adversarial", "Adversarial"


class SourcedFrom(models.TextChoices):
    PRODUCTION_CLASSIFICATION = "production_classification", "Production classification"
    HAND_CURATED = "hand_curated", "Hand curated"
    IMPORTED = "imported", "Imported"


class FilterEvalSet(models.Model):
    """One labeled item in the classifier evaluation set."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    content = models.TextField()
    content_context = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=64)

    human_label = models.CharField(max_length=16, choices=HumanLabel.choices)
    human_confidence = models.CharField(
        max_length=8, choices=HumanConfidence.choices, null=True, blank=True
    )
    difficulty_tier = models.CharField(max_length=16, choices=DifficultyTier.choices)
    reasoning_note = models.TextField(null=True, blank=True)

    labeled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="filter_eval_items_labeled",
    )
    labeled_at = models.DateTimeField(auto_now_add=True, db_index=True)
    last_revised_at = models.DateTimeField(null=True, blank=True)

    sourced_from = models.CharField(max_length=32, choices=SourcedFrom.choices)
    production_classification = models.ForeignKey(
        FilterClassification,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="eval_items",
    )


class FilterEvalRun(models.Model):
    """One end-to-end evaluation of a prompt version against the eval set."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    run_at = models.DateTimeField(auto_now_add=True, db_index=True)
    prompt_hash = models.CharField(max_length=64, db_index=True)
    prompt_content = models.TextField()
    model = models.CharField(max_length=64)

    eval_set_size = models.PositiveIntegerField()
    precision = models.FloatField()
    recall = models.FloatField()
    f1 = models.FloatField()
    metrics_by_tier = models.JSONField(default=dict, blank=True)
    eval_set_snapshot = models.JSONField(default=list, blank=True)

    total_cost_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    total_duration_s = models.PositiveIntegerField(default=0)


class FilterEvalClassification(models.Model):
    """The per-item verdict produced during one eval run."""

    YES_NO = [("yes", "Yes"), ("no", "No")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    eval_run = models.ForeignKey(
        FilterEvalRun, on_delete=models.CASCADE, related_name="classifications"
    )
    eval_item = models.ForeignKey(
        FilterEvalSet, on_delete=models.CASCADE, related_name="eval_classifications"
    )
    model_verdict = models.CharField(max_length=8, choices=YES_NO)
    model_confidence = models.FloatField()
    model_reason = models.TextField()
    agrees_with_label = models.BooleanField()
