"""Builders for persisted discovery records shaped like the research path writes them."""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import httpx
from django.utils import timezone

from discovery.connectors.base import ResearchPage
from discovery.connectors.http import MAX_RESPONSE_BYTES
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
# Schema 1.1 entries as start_discovery freezes them; PROMPTS stays the stored 1.0 shape.
PROMPTS_1_1 = {
    role: {
        "schema_version": "1.1",
        "content": f"{role} prompt\n\n{role} procedure",
        "hash": f"{role}-effective-hash",
        "path": f"prompts/{role}/system.md",
        "parts": [
            {
                "kind": "system",
                "path": f"prompts/{role}/system.md",
                "hash": f"{role}-hash",
                "schema_version": "1.0",
            },
            {
                "kind": "procedural",
                "path": f"prompts/{role}/procedural.md",
                "hash": f"{role}-procedural-hash",
                "schema_version": "1.0",
            },
        ],
    }
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
    found: int = 0,
) -> ResearchOperation:
    """A completed research operation; ``found`` documents make it count as a result."""
    return ResearchOperation.objects.create(
        discovery=step.discovery,
        task=step.task,
        operation_key=f"op-{step.number}-{ResearchOperation.objects.count()}",
        kind="research",
        connector="duckduckgo",
        state="completed",
        request={"connector": "duckduckgo", "action": action, "query": query},
        response={
            "status": status,
            "request_performed": performed,
            "documents": [{"url": f"https://result{i}.example/"} for i in range(found)],
        },
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


def typed_discovery(policy: str = "typed", **fields: Any) -> tuple[Discovery, dict[str, Evidence]]:
    """A services proposal citing typed, dated evidence under a 1.3 config.

    Demand: forum (dated 900 days) and vendor; competitors: vendor and news
    (dated 30 days); vendor is cited twice. Hand-computed metrics are asserted
    in tests/test_discovery_quality.py.
    """
    config = {**CONFIG, "schema_version": "1.3", "evidence_policy": policy}
    if policy == "typed":
        config["min_validator_query_novelty"] = 0.4
    row = discovery(turns_completed=2, config_snapshot=config, **fields)
    finder = turn(row, 1, "finder")
    sources = {
        "forum": evidence(finder, "reddit.com", source_type="forum", age_days=900),
        "vendor": evidence(
            finder, "acme.com", url="https://acme.com/pricing", source_type="vendor"
        ),
        "news": evidence(finder, "reuters.com", source_type="news", age_days=30),
    }
    final = proposal(
        finder,
        {
            "business_model_kind": "services",
            "demand_evidence_ids": [str(sources["forum"].pk), str(sources["vendor"].pk)],
            "competitor_evidence_ids": [str(sources["vendor"].pk), str(sources["news"].pk)],
        },
    )
    validation(turn(row, 2, "validator"), final, "accept")
    return row, sources


def pdf_bytes(pages: list[str], info: dict[str, str] | None = None) -> bytes:
    """A minimal text PDF; ``info`` entries become the document Info dictionary."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    children = []
    for text in pages:
        number = len(objects) + 1
        children.append(f"{number} 0 R")
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] "
                "/Resources << /Font << /F1 3 0 R >> >> "
                f"/Contents {number + 1} 0 R >>"
            ).encode()
        )
        stream = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode()
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(children)}] /Count {len(pages)} >>".encode()
    info_ref = ""
    if info:
        objects.append(
            ("<< " + " ".join(f"/{key} ({value})" for key, value in info.items()) + " >>").encode()
        )
        info_ref = f" /Info {len(objects)} 0 R"
    raw = b"%PDF-1.4\n"
    offsets = [0]
    for i, value in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += f"{i} 0 obj\n".encode() + value + b"\nendobj\n"
    xref = len(raw)
    raw += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    raw += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    return (
        raw
        + f"trailer\n<< /Size {len(offsets)} /Root 1 0 R{info_ref} >>\n".encode()
        + f"startxref\n{xref}\n%%EOF".encode()
    )


CONNECTOR_FIXTURES = Path(__file__).with_name("fixtures") / "connectors"


def provider_fixture(name: str) -> Any:
    """The provider response body kept in tests/fixtures/connectors/<name>.json."""
    wrapped = json.loads((CONNECTOR_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return wrapped["response"]


class ProviderStub:
    """Stands in for ``httpx.stream`` behind the connector HTTP helper; records each request.

    Replies are served in order: an ``httpx.Response`` is returned, an exception raised.
    """

    def __init__(self, *replies: httpx.Response | Exception) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> contextlib.AbstractContextManager:
        self.requests.append({"method": method, "url": url, **kwargs})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return contextlib.nullcontext(reply)

    def patch(self) -> Any:
        return patch("discovery.connectors.http.httpx.stream", side_effect=self)


def json_reply(
    body: Any, status: int = 200, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode(), headers=headers)


# Every provider behaviour the HTTP helper types, with the status it must yield.
TYPED_FAILURES: list[tuple[str, httpx.Response | Exception, str]] = [
    ("transport", httpx.ConnectError("refused"), "error"),
    ("timeout", httpx.ReadTimeout("slow"), "timeout"),
    ("unauthorized", httpx.Response(401), "access_denied"),
    ("forbidden", httpx.Response(403), "access_denied"),
    ("not_found", httpx.Response(404), "not_found"),
    ("rate_limited", httpx.Response(429, headers={"Retry-After": "30"}), "rate_limited"),
    ("server_error", httpx.Response(503), "error"),
    ("redirect", httpx.Response(302, headers={"Location": "https://elsewhere.example/"}), "error"),
    ("non_json", httpx.Response(200, text="<html>Service unavailable</html>"), "error"),
    ("oversized", httpx.Response(200, content=b" " * (MAX_RESPONSE_BYTES + 1)), "error"),
]


def assert_typed_failures(call: Callable[[], ResearchPage]) -> None:
    """Each provider failure is a typed page from ``call``, never an exception."""
    for name, reply, status in TYPED_FAILURES:
        with ProviderStub(reply).patch():
            page = call()
        assert page.status == status, (name, page)
        assert page.documents == [] and page.error_reason, name
        if name == "rate_limited":
            assert page.retry_after_seconds == 30
