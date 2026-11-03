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
from scripts.smoke_discovery import _response  # noqa: E402


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
                "max_turns": 4,
                "max_duration_s": 180,
                "connectors": ["duckduckgo", "prototype_fixture"],
            },
            constraints="Synthetic evidence and model responses; actual Claude Code agent harness.",
            enqueue=False,
        )
        try:
            for _ in range(4):
                discovery.refresh_from_db()
                if discovery.status == "completed":
                    break
                task = discovery.tasks.get(state="open")
                active["task"] = task
                run_task(str(task.pk))
                print(f"Claude Code turn {task.turn_number} completed", flush=True)
            discovery.refresh_from_db()
            assert discovery.outcome == "converged", discovery.error_summary
            assert discovery.turns_completed == 4
            tasks = list(discovery.tasks.order_by("turn_number"))
            assert tasks[0].context["claude_session_id"] == tasks[2].context["claude_session_id"]
            assert tasks[1].context["claude_session_id"] != tasks[3].context["claude_session_id"]
            assert discovery.cost_used_usd == 0
            path = Path(".tmp/claude-discovery-smoke-report.md")
            path.write_text(discovery.report_markdown)
            print(f"PASS: real Claude Code, MCP research/mail, four turns, no paid calls. {path}")
        finally:
            discovery.refresh_from_db()
            if discovery.status in {"queued", "running"}:
                cancel_discovery(discovery.pk)


if __name__ == "__main__":
    main()
