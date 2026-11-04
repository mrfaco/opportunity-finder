"""Deterministic checks for agreement; model confidence is not a stop signal."""

from __future__ import annotations

import json
from typing import Any

from discovery.models import (
    Discovery,
    DiscoveryTask,
    JudgmentRevision,
    ProposalRevision,
    ValidationRevision,
)
from discovery.schemas import Judgment, Validation


class SubmissionError(ValueError):
    """A submitted artifact violates dialogue or evidence requirements."""


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


def has_independent_research(task: DiscoveryTask, *, require_search: bool = True) -> bool:
    finder_queries = {
        _normalized_query(op.request.get("query", ""))
        for op in task.discovery.operations.filter(
            kind="research",
            task__role="finder",
            task__turn_number__lt=task.turn_number,
        )
        if op.request.get("action") == "search"
    }
    successful_search = any(
        op.request.get("action") == "search"
        and _normalized_query(op.request.get("query", ""))
        and _normalized_query(op.request.get("query", "")) not in finder_queries
        and op.response.get("status") == "success"
        and bool(op.response.get("documents"))
        for op in task.operations.filter(kind="research", state="completed")
    )
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


def evidence_gaps(discovery: Discovery, proposal: ProposalRevision) -> list[str]:
    content = proposal.content
    demand = set(content.get("demand_evidence_ids", []))
    competitors = set(content.get("competitor_evidence_ids", []))
    if not demand or not competitors:
        return ["Cited demand and competitor evidence are both required"]
    evidence = list(discovery.evidence.filter(pk__in=demand | competitors))
    usable = [e for e in evidence if e.kind != "search_snippet"]
    usable_ids = {str(e.pk) for e in usable}
    if not demand & usable_ids or not competitors & usable_ids:
        return ["Search snippets alone cannot establish demand or competition"]
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
    return [] if len(origins) >= 2 else ["Two distinct source origins are required"]


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
    gaps = evidence_gaps(discovery, proposal)
    if gaps:
        raise SubmissionError("; ".join(gaps))
    if not has_independent_research(task):
        raise SubmissionError("Validator must independently search before accepting")
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
