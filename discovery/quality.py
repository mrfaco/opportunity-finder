"""Deterministic discovery quality metrics read from persisted records.

No network, model calls or writes. Metrics observe; acceptance gates enforce.
"""

from __future__ import annotations

import statistics
from decimal import Decimal
from typing import Any

from django.db.models import Q

from discovery.independence import novelty as query_novelty
from discovery.models import Discovery, Evidence, ProposalRevision, ResearchOperation
from discovery.schemas import QualityMetrics
from discovery.termination import business_fingerprint

METRICS_SCHEMA_VERSION = "1.0"
# Evidence older than this at retrieval is reported as stale; it never blocks.
STALE_EVIDENCE_MONTHS = 24
_DAYS_PER_MONTH = 365.25 / 12
# Aggregated as mean/median/min/max/sum over non-None values; booleans as 0/1 shares.
QUANTITATIVE_METRICS = (
    "cited_evidence_count",
    "cited_origin_count",
    "snippet_citation_share",
    "fact_claims_with_quote_rate",
    "claims_with_evidence_rate",
    "published_at_known_share",
    "median_evidence_age_days",
    "validator_query_novelty_min",
    "accepting_turn_query_novelty",
    "validator_new_origin_share",
    "turns_completed",
    "proposal_count",
    "pivot_count",
    "turns_to_first_accept",
    "research_calls",
    "research_failure_share",
    "local_denials",
    "known_cost_usd",
    "estimated_cost_usd",
    "unknown_operations",
    "judge_required",
    "judge_approved",
    "same_model_family",
)
CATEGORICAL_METRICS = ("outcome", "stop_reason", "human_verdict")


def evidence_age_months(evidence: Evidence) -> float | None:
    """Age at retrieval in average-length months; ``None`` when undated."""
    if evidence.published_at is None:
        return None
    return (
        (evidence.retrieved_at - evidence.published_at).total_seconds() / 86400 / (_DAYS_PER_MONTH)
    )


def _share(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _origin(evidence: Evidence) -> str:
    return str(evidence.metadata.get("original_source_origin") or evidence.origin)


def cost_breakdown(discovery: Discovery) -> tuple[Decimal, Decimal, int]:
    """Known spend, estimated charges and operations whose billing outcome is unknown."""
    unknown = discovery.operations.filter(
        Q(state__in=["pending", "started", "unknown"]) | Q(response__cost_unknown=True)
    ).count()
    estimated = sum(
        (
            op.cost_usd
            for op in discovery.operations.filter(state="completed")
            if op.response.get("cost_is_estimate")
        ),
        Decimal("0"),
    )
    return discovery.cost_used_usd - estimated, estimated, unknown


def cited_ids(content: dict[str, Any]) -> set[str]:
    refs = set(content.get("demand_evidence_ids", []))
    refs.update(content.get("competitor_evidence_ids", []))
    for claim in content.get("claims", []):
        refs.update(ref["evidence_id"] for ref in claim.get("evidence", []))
    return refs


def _evidence_metrics(
    discovery: Discovery, final: ProposalRevision | None, notes: list[str]
) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "cited_evidence_count": None,
        "cited_origin_count": None,
        "cited_connector_counts": None,
        "snippet_citation_share": None,
        "fact_claims_with_quote_rate": None,
        "claims_with_evidence_rate": None,
        "published_at_known_share": None,
        "median_evidence_age_days": None,
    }
    if final is None:
        notes.append("No Finder proposal was completed; evidence metrics are not computable.")
        return empty
    content = final.content
    refs = cited_ids(content)
    by_id = {str(row.pk): row for row in discovery.evidence.all()}
    unmatched = refs - by_id.keys()
    if unmatched:
        notes.append(
            f"{len(unmatched)} cited evidence ID(s) do not match captured evidence and are "
            "excluded from evidence metrics."
        )
    cited = [by_id[ref] for ref in sorted(refs & by_id.keys())]
    metrics = dict(empty, cited_evidence_count=len(cited))
    claims = content.get("claims", [])
    facts = [claim for claim in claims if claim.get("kind") == "fact"]
    metrics["claims_with_evidence_rate"] = _share(
        sum(1 for claim in claims if claim.get("evidence")), len(claims)
    )
    if not claims:
        notes.append("The final proposal has no claims; claims_with_evidence_rate is unknown.")
    metrics["fact_claims_with_quote_rate"] = _share(
        sum(
            1
            for claim in facts
            if any(ref.get("quote", "").strip() for ref in claim.get("evidence", []))
        ),
        len(facts),
    )
    if not facts:
        notes.append(
            "The final proposal has no fact claims; fact_claims_with_quote_rate is unknown."
        )
    if not cited:
        notes.append("The final proposal cites no captured evidence; citation shares are unknown.")
        return metrics
    # Exact syndicated copies are one origin, matching evidence_gaps.
    origins: set[str] = set()
    seen_content: set[str] = set()
    for row in cited:
        if row.content_hash in seen_content:
            continue
        seen_content.add(row.content_hash)
        if _origin(row):
            origins.add(_origin(row))
    connectors: dict[str, int] = {}
    for row in cited:
        connectors[row.connector] = connectors.get(row.connector, 0) + 1
    dated = [row for row in cited if row.published_at is not None]
    metrics.update(
        cited_origin_count=len(origins),
        cited_connector_counts=dict(sorted(connectors.items())),
        snippet_citation_share=_share(
            sum(1 for row in cited if row.kind == "search_snippet"), len(cited)
        ),
        published_at_known_share=_share(len(dated), len(cited)),
    )
    if dated:
        metrics["median_evidence_age_days"] = statistics.median(
            (row.retrieved_at - row.published_at).total_seconds() / 86400
            for row in dated
            if row.published_at is not None
        )
    else:
        notes.append(
            "No cited evidence has a known publication date; median_evidence_age_days is unknown."
        )
    return metrics


