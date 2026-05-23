"""Tests for the investigation agent loop and its model call.

The Anthropic SDK is mocked everywhere — these tests never hit the network.
The real query_cluster tool runs against the test DB so the loop's tool
dispatch + history mutation is exercised end-to-end.
"""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.utils import timezone

from agents import loop as agent_loop
from agents import prompts as prompt_loader
from agents.models import (
    AgentEvent,
    AgentRun,
    AgentRunStatus,
    AgentRunTrigger,
    AgentStep,
    EventType,
    TerminationReason,
)
from agents.tools import all_tools_for_agent, export_mcp_definitions, get_tool
from clusters.models import (
    EMBEDDING_DIM,
    ClassifierVerdict,
    Cluster,
    ClusterItem,
    ClusterStatus,
    Source,
)
from investigations.models import Investigation, InvestigationStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _vec() -> list[float]:
    return [0.01] * EMBEDDING_DIM


def _make_run(cluster: Cluster) -> AgentRun:
    """Build an AgentRun with realistic config + cluster snapshots."""
    prompts = prompt_loader.get_prompts_for_agent("investigation")
    return AgentRun.objects.create(
        agent_name="investigation",
        cluster=cluster,
        trigger=AgentRunTrigger.MANUAL,
        status=AgentRunStatus.RUNNING,
        budget_max_steps=10,
        budget_max_cost_usd=Decimal("0.50"),
        budget_max_duration_s=300,
        models_used=["claude-sonnet-4-6"],
        config_snapshot={
            "prompts": {
                k: {"content": p.content, "hash": p.hash, "path": p.path}
                for k, p in prompts.items()
            },
            "tool_registry_version": "tooldigest",
            "tool_names": [t.name for t in all_tools_for_agent("investigation")],
            "model_selection": {"investigation": "claude-sonnet-4-6"},
            "budgets": {
                "max_steps": 10,
                "max_cost_usd": "0.50",
                "max_duration_s": 300,
            },
        },
        cluster_snapshot={
            "id": str(cluster.id),
            "status": cluster.status,
            "title": cluster.title,
            "summary": cluster.summary,
            "size": cluster.size,
            "sources": list(cluster.sources),
            "category_tags": list(cluster.category_tags),
            "first_seen_at": cluster.first_seen_at.isoformat(),
            "last_seen_at": cluster.last_seen_at.isoformat(),
            "classifier_score": cluster.classifier_score,
            "item_ids": [],
        },
        prompt_hashes={k: p.hash for k, p in prompts.items()},
    )


def _make_cluster_with_items() -> Cluster:
    now = timezone.now()
    cluster = Cluster.objects.create(
        status=ClusterStatus.PENDING,
        size=1,
        first_seen_at=now,
        last_seen_at=now,
        sources=[Source.HACKER_NEWS],
        centroid_embedding=_vec(),
        title="Indie PDF tooling",
        summary="Bookkeepers want a one-time-purchase PDF merger",
        category_tags=["dev-tools"],
        classifier_score=0.9,
    )
    ClusterItem.objects.create(
        cluster=cluster,
        source=Source.HACKER_NEWS,
        source_item_id="42",
        url="https://news.ycombinator.com/item?id=42",
        title="Ask HN: simple PDF merge?",
        author="someone",
        posted_at=now,
        raw_text="I just want to merge a folder of PDFs without a subscription.",
        snippet="I just want to merge a folder of PDFs without a subscription.",
        classifier_verdict=ClassifierVerdict.OPPORTUNITY,
        classifier_confidence=0.92,
        embedding=_vec(),
        added_to_cluster_at=now,
        assigned_at=now,
    )
    return cluster


def _tool_use_block(tool_id: str, name: str, tool_input: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _fake_anthropic_response(
    content_blocks, stop_reason="tool_use", input_tokens=200, output_tokens=120, cache_read=0
):
    return SimpleNamespace(
        content=content_blocks,
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
        ),
    )


