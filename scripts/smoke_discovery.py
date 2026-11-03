"""Small offline prototype demo with real PostgreSQL and scripted batch responses.

Run `python scripts/smoke_discovery.py`. Provider traffic is intercepted with
MockTransport. The clearly labelled fixture and Markdown report remain available
for inspecting the prototype; no paid provider requests are made.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import django
import httpx
from django.test import override_settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from discovery.connectors.base import (  # noqa: E402  # models require Django initialization
    Connector,
    ConnectorDescription,
    ResearchDocument,
    ResearchPage,
)
from discovery.loop import run_task  # noqa: E402
from discovery.models import ResearchOperation  # noqa: E402
from discovery.orchestrator import start_discovery  # noqa: E402
from discovery.schemas import Proposal, Validation  # noqa: E402


class FixtureConnector(Connector):
    id = "prototype_fixture"

    def describe(self):
        return ConnectorDescription(
            id=self.id,
            name="Synthetic smoke evidence",
            capabilities=["search"],
            minimum_interval_seconds=0,
        )

    def search(self, query, filters=None, cursor=None, limit=5):
        self.validate_search(query, filters, cursor, limit)
        return ResearchPage(
            documents=[
                ResearchDocument(
                    connector=self.id,
                    external_id=f"{query}:{origin}",
                    url=f"https://{origin}/synthetic-evidence",
                    title=f"Synthetic {kind} fixture",
                    text=f"Synthetic fixture for smoke verification only: {kind} about {query}.",
                    kind="source_record",
                    metadata={"synthetic": True},
                )
                for origin, kind in [("example.com", "demand"), ("example.org", "alternatives")]
            ]
        )


def _call(name, arguments):
    return {
        "id": f"call-{name}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


def _response(body, operation_id):
    context = json.loads(body["messages"][1]["content"])
    task = ResearchOperation.objects.get(pk=operation_id).task
    researched = any(message["role"] == "tool" for message in body["messages"])
    if not researched:
        call = _call(
            "research",
            {
                "connector": "prototype_fixture",
                "action": "search",
                "query": f"{context['role']} turn {context['turn_number']} independent research",
            },
        )
    else:
        ids = [str(e.pk) for e in task.discovery.evidence.filter(turn=task.turn)]
        if task.role == "finder":
            revised = context["turn_number"] > 1
            artifact = Proposal(
                title="Synthetic airport luggage repair service"
                if revised
                else "Synthetic travel service",
                target_customer="Travellers with damaged luggage",
                buyer="Traveller",
                problem="Luggage damage disrupts onward travel",
                offering="Same-day luggage repair",
                business_model="Repair service",
                customer_relationship="B2C",
                revenue_hypothesis="One-time repair fee",
                delivery_model="Mobile repair technician",
                distribution_hypothesis="Hotel referrals",
                summary="Synthetic fixture, not market research.",
                demand_evidence_ids=ids[:1],
                competitor_evidence_ids=ids[1:],
                parent_revision_id=context["proposal"]["id"] if revised else None,
                objection_responses=[
                    {
                        "objection_id": "saturation",
                        "disposition": "narrow",
                        "rationale": "Narrowed to airport arrivals; synthetic fixture only.",
                        "evidence_ids": ids,
                    }
                ]
                if revised
                else [],
                next_tests=["Interview travellers before investing."],
            )
            call = _call("submit_proposal", artifact.model_dump(mode="json"))
        else:
            accepted = context["turn_number"] >= 4
            artifact = Validation(
                reviewed_proposal_id=context["proposal"]["id"],
                reviewed_proposal_hash=context["proposal"]["hash"],
                verdict="accept" if accepted else "challenge",
                summary="Synthetic agreement on narrower service."
                if accepted
                else "Synthetic saturation objection; test an airport niche.",
                falsification_checks=["Independently searched fixture demand and alternatives."],
                evidence_ids=ids,
                resolved_objection_ids=["saturation"] if accepted else [],
                objections=[]
                if accepted
                else [
                    {
                        "id": "saturation",
                        "severity": "critical",
                        "text": "Broad travel service is saturated (synthetic fixture).",
                        "evidence_ids": ids,
                    }
                ],
                counterproposal=None if accepted else "Airport-arrival luggage repair niche.",
                next_tests=["Test willingness to pay with real customers."],
            )
            call = _call("submit_validation", artifact.model_dump(mode="json"))
    return {
        "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call]}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 100},
    }


def main():
    batches = {}

    def handler(request):
        if request.url.path.endswith("/endpoints"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "endpoints": [
                            {
                                "tag": "sail-research/fp4",
                                "supported_parameters": ["tools"],
                                "pricing": {"prompt": "0.00000005", "completion": "0.00000015"},
                                "context_length": 1_000_000,
                            }
                        ]
                    }
                },
            )
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["endpoint"] == "/v1/chat/completions"
            assert payload["model"] == "deepseek/deepseek-v4.1-flash"
            batch_id = f"fixture-batch-{len(batches)}"
            batches[batch_id] = payload["requests"][0]
            return httpx.Response(202, json={"id": batch_id, "status": "in_progress"})
        item = batches[request.url.path.rsplit("/", 1)[1]]
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"cost": 0},
                "results": [
                    {
                        "custom_id": item["custom_id"],
                        "response": {
                            "status_code": 200,
                            "body": _response(item["body"], item["custom_id"]),
                        },
                    }
                ],
            },
        )

    with (
        override_settings(
            OPENROUTER_API_KEY="synthetic-fixture-not-a-key",
            DISCOVERY_CONNECTOR_CLASSES=["scripts.smoke_discovery.FixtureConnector"],
        ),
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        patch("discovery.runtime.httpx.get", client.get),
        patch("discovery.runtime.httpx.post", client.post),
        patch("discovery.orchestrator._kick_dispatch"),
        patch("discovery.tasks.run_discovery_turn.apply_async"),
    ):
        discovery = start_discovery(
            "SYNTHETIC SMOKE FIXTURE — travel service opportunity",
            constraints="All evidence and responses are fixtures, not actual market research.",
            config={
                "schema_version": "1.0",
                "runtime": "openrouter_batch",
                "finder_model": "deepseek/deepseek-v4.1-flash:batch",
                "validator_model": "deepseek/deepseek-v4.1-flash:batch",
                "max_turns": 4,
                "connectors": ["duckduckgo", "prototype_fixture"],
                "max_cost_usd": Decimal("1"),
            },
            enqueue=False,
        )
        for _ in range(24):
            discovery.refresh_from_db()
            if discovery.status in {"completed", "failed", "cancelled"}:
                break
            task = discovery.tasks.get(state="open")
            run_task(str(task.pk))
        discovery.refresh_from_db()
        assert discovery.status == "completed", discovery.error_summary
        assert discovery.outcome == "converged"
        assert discovery.turns_completed == 4
        assert discovery.operations.filter(kind="model").count() == 8
        assert not discovery.tasks.filter(state__in=["open", "working"]).exists()
        assert discovery.cost_used_usd == 0
        target = Path(".tmp/discovery-smoke-report.md")
        target.parent.mkdir(exist_ok=True)
        target.write_text(discovery.report_markdown)
        print(f"PASS: 4 turns, challenge/pivot/agreement, 8 async batches, no paid calls. {target}")
        print(f"Admin: /admin/discovery/discovery/{discovery.pk}/review/")


if __name__ == "__main__":
    main()