def _search_ops(discovery: Discovery, role: str) -> list[ResearchOperation]:
    return [
        op
        for op in discovery.operations.filter(kind="research", task__role=role)
        .select_related("task")
        .order_by("task__turn_number", "created_at")
        if op.request.get("action") == "search" and str(op.request.get("query", "")).strip()
    ]


def _independence_metrics(discovery: Discovery, notes: list[str]) -> dict[str, Any]:
    finder = _search_ops(discovery, "finder")
    validator = _search_ops(discovery, "validator")

    def novelty(op: ResearchOperation) -> float:
        earlier = [
            str(row.request["query"])
            for row in finder
            if row.task.turn_number < op.task.turn_number
        ]
        return query_novelty(str(op.request["query"]), earlier)

    metrics: dict[str, Any] = {
        "validator_query_novelty_min": None,
        "accepting_turn_query_novelty": None,
        "validator_new_origin_share": None,
    }
    if validator:
        metrics["validator_query_novelty_min"] = min(novelty(op) for op in validator)
    else:
        notes.append("Validator never searched; query novelty is unknown.")
    accepted = discovery.validations.filter(verdict="accept").order_by("-turn__number").first()
    if accepted is None:
        notes.append("No validation accepted a proposal; accepting_turn_query_novelty is unknown.")
    else:
        accepting = [op for op in validator if op.task_id == accepted.turn.task_id]
        if accepting:
            metrics["accepting_turn_query_novelty"] = min(novelty(op) for op in accepting)
        else:
            notes.append(
                f"The accepting Validator turn {accepted.turn.number} recorded no search; "
                "accepting_turn_query_novelty is unknown."
            )
    first_role: dict[str, str] = {}
    for row in discovery.evidence.select_related("turn").order_by("turn__number", "retrieved_at"):
        if _origin(row):
            first_role.setdefault(_origin(row), row.turn.role if row.turn else "")
    metrics["validator_new_origin_share"] = _share(
        sum(1 for role in first_role.values() if role == "validator"), len(first_role)
    )
    if not first_role:
        notes.append("No evidence origins were captured; validator_new_origin_share is unknown.")
    return metrics


