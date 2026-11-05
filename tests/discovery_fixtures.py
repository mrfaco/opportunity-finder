"""Builders for persisted discovery records shaped like the research path writes them."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from django.utils import timezone

from discovery.models import (
    Discovery,
    DiscoveryTask,
    DiscoveryTurn,
    Evidence,
    JudgmentRevision,
    ProposalRevision,
    ResearchOperation,
    ValidationRevision,
)

RETRIEVED = timezone.now().replace(microsecond=0)
PROMPTS = {
    role: {"content": f"{role} prompt", "hash": f"{role}-hash", "path": f"prompts/{role}.md"}
    for role in ("finder", "validator", "judge")
}
CONFIG = {
    "schema_version": "1.2",
    "runtime": "claude_code",
    "judge_enabled": True,
    "finder_provider": "openrouter",
    "validator_provider": "openrouter",
    "judge_provider": "ollama",
    "finder_model": "deepseek/deepseek-v4.1-flash",
    "validator_model": "deepseek/deepseek-v4.1-flash",
    "judge_model": "kimi-k3:cloud",
    "max_cost_usd": "10.00",
    "connectors": ["search_auto", "web_fetch"],
}


def discovery(**fields: Any) -> Discovery:
    values: dict[str, Any] = {
        "objective": "Find a service business",
        "request_hash": "fixture",
        "status": "completed",
        "outcome": "converged",
        "stop_reason": "judge_approved",
        "config_snapshot": dict(CONFIG),
        "prompt_snapshot": PROMPTS,
        "ended_at": RETRIEVED,
    }
    values.update(fields)
    return Discovery.objects.create(**values)


def turn(row: Discovery, number: int, role: str, submission: dict | None = None) -> DiscoveryTurn:
    task = DiscoveryTask.objects.create(discovery=row, role=role, turn_number=number, state="done")
    return DiscoveryTurn.objects.create(
        discovery=row,
        task=task,
        number=number,
        role=role,
        submission=submission or {"summary": f"{role} turn {number}"},
        completed_at=RETRIEVED,
    )


def research_task(
    max_calls: int = 8, connectors: list[str] | None = None, **config: Any
) -> DiscoveryTask:
    """A leased Finder task ready for ``perform_research``."""
    row = Discovery.objects.create(
        objective="test sources",
        request_hash=str(uuid4()),
        config_snapshot={
            "connectors": connectors or ["search_auto", "web_fetch"],
            "max_research_calls_per_turn": max_calls,
            **config,
        },
    )
    task = DiscoveryTask.objects.create(
        discovery=row,
        role="finder",
        turn_number=1,
        state="working",
        lease_token=uuid4(),
        lease_expires_at=timezone.now() + timedelta(minutes=3),
    )
    DiscoveryTurn.objects.create(discovery=row, task=task, role="finder", number=1)
    return task


def search(
    step: DiscoveryTurn,
    query: str,
    *,
    status: str = "success",
    action: str = "search",
    performed: bool = True,
) -> ResearchOperation:
    return ResearchOperation.objects.create(
        discovery=step.discovery,
        task=step.task,
        operation_key=f"op-{step.number}-{ResearchOperation.objects.count()}",
        kind="research",
        connector="duckduckgo",
        state="completed",
        request={"connector": "duckduckgo", "action": action, "query": query},
        response={"status": status, "request_performed": performed, "documents": []},
        completed_at=RETRIEVED,
    )


def evidence(
    step: DiscoveryTurn,
    origin: str,
    *,
    kind: str = "fetched_page",
    connector: str = "web_fetch",
    text: str | None = None,
    age_days: int | None = None,
    source_type: str = "unclassified",
    url: str | None = None,
) -> Evidence:
    content = text or f"Source text from {origin}"
    return Evidence.objects.create(
        discovery=step.discovery,
        turn=step,
        connector=connector,
        external_id=origin,
        url=url or f"https://{origin}/page",
        title=origin,
        content=content,
        kind=kind,
        origin=origin,
        source_type=source_type,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        published_at=RETRIEVED - timedelta(days=age_days) if age_days is not None else None,
        retrieved_at=RETRIEVED,
    )


def proposal(step: DiscoveryTurn, content: dict[str, Any]) -> ProposalRevision:
    body = {"title": "Proposal", "summary": "Summary", **content}
    step.submission = body
    step.save(update_fields=["submission"])
    return ProposalRevision.objects.create(
        discovery=step.discovery, turn=step, content=body, content_hash=str(step.number)
    )


def validation(step: DiscoveryTurn, target: ProposalRevision, verdict: str) -> ValidationRevision:
    return ValidationRevision.objects.create(
        discovery=step.discovery,
        turn=step,
        proposal=target,
        verdict=verdict,
        content={"summary": f"Validator {verdict}", "verdict": verdict},
        content_hash=str(step.number),
    )


def judgment(
    step: DiscoveryTurn, target: ValidationRevision, verdict: str = "approve"
) -> JudgmentRevision:
    return JudgmentRevision.objects.create(
        discovery=step.discovery,
        turn=step,
        proposal=target.proposal,
        validation=target,
        verdict=verdict,
        content={"summary": f"Judge {verdict}", "worth_pursuing": verdict == "approve"},
        content_hash=str(step.number),
    )


def _ref(source: Evidence, quote: str) -> dict[str, str]:
    return {
        "evidence_id": str(source.pk),
        "locator": "",
        "relationship": "supports",
        "quote": quote,
    }


FINDER_QUERY = "bookkeeping outsourcing demand small business argentina monthly price survey 2026"
REWORDED_QUERY = "bookkeeping outsourcing demand small business argentina monthly price study 2026"
DISTINCT_QUERY = "monotributo accountant fee cordoba"


def evidenced_discovery(**fields: Any) -> tuple[Discovery, dict[str, Evidence]]:
    """Two businesses, a pivot, a reworded and a distinct Validator search, Judge approval.

    Hand-computed metrics are asserted in tests/test_discovery_quality.py.
    """
    row = discovery(turns_completed=5, cost_used_usd=Decimal("1.5"), **fields)
    t1 = turn(row, 1, "finder")
    search(t1, FINDER_QUERY)
    e1 = evidence(t1, "a.example", text="Shared syndicated text", age_days=10)
    e2 = evidence(t1, "b.example", kind="search_snippet", connector="duckduckgo")
    first = proposal(
        t1, {"target_customer": "Retailers", "buyer": "Owner", "offering": "Bookkeeping"}
    )
    t2 = turn(row, 2, "validator")
    search(t2, REWORDED_QUERY)
    e3 = evidence(t2, "c.example", connector="bing", age_days=30)
    validation(t2, first, "challenge")
    t3 = turn(row, 3, "finder")
    e4 = evidence(t3, "d.example", text="Shared syndicated text")
    final = proposal(
        t3,
        {
            "target_customer": "Monotributistas",
            "buyer": "Sole trader",
            "offering": "Tax filing retainer",
            "demand_evidence_ids": [str(e1.pk)],
            "competitor_evidence_ids": [str(e2.pk), str(e4.pk)],
            "claims": [
                {
                    "id": "c1",
                    "kind": "fact",
                    "text": "Traders pay monthly.",
                    "evidence": [_ref(e1, "Shared syndicated")],
                },
                {
                    "id": "c2",
                    "kind": "fact",
                    "text": "Fees vary by city.",
                    "evidence": [_ref(e3, "")],
                },
                {"id": "c3", "kind": "hypothesis", "text": "Referrals work.", "evidence": []},
            ],
        },
    )
    t4 = turn(row, 4, "validator")
    search(t4, DISTINCT_QUERY)
    search(t4, "", action="fetch", status="rate_limited", performed=False)
    e5 = evidence(t4, "e.example")
    accepted = validation(t4, final, "accept")
    t5 = turn(row, 5, "judge")
    judgment(t5, accepted, "approve")
    ResearchOperation.objects.create(
        discovery=row,
        task=t5.task,
        operation_key="model-estimate",
        kind="model",
        state="completed",
        cost_usd=Decimal("0.5"),
        response={"cost_is_estimate": True},
    )
    ResearchOperation.objects.create(
        discovery=row, task=t5.task, operation_key="model-unknown", kind="model", state="unknown"
    )
    return row, {"e1": e1, "e2": e2, "e3": e3, "e4": e4, "e5": e5}
