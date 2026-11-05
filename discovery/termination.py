"""Deterministic checks for agreement; model confidence is not a stop signal."""

from __future__ import annotations

import json
from typing import Any

from discovery.independence import novelty
from discovery.models import (
    Discovery,
    DiscoveryTask,
    Evidence,
    JudgmentRevision,
    ProposalRevision,
    ValidationRevision,
)
from discovery.schemas import DiscoveryConfig, Judgment, Validation
from discovery.source_types import INDEPENDENT_DEMAND_TYPES, OFFER_TYPES, SourceType

# Kinds whose competition is an actual supplier, offer or price, not commentary.
OFFER_LEVEL_KINDS = frozenset({"services", "manufacturing", "goods", "marketplace"})


class SubmissionError(ValueError):
    """A submitted artifact violates dialogue or evidence requirements.

    ``finder_must_revise`` marks a gap in Finder's immutable proposal that only a
    new Finder revision can fix; ``missing_source_types`` names what would.
    """

    def __init__(
        self,
        message: str,
        *,
        finder_must_revise: bool = False,
        missing_source_types: frozenset[SourceType] = frozenset(),
    ) -> None:
        super().__init__(message)
        self.finder_must_revise = finder_must_revise
        self.missing_source_types = missing_source_types


def proposal_kind(content: dict[str, Any]) -> str:
    """The proposal's business-model kind; ``unspecified`` for contract-1.0 content.

    The only reader of ``business_model_kind``, so stored 1.0 proposals are
    handled in one place and never rewritten.
    """
    return str(content.get("business_model_kind") or "unspecified")


def evidence_references(content: dict[str, Any]) -> set[str]:
    refs = set(content.get("demand_evidence_ids", []))
    refs.update(content.get("competitor_evidence_ids", []))
    refs.update(content.get("evidence_ids", []))
    for claim in content.get("claims", []):
        refs.update(ref["evidence_id"] for ref in claim.get("evidence", []))
    for objection in content.get("objections", []):
        refs.update(objection.get("evidence_ids", []))
    for response in content.get("objection_responses", []):
        refs.update(response.get("evidence_ids", []))
    return refs


def validate_references(discovery: Discovery, content: dict[str, Any]) -> None:
    refs = evidence_references(content)
    by_id = {str(e.pk): e for e in discovery.evidence.all()}
    valid = set(by_id)
    if refs - valid:
        candidates = {}
        for ref in sorted(refs - valid)[:50]:
            matches = [key for key in valid if len(ref) >= 8 and key.startswith(ref[:8])]
            if len(matches) == 1:
                source = by_id[matches[0]]
                candidates[ref] = {
                    "evidence_id": str(source.pk),
                    "title": source.title,
                    "url": source.url,
                }
        raise SubmissionError(
            f"Unknown evidence IDs: {sorted(refs - valid)}. Copy exact full IDs. "
            "Unambiguous source candidates (verify the source before citing): "
            + json.dumps(candidates)
        )
    for claim in content.get("claims", []):
        for ref in claim.get("evidence", []):
            quote = ref.get("quote", "")
            if quote and quote not in by_id[ref["evidence_id"]].content:
                raise SubmissionError("Quoted text does not match captured evidence")


