"""Real Claude Code + MCP, scripted upstream inference and real PostgreSQL.

No paid calls: every model response comes from httpx.MockTransport. Run this
only against a development database. Leaves a clearly labelled synthetic report.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import django
import httpx
from django.test import override_settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from discovery.claude_runtime import Gateway  # noqa: E402
from discovery.loop import run_task  # noqa: E402
from discovery.models import ResearchOperation  # noqa: E402
from discovery.orchestrator import cancel_discovery, start_discovery  # noqa: E402
from discovery.schemas import Judgment  # noqa: E402
from scripts.smoke_discovery import _call, _response  # noqa: E402


def _stream(message, model):
    tool = message.get("tool_calls", [None])[0]
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_synthetic_fixture",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 100, "output_tokens": 0, "cost": 0},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": tool["id"],
                "name": "mcp__discovery__" + tool["function"]["name"],
                "input": {},
            }
            if tool
            else {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "input_json_delta",
                "partial_json": tool["function"]["arguments"],
            }
            if tool
            else {"type": "text_delta", "text": "Brief submitted."},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": "tool_use" if tool else "end_turn",
                "stop_sequence": None,
            },
            "usage": {"output_tokens": 100, "cost": 0},
        },
        {"type": "message_stop"},
    ]
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)


def main():
    active = {}

    def handler(request):
        if request.method == "GET" and request.url.path.endswith("/endpoints"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "endpoints": [
                            {
                                "supported_parameters": ["tools"],
                                "pricing": {"prompt": "0.00000005", "completion": "0.00000015"},
                            }
                        ]
                    }
                },
            )
        assert str(request.url) == "https://openrouter.ai/api/v1/messages"
        task = active["task"]
        operation = ResearchOperation.objects.filter(task=task, kind="model").latest("created_at")
        call = task.operations.filter(kind="model").count()
        if call <= 2:
            pseudo = {
                "messages": [
                    {"role": "system"},
                    {"role": "user", "content": json.dumps(task.turn.input_snapshot)},
                ]
            }
            if call == 2:
                pseudo["messages"].append({"role": "tool"})
            if task.role == "judge" and call == 2:
                context = task.turn.input_snapshot
                ids = [str(e.pk) for e in task.discovery.evidence.filter(turn=task.turn)]
                artifact = Judgment(
                    reviewed_proposal_id=context["proposal"]["id"],
                    reviewed_proposal_hash=context["proposal"]["hash"],
                    reviewed_validation_id=context["accepted_validation"]["id"],
                    reviewed_validation_hash=context["accepted_validation"]["hash"],
                    verdict="approve",
                    worth_pursuing=True,
                    summary="Synthetic Judge approval; not real market research.",
                    assessment={
                        key: "Synthetic positive fixture assessment only."
                        for key in (
                            "economics",
                            "opportunity_cost",
                            "observed_transaction",
                            "competitive_pressure",
                            "objective_fit",
                            "convergence_risk",
                        )
                    },
                    evidence_ids=ids,
                    next_tests=["Interview real customers before investing."],
                )
                message = {
                    "tool_calls": [_call("submit_judgment", artifact.model_dump(mode="json"))]
                }
            else:
                message = _response(pseudo, operation.pk)["choices"][0]["message"]
        else:
            message = {}
        body = json.loads(request.content)
        return httpx.Response(
            200, text=_stream(message, body["model"]), headers={"content-type": "text/event-stream"}
        )

    transport = httpx.MockTransport(handler)
    original_gateway = Gateway

    def gateway(*args, **kwargs):
        return original_gateway(*args, transport=transport, **kwargs)

    with (
        override_settings(
            OPENROUTER_API_KEY="synthetic-fixture-not-a-key",
            DISCOVERY_CONNECTOR_CLASSES=["scripts.smoke_discovery.FixtureConnector"],
        ),
        httpx.Client(transport=transport) as metadata,
        patch("discovery.claude_runtime.httpx.get", metadata.get),
        patch("discovery.claude_runtime.Gateway", gateway),
        patch("discovery.orchestrator._kick_dispatch"),
    ):
        discovery = start_discovery(
            "SYNTHETIC CLAUDE CODE SMOKE — travel service opportunity",
            config={
                "schema_version": "1.2",
                "runtime": "claude_code",
                "finder_provider": "openrouter",
                "validator_provider": "openrouter",
                "finder_model": "deepseek/deepseek-v4.1-flash",
                "validator_model": "deepseek/deepseek-v4.1-flash",
                "judge_enabled": True,
                "judge_provider": "openrouter",
                "judge_model": "moonshotai/kimi-k3",
                "max_turns": 5,
                "max_duration_s": 240,
                "connectors": ["duckduckgo", "prototype_fixture"],
            },
            constraints="Synthetic evidence and model responses; actual Claude Code agent harness.",
            enqueue=False,
        )
        try:
            for _ in range(5):
                discovery.refresh_from_db()
                if discovery.status == "completed":
                    break
                task = discovery.tasks.get(state="open")
                active["task"] = task
                run_task(str(task.pk))
                print(f"Claude Code turn {task.turn_number} completed", flush=True)
            discovery.refresh_from_db()
            assert discovery.outcome == "converged", discovery.error_summary
            assert discovery.turns_completed == 5
            tasks = list(discovery.tasks.order_by("turn_number"))
            assert tasks[0].context["claude_session_id"] == tasks[2].context["claude_session_id"]
            assert tasks[1].context["claude_session_id"] != tasks[3].context["claude_session_id"]
            assert tasks[4].role == "judge"
            assert tasks[4].context["claude_session_id"] not in {
                task.context["claude_session_id"] for task in tasks[:4]
            }
            assert discovery.judgments.get().verdict == "approve"
            assert discovery.cost_used_usd == 0
            path = Path(".tmp/claude-discovery-smoke-report.md")
            path.write_text(discovery.report_markdown)
            print(
                "PASS: real Claude Code, Judge approval, MCP mail, five turns, no paid calls. "
                f"{path}"
            )
        finally:
            discovery.refresh_from_db()
            if discovery.status in {"queued", "running"}:
                cancel_discovery(discovery.pk)


if __name__ == "__main__":
    main()
