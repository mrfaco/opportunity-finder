"""Orchestrator: start_run records snapshot; tool registry exports valid schema."""

from __future__ import annotations

import json

import numpy as np
import pytest
from django.utils import timezone

from agents import orchestrator
from agents import tools as tool_registry
from agents.models import AgentRun, AgentRunStatus
from clusters.models import EMBEDDING_DIM, Cluster, ClusterStatus, Source


def _unit_vec(seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=EMBEDDING_DIM)
    v /= np.linalg.norm(v) + 1e-12
    return v.tolist()


@pytest.mark.django_db
def test_start_run_creates_running_run_with_snapshot(monkeypatch, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True

    # Stub the loop so we don't try to call Anthropic in the smoke test.
    from agents import tasks as agent_tasks

    monkeypatch.setattr(agent_tasks, "run_agent_loop", _no_op_task())

    cluster = Cluster.objects.create(
        status=ClusterStatus.PENDING,
        size=0,
        first_seen_at=timezone.now(),
        last_seen_at=timezone.now(),
        sources=[Source.HACKER_NEWS],
        centroid_embedding=_unit_vec(),
        classifier_score=0.9,
    )

    run_id = orchestrator.start_run(cluster.id, agent_name="investigation")
    run = AgentRun.objects.get(pk=run_id)

    assert run.status == AgentRunStatus.RUNNING
    assert "prompts" in run.config_snapshot
    assert run.config_snapshot["tool_registry_version"]
    assert run.prompt_hashes  # denormalized for filtering
    assert "system" in run.prompt_hashes


def test_export_mcp_definitions_yields_valid_json_schema():
    defs = tool_registry.export_mcp_definitions("investigation")
    assert len(defs) >= 1
    for d in defs:
        assert "name" in d and "description" in d and "inputSchema" in d
        # The schema should round-trip through json.dumps cleanly.
        json.dumps(d["inputSchema"])
        # And declare itself an object schema (Pydantic emits this).
        assert d["inputSchema"].get("type") == "object"


def _no_op_task():
    """Return a stand-in for ``run_agent_loop`` that does nothing when delayed."""

    class _Stub:
        def delay(self, *args, **kwargs):
            return None

    return _Stub()
