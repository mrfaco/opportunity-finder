"""Durable discovery state, dialogue mail and research provenance."""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q


class Discovery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schema_version = models.CharField(max_length=16, default="1.0")
    objective = models.TextField()
    constraints = models.TextField(blank=True)
    config_snapshot = models.JSONField(default=dict)
    prompt_snapshot = models.JSONField(default=dict)
    connector_snapshot = models.JSONField(default=list)
    runtime_snapshot = models.JSONField(default=dict)
    status = models.CharField(max_length=24, default="queued", db_index=True)
    outcome = models.CharField(max_length=24, blank=True)
    stop_reason = models.CharField(max_length=40, blank=True)
    turns_completed = models.PositiveIntegerField(default=0)
    cost_used_usd = models.DecimalField(max_digits=14, decimal_places=8, default=0)
    error_summary = models.TextField(blank=True)
    report_markdown = models.TextField(blank=True)
    report_json = models.JSONField(default=dict)
    idempotency_key = models.CharField(max_length=200, unique=True, null=True, blank=True)
    request_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.objective[:100]


class DiscoveryTask(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    discovery = models.ForeignKey(Discovery, on_delete=models.CASCADE, related_name="tasks")
    role = models.CharField(max_length=16)
    turn_number = models.PositiveIntegerField()
    state = models.CharField(max_length=16, default="open", db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    context = models.JSONField(default=dict)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["turn_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["discovery", "turn_number"], name="discovery_task_turn"
            ),
            models.UniqueConstraint(
                fields=["discovery"],
                condition=Q(state__in=["open", "working"]),
                name="discovery_one_active_task",
            ),
            models.CheckConstraint(condition=Q(turn_number__gte=1), name="discovery_task_positive"),
        ]


class DiscoveryTurn(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    discovery = models.ForeignKey(Discovery, on_delete=models.CASCADE, related_name="turns")
    task = models.OneToOneField(DiscoveryTask, on_delete=models.CASCADE, related_name="turn")
    number = models.PositiveIntegerField()
    role = models.CharField(max_length=16)
    input_snapshot = models.JSONField(default=dict)
    submission = models.JSONField(default=dict)
    model = models.CharField(max_length=160, blank=True)
    prompt_hash = models.CharField(max_length=64, blank=True)
    submission_hash = models.CharField(max_length=64, blank=True)
    model_calls = models.PositiveIntegerField(default=0)
    research_calls = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(fields=["discovery", "number"], name="discovery_turn")
        ]


class DiscoveryMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schema_version = models.CharField(max_length=16, default="1.0")
    discovery = models.ForeignKey(Discovery, on_delete=models.CASCADE, related_name="messages")
    task = models.ForeignKey(
        DiscoveryTask, on_delete=models.CASCADE, null=True, related_name="messages"
    )
    sender = models.CharField(max_length=16)
    recipient = models.CharField(max_length=16)
    kind = models.CharField(max_length=16)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    artifact_id = models.UUIDField(null=True, blank=True)
    reply_to = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True)
    operation_key = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["discovery", "operation_key"], name="discovery_mail_key"
            ),
        ]
        indexes = [models.Index(fields=["discovery", "recipient", "read_at"])]


class ProposalRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    discovery = models.ForeignKey(Discovery, on_delete=models.CASCADE, related_name="proposals")
    turn = models.OneToOneField(DiscoveryTurn, on_delete=models.CASCADE, related_name="proposal")
    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True)
    content = models.JSONField()
    content_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["turn__number"]


class ValidationRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    discovery = models.ForeignKey(Discovery, on_delete=models.CASCADE, related_name="validations")
    turn = models.OneToOneField(DiscoveryTurn, on_delete=models.CASCADE, related_name="validation")
    proposal = models.ForeignKey(
        ProposalRevision, on_delete=models.PROTECT, related_name="validations"
    )
    verdict = models.CharField(max_length=16)
    content = models.JSONField()
    content_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["turn__number"]


class JudgmentRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    discovery = models.ForeignKey(Discovery, on_delete=models.CASCADE, related_name="judgments")
    turn = models.OneToOneField(DiscoveryTurn, on_delete=models.CASCADE, related_name="judgment")
    proposal = models.ForeignKey(
        ProposalRevision, on_delete=models.PROTECT, related_name="judgments"
    )
    validation = models.ForeignKey(
        ValidationRevision, on_delete=models.PROTECT, related_name="judgments"
    )
    verdict = models.CharField(max_length=16)
    content = models.JSONField()
    content_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["turn__number"]


class ResearchOperation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    discovery = models.ForeignKey(Discovery, on_delete=models.CASCADE, related_name="operations")
    task = models.ForeignKey(DiscoveryTask, on_delete=models.CASCADE, related_name="operations")
    operation_key = models.CharField(max_length=200)
    kind = models.CharField(max_length=24)
    connector = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=24, default="planned")
    request = models.JSONField(default=dict)
    response = models.JSONField(default=dict)
    reserved_usd = models.DecimalField(max_digits=14, decimal_places=8, default=0)
    cost_usd = models.DecimalField(max_digits=14, decimal_places=8, default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cached_tokens = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["discovery", "operation_key"], name="discovery_op_key"),
        ]


class Evidence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schema_version = models.CharField(max_length=16, default="1.0")
    discovery = models.ForeignKey(Discovery, on_delete=models.CASCADE, related_name="evidence")
    turn = models.ForeignKey(DiscoveryTurn, on_delete=models.SET_NULL, null=True, blank=True)
    operation = models.ForeignKey(
        ResearchOperation, on_delete=models.SET_NULL, null=True, blank=True
    )
    connector = models.CharField(max_length=100)
    connector_version = models.CharField(max_length=32, default="1.0")
    external_id = models.CharField(max_length=1000)
    url = models.URLField(max_length=2000)
    title = models.TextField(blank=True)
    content = models.TextField()
    kind = models.CharField(max_length=24)
    origin = models.CharField(max_length=255)
    content_hash = models.CharField(max_length=64)
    published_at = models.DateTimeField(null=True, blank=True)
    retrieved_at = models.DateTimeField()
    metadata = models.JSONField(default=dict)

    class Meta:
        ordering = ["retrieved_at"]


class DiscoveryDispatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.OneToOneField(DiscoveryTask, on_delete=models.CASCADE, related_name="dispatch")
    published_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
