"""Agent run / step / event tables.

These are the structured-log substrate. Every model call, every tool call,
every agent decision writes here. No ``print()`` statements.
"""

from __future__ import annotations

import uuid

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models

from clusters.models import Cluster


class AgentRunStatus(models.TextChoices):
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    KILLED = "killed", "Killed"
    BUDGET_EXHAUSTED = "budget_exhausted", "Budget exhausted"


class AgentRunTrigger(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    MANUAL = "manual", "Manual"
    RERUN = "rerun", "Rerun"
    EVAL = "eval", "Eval"


class TerminationReason(models.TextChoices):
    AGENT_DECIDED_DONE = "agent_decided_done", "Agent decided done"
    BUDGET_STEPS = "budget_steps", "Budget exhausted (steps)"
    BUDGET_COST = "budget_cost", "Budget exhausted (cost)"
    BUDGET_DURATION = "budget_duration", "Budget exhausted (duration)"
    ERROR = "error", "Error"
    KILLED_BY_HUMAN = "killed_by_human", "Killed by human"
    TOOL_FAILURE_CASCADE = "tool_failure_cascade", "Tool failure cascade"
    LOOP_DETECTED = "loop_detected", "Loop detected"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed", "Schema validation failed"
    MODEL_API_FAILURE = "model_api_failure", "Model API failure"
    WORKER_CRASHED = "worker_crashed", "Worker crashed"


class AgentRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    agent_name = models.CharField(max_length=64)
    cluster = models.ForeignKey(Cluster, on_delete=models.CASCADE, related_name="agent_runs")

    trigger = models.CharField(max_length=16, choices=AgentRunTrigger.choices)
    status = models.CharField(
        max_length=24, choices=AgentRunStatus.choices, default=AgentRunStatus.RUNNING
    )

    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    budget_max_steps = models.PositiveIntegerField()
    budget_max_cost_usd = models.DecimalField(max_digits=10, decimal_places=4)
    budget_max_duration_s = models.PositiveIntegerField()
    steps_used = models.PositiveIntegerField(default=0)
    cost_used_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    duration_used_s = models.PositiveIntegerField(default=0)

    final_output = models.JSONField(null=True, blank=True)
    termination_reason = models.CharField(
        max_length=32, choices=TerminationReason.choices, null=True, blank=True
    )
    error_summary = models.TextField(null=True, blank=True)

    models_used = ArrayField(models.CharField(max_length=64), default=list, blank=True)

    eval_run_id = models.UUIDField(null=True, blank=True)
    human_label = models.JSONField(null=True, blank=True)
    seed = models.IntegerField(null=True, blank=True)

    config_snapshot = models.JSONField()
    cluster_snapshot = models.JSONField()
    # Denormalized from config_snapshot for fast filtering by prompt version.
    prompt_hashes = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["cluster", "started_at"]),
            GinIndex(fields=["prompt_hashes"], name="agentrun_prompts_gin"),
        ]


class StepType(models.TextChoices):
    TOOL_CALL = "tool_call", "Tool call"
    MODEL_REASONING_ONLY = "model_reasoning_only", "Model reasoning only"
    FINAL_OUTPUT = "final_output", "Final output"


class ToolStatus(models.TextChoices):
    SUCCESS = "success", "Success"
    ERROR = "error", "Error"
    RATE_LIMITED = "rate_limited", "Rate limited"
    TIMEOUT = "timeout", "Timeout"
    VALIDATION_FAILED = "validation_failed", "Validation failed"


class AgentStep(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="steps")
    step_number = models.PositiveIntegerField()

    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    step_type = models.CharField(max_length=32, choices=StepType.choices)
    tool_name = models.CharField(max_length=64, null=True, blank=True)
    tool_input = models.JSONField(null=True, blank=True)
    tool_output_summary = models.TextField(null=True, blank=True)
    tool_output_full_ref = models.CharField(max_length=256, null=True, blank=True)
    tool_status = models.CharField(max_length=24, choices=ToolStatus.choices, null=True, blank=True)
    tool_retry_count = models.PositiveIntegerField(default=0)
    was_cached = models.BooleanField(default=False)
    cache_age_seconds = models.PositiveIntegerField(null=True, blank=True)

    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cached_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    model = models.CharField(max_length=64, null=True, blank=True)

    cumulative_cost_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    cumulative_steps = models.PositiveIntegerField(default=0)

    is_anomaly = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["run", "step_number"]),
            models.Index(fields=["tool_name", "started_at"]),
            models.Index(
                fields=["tool_status"],
                name="agentstep_tool_err_idx",
                condition=models.Q(tool_status="error"),
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "step_number"], name="agentstep_run_stepnum_unique"
            ),
        ]


class EventType(models.TextChoices):
    MODEL_REQUEST = "model_request", "Model request"
    MODEL_RESPONSE = "model_response", "Model response"
    MODEL_THINKING = "model_thinking", "Model thinking"
    TOOL_REQUEST = "tool_request", "Tool request"
    TOOL_RESPONSE = "tool_response", "Tool response"
    TOOL_ERROR = "tool_error", "Tool error"
    GUARDRAIL_TRIGGERED = "guardrail_triggered", "Guardrail triggered"
    BUDGET_WARNING = "budget_warning", "Budget warning"
    HUMAN_INTERVENTION = "human_intervention", "Human intervention"


class AgentEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="events")
    step = models.ForeignKey(
        AgentStep, on_delete=models.CASCADE, related_name="events", null=True, blank=True
    )
    sequence = models.PositiveIntegerField()
    recorded_at = models.DateTimeField(auto_now_add=True)

    event_type = models.CharField(max_length=32, choices=EventType.choices)
    payload = models.JSONField()
    payload_size_bytes = models.PositiveIntegerField(default=0)

    prompt_hash = models.CharField(max_length=64, null=True, blank=True)
    response_hash = models.CharField(max_length=64, null=True, blank=True)
    tool_name = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["step", "sequence"]),
            models.Index(fields=["run", "recorded_at"]),
        ]