def unresolved_objections(discovery: Discovery) -> dict[str, dict[str, Any]]:
    objections: dict[str, dict[str, Any]] = {}
    events: list[ProposalRevision | ValidationRevision | JudgmentRevision] = []
    events.extend(discovery.proposals.select_related("turn"))
    events.extend(discovery.validations.select_related("turn"))
    events.extend(discovery.judgments.select_related("turn", "proposal"))
    rejection: JudgmentRevision | None = None
    rejected_ids: set[str] = set()
    archived: set[str] = set()
    previous_proposal: ProposalRevision | None = None
    for review in sorted(events, key=lambda row: row.turn.number):
        if isinstance(review, ProposalRevision):
            if previous_proposal and business_fingerprint(review.content) != business_fingerprint(
                previous_proposal.content
            ):
                archived.update(
                    row["objection_id"]
                    for row in review.content.get("objection_responses", [])
                    if row["disposition"] in {"pivot", "abandon"}
                    and row.get("rationale")
                    and row.get("evidence_ids")
                )
            if (
                rejection is not None
                and business_fingerprint(review.content)
                != business_fingerprint(rejection.proposal.content)
                and any(
                    row["disposition"] in {"pivot", "abandon"}
                    for row in review.content.get("objection_responses", [])
                )
            ):
                # Rejected-business objections stay in immutable history. A new
                # business need not prove the abandoned business viable; carried
                # copies of its stable IDs must not resurrect those obligations.
                archived.update(rejected_ids)
            objections = {key: value for key, value in objections.items() if key not in archived}
            previous_proposal = review
            continue
        for objection in review.content.get("objections", []):
            if objection["id"] not in archived:
                objections[objection["id"]] = objection
        for resolved_id in review.content.get("resolved_objection_ids", []):
            if resolved_id in objections:
                objections[resolved_id] = {**objections[resolved_id], "resolved": True}
        if isinstance(review, JudgmentRevision) and review.verdict == "reject":
            rejection = review
            rejected_ids = set(objections)
    return {key: value for key, value in objections.items() if not value.get("resolved")}


def business_fingerprint(content: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        " ".join(str(content.get(key, "")).casefold().split())
        for key in ("target_customer", "buyer", "problem", "offering", "business_model")
    )


def require_judge_feedback(discovery: Discovery, content: dict[str, Any]) -> None:
    latest = discovery.judgments.select_related("proposal").order_by("-turn__number").first()
    if latest is None or latest.verdict == "approve":
        return
    responses = {row["objection_id"]: row for row in content.get("objection_responses", [])}
    unresolved = unresolved_objections(discovery)
    required = {
        objection["id"]
        for objection in latest.content.get("objections", [])
        if objection["id"] in unresolved
    }
    if required - responses.keys():
        raise SubmissionError("Finder must answer every unresolved Judge objection by ID")
    for rejected in discovery.judgments.filter(verdict="reject").select_related("proposal"):
        if business_fingerprint(content) == business_fingerprint(rejected.proposal.content):
            raise SubmissionError(
                "Judge rejected this direction; Finder must propose a new opportunity"
            )
    current = discovery.proposals.order_by("-turn__number").first()
    if latest.verdict == "reject" and current is not None and current.pk == latest.proposal_id:
        if not any(
            row["disposition"] in {"pivot", "abandon"}
            for key, row in responses.items()
            if key in required
        ):
            raise SubmissionError(
                "Judge rejection requires an explicit pivot or abandonment disposition"
            )


def require_judge_approval(
    discovery: Discovery, proposal: ProposalRevision, judgment: Judgment
) -> None:
    for objection in judgment.objections:
        if objection.resolved and objection.severity in {"critical", "major"}:
            if not objection.resolution or not objection.evidence_ids:
                raise SubmissionError("Resolved Judge objections require rationale and evidence")
    if judgment.verdict != "approve":
        return
    for previous in discovery.judgments.filter(verdict="reject").select_related("proposal"):
        if business_fingerprint(proposal.content) == business_fingerprint(
            previous.proposal.content
        ):
            raise SubmissionError("Judge cannot approve an unchanged previously rejected business")
    prior = {
        key: value
        for key, value in unresolved_objections(discovery).items()
        if value.get("severity") in {"critical", "major"}
    }
    resolutions = {
        objection.id: objection for objection in judgment.objections if objection.resolved
    }
    responses = {
        row["objection_id"]: row for row in proposal.content.get("objection_responses", [])
    }
    for key in prior:
        resolution = resolutions.get(key)
        if (
            resolution is None
            or not resolution.resolution
            or not resolution.evidence_ids
            or key not in responses
        ):
            raise SubmissionError(
                f"Material objection {key} needs a Finder disposition "
                "and explicit evidenced Judge resolution"
            )