def compute_quality(discovery: Discovery) -> QualityMetrics:
    notes: list[str] = []
    proposals = list(discovery.proposals.select_related("turn").order_by("turn__number"))
    final = proposals[-1] if proposals else None
    final_review = (
        discovery.validations.filter(proposal=final, turn__completed_at__isnull=False)
        .order_by("-turn__number")
        .first()
        if final
        else None
    )
    current_judgment = (
        discovery.judgments.filter(proposal=final, validation=final_review)
        .order_by("-turn__number")
        .first()
        if final and final_review
        else None
    )
    config = discovery.config_snapshot
    judge_required = bool(config.get("judge_enabled", False))
    first_accept = discovery.validations.filter(verdict="accept").order_by("turn__number").first()
    if first_accept is None:
        notes.append("No validation accepted a proposal; turns_to_first_accept is unknown.")
    verdict_counts: dict[str, int] = {}
    for verdict in discovery.judgments.values_list("verdict", flat=True):
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
    research = list(discovery.operations.filter(kind="research"))
    completed = [op for op in research if op.state == "completed"]
    if not completed:
        notes.append("No research operation completed; research_failure_share is unknown.")
    known, estimated, unknown = cost_breakdown(discovery)
    latest_verdict = discovery.verdicts.last()
    return QualityMetrics(
        discovery_id=str(discovery.pk),
        **_evidence_metrics(discovery, final, notes),
        **_independence_metrics(discovery, notes),
        turns_completed=discovery.turns_completed,
        proposal_count=len(proposals),
        pivot_count=sum(
            1
            for previous, current in zip(proposals, proposals[1:], strict=False)
            if business_fingerprint(previous.content) != business_fingerprint(current.content)
        ),
        turns_to_first_accept=first_accept.turn.number if first_accept else None,
        judge_verdict_counts=dict(sorted(verdict_counts.items())),
        research_calls=len(completed),
        research_failure_share=_share(
            sum(1 for op in completed if op.response.get("status") != "success"), len(completed)
        ),
        local_denials=sum(1 for op in research if op.response.get("request_performed") is False),
        known_cost_usd=known,
        estimated_cost_usd=estimated,
        unknown_operations=unknown,
        outcome=discovery.outcome,
        stop_reason=discovery.stop_reason,
        judge_required=judge_required,
        judge_approved=bool(
            current_judgment
            and current_judgment.verdict == "approve"
            and current_judgment.content.get("worth_pursuing")
            and final_review
            and final_review.verdict == "accept"
        ),
        human_verdict=latest_verdict.verdict if latest_verdict else None,
        finder_model=config.get("finder_model"),
        validator_model=config.get("validator_model"),
        judge_model=config.get("judge_model") if judge_required else None,
        same_model_family=bool(
            config.get("finder_model")
            and config.get("finder_model") == config.get("validator_model")
            and config.get("finder_provider") == config.get("validator_provider")
        ),
        prompt_hashes={
            role: str(snapshot["hash"])
            for role, snapshot in sorted(discovery.prompt_snapshot.items())
        },
        notes=notes,
    )


def aggregate_metrics(rows: list[QualityMetrics]) -> dict[str, dict[str, Any]]:
    """Per-metric statistics; ``None`` values are excluded and counted in ``none_count``."""
    result: dict[str, dict[str, Any]] = {}
    for name in QUANTITATIVE_METRICS:
        values = [getattr(row, name) for row in rows]
        present = [float(value) for value in values if value is not None]
        result[name] = {
            "count": len(present),
            "none_count": len(values) - len(present),
            "mean": statistics.fmean(present) if present else None,
            "median": statistics.median(present) if present else None,
            "min": min(present) if present else None,
            "max": max(present) if present else None,
            "sum": sum(present) if present else None,
        }
    for name in CATEGORICAL_METRICS:
        distribution: dict[str, int] = {}
        for row in rows:
            value = getattr(row, name)
            if value is not None:
                distribution[value] = distribution.get(value, 0) + 1
        result[name] = {
            "count": sum(distribution.values()),
            "none_count": len(rows) - sum(distribution.values()),
            "distribution": dict(sorted(distribution.items())),
        }
    return result


def distinct_prompt_hashes(rows: list[QualityMetrics]) -> dict[str, list[str]]:
    hashes: dict[str, set[str]] = {}
    for row in rows:
        for role, value in row.prompt_hashes.items():
            hashes.setdefault(role, set()).add(value)
    return {role: sorted(values) for role, values in sorted(hashes.items())}


def metric_deltas(
    earlier: dict[str, dict[str, Any]], current: dict[str, dict[str, Any]]
) -> list[tuple[str, float | None, float | None, float | None]]:
    """``(metric, earlier mean, current mean, delta)``; delta is ``None`` when either is."""
    rows = []
    for name in QUANTITATIVE_METRICS:
        before = earlier.get(name, {}).get("mean")
        after = current.get(name, {}).get("mean")
        delta = after - before if before is not None and after is not None else None
        rows.append((name, before, after, delta))
    return rows