_VALID_BRIEF: dict = {
    "schema_version": "1.0",
    "headline": "Local-first PDF merge for solo bookkeepers",
    "problem_statement": ("Solo bookkeepers want to merge client PDFs locally, no subscription."),
    "target_user": "Solo and small-firm bookkeepers handling 5-30 clients.",
    "evidence_summary": (
        "The cluster items and an external Reddit thread describe the same "
        "recurring monthly task and the same refusal to use cloud-upload tools."
    ),
    "evidence": [
        {
            "schema_version": "1.0",
            "source": "hacker_news",
            "url": "https://news.ycombinator.com/item?id=42",
            "title": "Ask HN: simple PDF merge?",
            "snippet": "I just want to merge a folder of PDFs without a subscription.",
            "posted_at": None,
        },
    ],
    "competitors": [
        {
            "schema_version": "1.0",
            "name": "Adobe Acrobat",
            "url": "https://adobe.com",
            "revenue_signal": None,
            "notes": "Subscription, too heavy for solo users.",
        }
    ],
    "differentiators": ["one-time purchase", "fully offline"],
    "risks": ["niche may be small", "established players can ship a cheap tier"],
    "confidence": 0.75,
    "recommended_next_step": "Validate with five bookkeepers on r/bookkeeping.",
}


