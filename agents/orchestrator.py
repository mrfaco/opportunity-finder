"""Orchestrator.

The orchestrator is the boundary between the world and the agent. It validates
inputs, takes the immutable snapshot of cluster + prompts + tool registry that
the loop will read from, persists the ``AgentRun`` row, and enqueues the
Celery task that actually runs the loop.

Snapshots over live lookups: once a run is started, the agent reads from
``config_snapshot`` and ``cluster_snapshot``. Subsequent prompt edits, tool
registrations, or cluster mutations do not affect the in-flight run.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from agents import prompts as prompt_loader
from agents import tools as tool_registry
from agents.models import AgentRun, AgentRunStatus, TerminationReason
from agents.tasks import run_agent_loop
from clusters.models import Cluster


@dataclass
class BudgetConfig:
    max_steps: int
    max_cost_usd: Decimal
    max_duration_s: int

    @classmethod
    def default(cls) -> "BudgetConfig":
        return cls(
            max_steps=settings.DEFAULT_BUDGET_MAX_STEPS,
            max_cost_usd=settings.DEFAULT_BUDGET_MAX_COST_USD,
            max_duration_s=settings.DEFAULT_BUDGET_MAX_DURATION_S,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "max_cost_usd": str(self.max_cost_usd),
            "max_duration_s": self.max_duration_s,
        }


def _snapshot_cluster(cluster: Cluster) -> dict[str, Any]:
    """Freeze the cluster state at run start."""
    return {
        "id": str(cluster.id),
        "status": cluster.status,
        "title": cluster.title,
        "summary": cluster.summary,
        "size": cluster.size,
        "sources": list(cluster.sources),
        "category_tags": list(cluster.category_tags),
        "first_seen_at": cluster.first_seen_at.isoformat() if cluster.first_seen_at else None,
        "last_seen_at": cluster.last_seen_at.isoformat() if cluster.last_seen_at else None,
        "classifier_score": cluster.classifier_score,
        "item_ids": [str(i) for i in cluster.items.values_list("id", flat=True)],
    }


def _snapshot_prompts(agent_name: str) -> tuple[dict[str, Any], dict[str, str]]:
    """Load all prompts for the agent and return (full_snapshot, hashes-only)."""
    loaded = prompt_loader.get_prompts_for_agent(agent_name)
    full: dict[str, Any] = {}
    hashes: dict[str, str] = {}
    for kind, p in loaded.items():
        full[kind] = {"content": p.content, "hash": p.hash, "path": p.path}
        hashes[kind] = p.hash
    return full, hashes


def _model_selection(agent_name: str) -> dict[str, str]:
    if agent_name == "investigation":
        return {"investigation": settings.MODEL_INVESTIGATION}
    if agent_name == "filter":
        return {"filter": settings.MODEL_FILTER}
    return {}


def start_run(
    cluster_id: UUID | str,
    agent_name: str,
    trigger: str = "manual",
    budget: BudgetConfig | None = None,
) -> UUID:
    """Snapshot, persist, enqueue. Returns the new run id."""
    cluster = Cluster.objects.get(pk=cluster_id)
    cfg_budget = budget or BudgetConfig.default()

    prompts_snapshot, prompt_hashes = _snapshot_prompts(agent_name)
    config_snapshot: dict[str, Any] = {
        "prompts": prompts_snapshot,
        "tool_registry_version": tool_registry.registry_hash(),
        "tool_names": sorted(tool_registry.AGENT_TOOLSETS.get(agent_name, [])),
        "model_selection": _model_selection(agent_name),
        "budgets": cfg_budget.as_dict(),
    }
    cluster_snapshot = _snapshot_cluster(cluster)

    with transaction.atomic():
        run = AgentRun.objects.create(
            agent_name=agent_name,
            cluster=cluster,
            trigger=trigger,
            status=AgentRunStatus.RUNNING,
            budget_max_steps=cfg_budget.max_steps,
            budget_max_cost_usd=cfg_budget.max_cost_usd,
            budget_max_duration_s=cfg_budget.max_duration_s,
            models_used=list(config_snapshot["model_selection"].values()),
            config_snapshot=config_snapshot,
            cluster_snapshot=cluster_snapshot,
            prompt_hashes=prompt_hashes,
        )

    # Enqueue the actual loop. Done outside the atomic block so the row is
    # visible to the worker by the time it picks the task up.
    run_agent_loop.delay(str(run.id))
    return run.id


def get_run(run_id: UUID | str) -> dict[str, Any]:
    run = AgentRun.objects.get(pk=run_id)
    return {
        "id": str(run.id),
        "agent_name": run.agent_name,
        "cluster_id": str(run.cluster_id),
        "status": run.status,
        "trigger": run.trigger,
        "started_at": run.started_at.isoformat(),
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        "steps_used": run.steps_used,
        "cost_used_usd": str(run.cost_used_usd),
        "duration_used_s": run.duration_used_s,
        "termination_reason": run.termination_reason,
        "prompt_hashes": run.prompt_hashes,
    }


def kill_run(run_id: UUID | str, reason: str) -> None:
    """Mark a run as killed. The worker checks this flag each step."""
    AgentRun.objects.filter(pk=run_id, status=AgentRunStatus.RUNNING).update(
        status=AgentRunStatus.KILLED,
        termination_reason=TerminationReason.KILLED_BY_HUMAN,
        ended_at=timezone.now(),
        error_summary=f"Killed by human: {reason}",
    )


def replay_run(run_id: UUID | str, new_prompts: bool = False) -> UUID:
    """Replay a past run.

    TODO(v1-followup): implement once the loop has real model + tool calls.
    Loads the historical ``config_snapshot``; if ``new_prompts`` is True,
    re-snapshots the current prompts from disk before kicking off the new run.
    """
    raise NotImplementedError(
        "TODO(v1-followup): implement replay_run once the loop runs end-to-end"
    )


def list_runs(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    qs = AgentRun.objects.all().order_by("-started_at")
    if filters:
        qs = qs.filter(**filters)
    return [
        {
            "id": str(r.id),
            "agent_name": r.agent_name,
            "cluster_id": str(r.cluster_id),
            "status": r.status,
            "started_at": r.started_at.isoformat(),
            "cost_used_usd": str(r.cost_used_usd),
            "steps_used": r.steps_used,
            "prompt_hashes": r.prompt_hashes,
        }
        for r in qs[:200]
    ]