def _normalized_query(value: str) -> str:
    return " ".join(value.casefold().split())


def _search_novelties(task: DiscoveryTask) -> list[float]:
    """Novelty of each successful, non-identical Validator search with documents."""
    finder_queries = [
        str(op.request.get("query", ""))
        for op in task.discovery.operations.filter(
            kind="research",
            task__role="finder",
            task__turn_number__lt=task.turn_number,
        )
        if op.request.get("action") == "search"
    ]
    finder_normalized = {_normalized_query(query) for query in finder_queries}
    return [
        novelty(str(op.request.get("query", "")), finder_queries)
        for op in task.operations.filter(kind="research", state="completed")
        if op.request.get("action") == "search"
        and _normalized_query(op.request.get("query", ""))
        and _normalized_query(op.request.get("query", "")) not in finder_normalized
        and op.response.get("status") == "success"
        and bool(op.response.get("documents"))
    ]


def _novelty_threshold(discovery: Discovery) -> float:
    return DiscoveryConfig.model_validate(discovery.config_snapshot).min_validator_query_novelty


def independence_shortfall(task: DiscoveryTask) -> str:
    """Why no search counts as independent: best novelty achieved and the threshold."""
    novelties = _search_novelties(task)
    threshold = _novelty_threshold(task.discovery)
    if not novelties:
        return (
            "no successful, non-identical search returned results this turn "
            f"(query novelty threshold {threshold:.2f})"
        )
    return (
        f"best query novelty against earlier Finder searches was {max(novelties):.2f}; "
        f"the threshold is {threshold:.2f}, so rewording Finder's query is not enough"
    )


def has_independent_research(task: DiscoveryTask, *, require_search: bool = True) -> bool:
    threshold = _novelty_threshold(task.discovery)
    successful_search = any(value >= threshold for value in _search_novelties(task))
    if successful_search or require_search:
        return successful_search
    # A challenge can independently inspect a new source while search is down.
    # Acceptance still requires a fresh successful search, never a reused fetch.
    finder_urls = set(
        task.discovery.evidence.filter(
            turn__role="finder", turn__number__lt=task.turn_number
        ).values_list("url", flat=True)
    )
    return any(
        op.request.get("action") == "fetch"
        and op.response.get("status") == "success"
        and any(
            doc.get("kind") == "fetched_page"
            and doc.get("url") not in finder_urls
            and bool(doc.get("text"))
            for doc in op.response.get("documents", [])
        )
        for op in task.operations.filter(kind="research", state="completed")
    )


def _cited_types(ids: list[str], by_id: dict[str, Evidence]) -> str:
    return ", ".join(
        f"{ref}: {by_id[ref].source_type if ref in by_id else 'unknown'}"
        for ref in dict.fromkeys(ids)
    )


def _typed_gaps(
    content: dict[str, Any], evidence: list[Evidence]
) -> list[tuple[str, frozenset[SourceType]]]:
    by_id = {str(row.pk): row for row in evidence}
    demand = list(content.get("demand_evidence_ids", []))
    competitors = list(content.get("competitor_evidence_ids", []))
    gaps: list[tuple[str, frozenset[SourceType]]] = []
    if not any(
        ref in by_id and by_id[ref].source_type in INDEPENDENT_DEMAND_TYPES for ref in demand
    ):
        gaps.append(
            (
                "Demand evidence needs one independent source (forum, review, job post, "
                "tender, regulator, statistics, news or marketplace listing); cited demand "
                f"sources are: {_cited_types(demand, by_id)}",
                INDEPENDENT_DEMAND_TYPES,
            )
        )
    kind = proposal_kind(content)
    if kind in OFFER_LEVEL_KINDS and not any(
        ref in by_id and by_id[ref].source_type in OFFER_TYPES for ref in competitors
    ):
        gaps.append(
            (
                f"Competitor evidence for a {kind} business needs one offer-level source "
                "(vendor page, marketplace listing, job post, tender or review) about an "
                "actual supplier, offer or price; cited competitor sources are: "
                f"{_cited_types(competitors, by_id)}",
                OFFER_TYPES,
            )
        )
    return gaps