# ---------------------------------------------------------------------------
# _call_model — request shape, parsing, caching headers
# ---------------------------------------------------------------------------
def test_call_model_extracts_system_and_converts_tools(monkeypatch):
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_anthropic_response(
        [_text_block("Done."), _tool_use_block("tu_1", "query_cluster", {"cluster_id": "x"})]
    )
    monkeypatch.setattr(agent_loop, "get_client", lambda: fake_client)

    history = [
        {"role": "system", "content": "system prompt body"},
        {"role": "user", "content": "procedural body"},
        {"role": "user", "content": "the cluster context"},
    ]
    tools = [
        {
            "name": "query_cluster",
            "description": "look up a cluster",
            "inputSchema": {"type": "object", "properties": {}},
        }
    ]

    out = agent_loop._call_model(history, "claude-sonnet-4-6", tools)

    # The SDK was called with system lifted out + cache_control + MCP→SDK tools.
    _, kwargs = fake_client.messages.create.call_args
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["system"] == [
        {"type": "text", "text": "system prompt body", "cache_control": {"type": "ephemeral"}}
    ]
    assert kwargs["messages"] == history[1:]  # system removed
    assert kwargs["tools"] == [
        {
            "name": "query_cluster",
            "description": "look up a cluster",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    assert kwargs["cache_control"] == {"type": "ephemeral"}

    # Response parsed into the flat dict the loop expects.
    assert out["tool_calls"] == [
        {"id": "tu_1", "name": "query_cluster", "input": {"cluster_id": "x"}}
    ]
    # tool_calls present → final_text is suppressed even though a text block existed.
    assert out["final_text"] is None
    assert out["input_tokens"] == 200
    assert out["output_tokens"] == 120
    assert out["cached_tokens"] == 0


def test_call_model_returns_final_text_when_no_tool_calls(monkeypatch):
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_anthropic_response(
        [_text_block("All done.")], stop_reason="end_turn"
    )
    monkeypatch.setattr(agent_loop, "get_client", lambda: fake_client)

    out = agent_loop._call_model([{"role": "user", "content": "go"}], "claude-sonnet-4-6", [])
    assert out["tool_calls"] == []
    assert out["final_text"] == "All done."


# ---------------------------------------------------------------------------
# run_loop — end-to-end with mocked _call_model and the real query_cluster tool
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_run_loop_records_brief_and_creates_investigation(monkeypatch):
    cluster = _make_cluster_with_items()
    run = _make_run(cluster)

    # Script: first call → query_cluster, second call → record_brief.
    calls_made = {"n": 0}

    def fake_call_model(history, model, tools):
        calls_made["n"] += 1
        if calls_made["n"] == 1:
            return {
                "content": [
                    _tool_use_block("tu_1", "query_cluster", {"cluster_id": str(cluster.id)})
                ],
                "stop_reason": "tool_use",
                "input_tokens": 200,
                "output_tokens": 80,
                "cached_tokens": 0,
                "tool_calls": [
                    {
                        "id": "tu_1",
                        "name": "query_cluster",
                        "input": {"cluster_id": str(cluster.id)},
                    }
                ],
                "final_text": None,
            }
        return {
            "content": [_tool_use_block("tu_2", "record_brief", _VALID_BRIEF)],
            "stop_reason": "tool_use",
            "input_tokens": 250,
            "output_tokens": 400,
            "cached_tokens": 150,
            "tool_calls": [{"id": "tu_2", "name": "record_brief", "input": _VALID_BRIEF}],
            "final_text": None,
        }

    monkeypatch.setattr(agent_loop, "_call_model", fake_call_model)

    agent_loop.run_loop(run.id)

    run.refresh_from_db()
    assert run.status == AgentRunStatus.COMPLETED
    assert run.termination_reason == TerminationReason.AGENT_DECIDED_DONE
    assert run.final_output["headline"] == _VALID_BRIEF["headline"]
    assert run.steps_used == 2
    assert run.cost_used_usd > 0

    # An Investigation row was created against this cluster + run.
    investigation = Investigation.objects.get(primary_run=run)
    assert investigation.cluster_id == cluster.id
    assert investigation.status == InvestigationStatus.AWAITING_REVIEW
    assert investigation.brief["headline"] == _VALID_BRIEF["headline"]

    # The trajectory has model + tool events for both steps.
    assert AgentStep.objects.filter(run=run).count() == 2
    assert AgentEvent.objects.filter(run=run, event_type=EventType.TOOL_REQUEST).count() == 2


@pytest.mark.django_db
def test_run_loop_writes_tool_result_into_history(monkeypatch):
    """The loop must feed each tool's output back as a tool_result block.

    We capture the second _call_model invocation's `history` argument and
    verify the previous turn's tool_use is paired with the matching
    tool_result block in the Anthropic-expected shape.
    """
    cluster = _make_cluster_with_items()
    run = _make_run(cluster)

    captured_histories: list[list[dict]] = []

    def fake_call_model(history, model, tools):
        captured_histories.append([dict(m) for m in history])
        if len(captured_histories) == 1:
            return {
                "content": [
                    _tool_use_block("tu_1", "query_cluster", {"cluster_id": str(cluster.id)})
                ],
                "stop_reason": "tool_use",
                "input_tokens": 100,
                "output_tokens": 30,
                "cached_tokens": 0,
                "tool_calls": [
                    {
                        "id": "tu_1",
                        "name": "query_cluster",
                        "input": {"cluster_id": str(cluster.id)},
                    }
                ],
                "final_text": None,
            }
        return {
            "content": [_tool_use_block("tu_2", "record_brief", _VALID_BRIEF)],
            "stop_reason": "tool_use",
            "input_tokens": 200,
            "output_tokens": 200,
            "cached_tokens": 100,
            "tool_calls": [{"id": "tu_2", "name": "record_brief", "input": _VALID_BRIEF}],
            "final_text": None,
        }

    monkeypatch.setattr(agent_loop, "_call_model", fake_call_model)
    agent_loop.run_loop(run.id)

    # Second call's history must include the assistant's tool_use turn AND
    # a user turn carrying a tool_result block with the matching id.
    second_history = captured_histories[1]
    assistant_turns = [m for m in second_history if m["role"] == "assistant"]
    user_tool_results = [
        m
        for m in second_history
        if m["role"] == "user"
        and isinstance(m["content"], list)
        and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])
    ]
    assert assistant_turns, "previous tool_use turn must be replayed back"
    assert user_tool_results, "tool_result block must be present"
    tr = user_tool_results[0]["content"][0]
    assert tr["tool_use_id"] == "tu_1"
    # tool_result content is JSON-stringified output; the real query_cluster
    # ran and returned the cluster summary.
    payload = json.loads(tr["content"])
    assert payload["status"] == "success"
    assert payload["cluster"]["title"] == "Indie PDF tooling"


# ---------------------------------------------------------------------------
# record_brief tool registration
# ---------------------------------------------------------------------------
def test_record_brief_tool_is_registered_for_investigation():
    names = [t.name for t in all_tools_for_agent("investigation")]
    assert "record_brief" in names


def test_record_brief_tool_input_schema_matches_brief():
    tool = get_tool("record_brief")
    schema = tool.input_schema()
    assert "headline" in schema["properties"]
    assert "confidence" in schema["properties"]


def test_record_brief_tool_definition_exports_for_anthropic():
    defs = export_mcp_definitions("investigation")
    record = next(d for d in defs if d["name"] == "record_brief")
    assert record["inputSchema"]["type"] == "object"