def _gap_details(
    discovery: Discovery, proposal: ProposalRevision
) -> list[tuple[str, frozenset[SourceType]]]:
    content = proposal.content
    demand = set(content.get("demand_evidence_ids", []))
    competitors = set(content.get("competitor_evidence_ids", []))
    if not demand or not competitors:
        return [("Cited demand and competitor evidence are both required", frozenset())]
    evidence = list(discovery.evidence.filter(pk__in=demand | competitors))
    typed = (
        _typed_gaps(content, evidence)
        if DiscoveryConfig.model_validate(discovery.config_snapshot).evidence_policy == "typed"
        else []
    )
    usable = [e for e in evidence if e.kind != "search_snippet"]
    usable_ids = {str(e.pk) for e in usable}
    if not demand & usable_ids or not competitors & usable_ids:
        return [
            ("Search snippets alone cannot establish demand or competition", frozenset()),
            *typed,
        ]
    # Exact syndicated copies do not become independent evidence merely because
    # they were republished on another domain.
    origins: set[str] = set()
    seen_content: set[str] = set()
    for evidence_item in usable:
        if evidence_item.content_hash in seen_content:
            continue
        seen_content.add(evidence_item.content_hash)
        if evidence_item.origin:
            origins.add(
                str(evidence_item.metadata.get("original_source_origin") or evidence_item.origin)
            )
    if len(origins) < 2:
        return [("Two distinct source origins are required", frozenset()), *typed]
    return typed


def evidence_gaps(discovery: Discovery, proposal: ProposalRevision) -> list[str]:
    """Evidence requirements the proposal misses under the run's frozen evidence policy."""
    return [message for message, _ in _gap_details(discovery, proposal)]


def require_agreement(
    discovery: Discovery,
    task: DiscoveryTask,
    proposal: ProposalRevision,
    validation: Validation,
) -> None:
    content = proposal.content
    prior_critical = {
        key: value
        for key, value in unresolved_objections(discovery).items()
        if value.get("severity") == "critical"
    }
    resolutions = set(validation.resolved_objection_ids) | {
        objection.id for objection in validation.objections if objection.resolved
    }
    responses = {r["objection_id"]: r for r in content.get("objection_responses", [])}
    for objection in validation.objections:
        if (
            objection.id in prior_critical
            and objection.severity != "critical"
            and objection.id not in resolutions
        ):
            raise SubmissionError(
                "An unresolved critical objection cannot be downgraded: "
                f"{objection.id}. Retain its critical severity in a challenge, "
                "or provide an evidenced resolution."
            )
        if objection.severity == "critical" and objection.resolved:
            if not objection.resolution or not objection.evidence_ids:
                raise SubmissionError("Resolved critical objections need rationale and evidence")
    for objection_id in resolutions & prior_critical.keys():
        response = responses.get(objection_id)
        if not response or not response.get("evidence_ids"):
            raise SubmissionError(
                "Critical objection resolution requires a cited Finder disposition"
            )
    if validation.verdict != "accept":
        return
    gaps = _gap_details(discovery, proposal)
    if gaps:
        # Only a new Finder revision can change the cited evidence.
        raise SubmissionError(
            "; ".join(message for message, _ in gaps),
            finder_must_revise=True,
            missing_source_types=frozenset().union(*(types for _, types in gaps)),
        )
    if not has_independent_research(task):
        raise SubmissionError(
            "Validator must independently search before accepting: " + independence_shortfall(task)
        )
    open_critical = {
        key: value
        for key, value in unresolved_objections(discovery).items()
        if value.get("severity") == "critical"
    }
    resolved_ids = resolutions
    for objection_id in open_critical:
        response = responses.get(objection_id)
        if objection_id not in resolved_ids or not response or not response.get("evidence_ids"):
            raise SubmissionError(
                f"Critical objection {objection_id} needs a cited disposition "
                "and Validator resolution"
            )
