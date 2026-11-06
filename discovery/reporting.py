"""Readable reports derived from committed artifacts, without an extra model call."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from django.db.models import Q

from discovery.models import Discovery, DiscoveryVerdict
from discovery.quality import (
    STALE_EVIDENCE_MONTHS,
    cited_ids,
    compute_quality,
    evidence_age_months,
)
from discovery.schemas import QualityMetrics, Report, evidence_settings
from discovery.termination import evidence_gaps, proposal_kind, unresolved_objections

OUTCOME_LABELS = {
    "converged": "Agents reached agreement",
    "not_converged": "Did not converge",
    "inconclusive": "Insufficient evidence or incomplete validation",
}


def _bullets(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values) or "- None recorded."


def _distinct_text(values: list[str]) -> list[str]:
    unique: dict[str, str] = {}
    for value in values:
        unique.setdefault(" ".join(value.casefold().split()), value.strip())
    return list(unique.values())


def _current_claims(content: dict[str, Any]) -> list[dict[str, Any]]:
    claims: dict[tuple[str, str], dict[str, Any]] = {}
    for claim in content.get("claims", []):
        key = (claim["kind"], " ".join(claim["text"].casefold().split()))
        if key not in claims:
            claims[key] = {**claim, "evidence": []}
        claims[key]["evidence"].extend(claim.get("evidence", []))
    for claim in claims.values():
        claim["evidence"] = list(
            {json.dumps(ref, sort_keys=True): ref for ref in claim["evidence"]}.values()
        )
    return list(claims.values())


def _measure(value: float | int | None, *, percent: bool = False, digits: int = 2) -> str:
    if value is None:
        return "unknown"
    return f"{value:.0%}" if percent else f"{value:.{digits}f}"


def _published(row: dict[str, Any]) -> str:
    if not row["published_at"]:
        return "published unknown"
    source = row["published_at_source"] or "source not recorded"
    if source == "http_last_modified":
        source += ", weak: server modification time"
    age = row["age_months"]
    age_text = f"; age {age:.0f} months at retrieval" if age is not None else ""
    return f"published {row['published_at'][:10]} ({source}){age_text}"


def _verdict_line(verdict: DiscoveryVerdict) -> str:
    line = (
        f"**Human verdict: {verdict.verdict}** "
        f"({verdict.recorded_at.date().isoformat()}, via {verdict.recorded_via})"
    )
    return f"{line} — {verdict.notes}" if verdict.notes else line


def _yes_no(value: bool | None) -> str:
    return "unknown" if value is None else ("yes" if value else "no")


def _evidence_quality(quality: QualityMetrics, verdict: DiscoveryVerdict | None) -> list[str]:
    connectors = ", ".join(
        f"{name} {count}" for name, count in (quality.cited_connector_counts or {}).items()
    )
    source_types = ", ".join(
        f"{name} {count}" for name, count in (quality.cited_source_type_counts or {}).items()
    )
    age = (
        f"; median age when known: {quality.median_evidence_age_days:.0f} days"
        if quality.median_evidence_age_days is not None
        else ""
    )
    lines = [
        "### Evidence quality",
        "",
        "Measured from the stored records; these figures observe and do not gate the run.",
        "",
        f"- Cited sources: {_measure(quality.cited_evidence_count, digits=0)} from "
        f"{_measure(quality.cited_origin_count, digits=0)} distinct origins"
        + (f" ({connectors})." if connectors else "."),
        f"- Search snippets among cited sources: "
        f"{_measure(quality.snippet_citation_share, percent=True)}.",
        f"- Fact claims quoting their source: "
        f"{_measure(quality.fact_claims_with_quote_rate, percent=True)}; claims citing any "
        f"evidence: {_measure(quality.claims_with_evidence_rate, percent=True)}.",
        f"- Cited sources with a known publication date: "
        f"{_measure(quality.published_at_known_share, percent=True)}{age}; older than "
        f"{STALE_EVIDENCE_MONTHS} months at retrieval: "
        f"{_measure(quality.stale_cited_share, percent=True)} of dated sources.",
        f"- Cited source types: {source_types or 'unknown'}.",
        f"- Transaction signal cited (job post, tender or marketplace listing): "
        f"{_yes_no(quality.transaction_signal_cited)}; cited sources from specialist "
        f"connectors: {_measure(quality.specialist_connector_share, percent=True)}.",
        f"- Demand citations from an independent source type: "
        f"{_measure(quality.demand_independent_source_share, percent=True)}; competitor "
        f"citations at offer level: {_measure(quality.competitor_offer_source_share, percent=True)}"
        f" (evidence policy {quality.evidence_policy}).",
        f"- Validator query novelty against earlier Finder queries (1 = no shared terms): "
        f"lowest {_measure(quality.validator_query_novelty_min)}, accepting turn "
        f"{_measure(quality.accepting_turn_query_novelty)}.",
        f"- Source origins first found by Validator: "
        f"{_measure(quality.validator_new_origin_share, percent=True)}.",
        f"- {_verdict_line(verdict)}" if verdict else "- No human verdict recorded.",
    ]
    if quality.notes:
        lines.extend(["", "Unknown measures:", "", _bullets(quality.notes)])
    return [*lines, ""]


def render_report(discovery: Discovery) -> tuple[str, dict[str, Any]]:
    proposals = list(discovery.proposals.order_by("turn__number"))
    final = proposals[-1] if proposals else None
    final_review = (
        discovery.validations.filter(proposal=final, turn__completed_at__isnull=False)
        .order_by("-turn__number")
        .first()
        if final
        else None
    )
    judgments = list(discovery.judgments.select_related("turn").order_by("turn__number"))
    current_judgment = next(
        (
            row
            for row in reversed(judgments)
            if final
            and final_review
            and row.proposal_id == final.pk
            and row.validation_id == final_review.pk
        ),
        None,
    )
    judge_required = bool(discovery.config_snapshot.get("judge_enabled", False))
    judge_approved = bool(
        current_judgment
        and current_judgment.verdict == "approve"
        and current_judgment.content.get("worth_pursuing")
        and final_review
        and final_review.verdict == "accept"
    )
    judgment_rows = [
        {
            "id": str(row.pk),
            "turn_number": row.turn.number,
            "proposal_id": str(row.proposal_id),
            "validation_id": str(row.validation_id),
            "content": row.content,
        }
        for row in judgments
    ]
    turns = list(discovery.turns.filter(completed_at__isnull=False))
    evidence = list(discovery.evidence.all())
    unsettled = list(
        discovery.operations.filter(
            Q(state__in=["pending", "started", "unknown"]) | Q(response__cost_unknown=True)
        )
    )
    unknown = len(unsettled)
    estimated = sum(
        (
            op.cost_usd
            for op in discovery.operations.filter(state="completed")
            if op.response.get("cost_is_estimate")
        ),
        Decimal("0"),
    )
    known = discovery.cost_used_usd - estimated
    unresolved = list(unresolved_objections(discovery).values())
    limitations = [
        "Agent agreement and Judge approval are not proof of demand or commercial viability.",
        "Customer interviews and real purchasing tests remain necessary.",
        "Research covers the enabled connectors and retrieved sources, not the entire market.",
    ]
    config = discovery.config_snapshot
    run_config = evidence_settings(config)
    if judge_required and not judge_approved:
        limitations.append(
            "The latest candidate has not received Judge approval. "
            "Finder–Validator agreement alone is not final approval."
        )
    if (
        config.get("finder_model")
        and config.get("finder_model") == config.get("validator_model")
        and config.get("finder_provider") == config.get("validator_provider")
    ):
        limitations.append(
            "Finder and Validator used the same provider and model. Separate sessions do not "
            "remove shared model blind spots; consider a different Validator model."
        )
    if any(
        op.response.get("cloud_charges_unverified")
        or (
            op.request.get("provider") == "ollama"
            and op.request.get("model", "").endswith(":cloud")
        )
        for op in discovery.operations.all()
    ):
        limitations.append(
            "Ollama Cloud charges are token-based estimates at published rates, not verified "
            "account deductions. Recorded zeroes do not establish free cloud usage."
        )
        limitations.append(
            "Ollama Cloud's spending limit is checked between agent wakes. "
            "A multi-request wake can exceed it before usage is returned; "
            "this is not a provider-enforced billing cap."
        )
    if unknown:
        limitations.append(
            "Local cancellation or termination does not establish the billing outcome of "
            "outstanding remote work. Reconcile the listed operation/provider IDs."
        )
        limitations.append(
            f"{unknown} operation(s) have unknown billing outcomes; spend is incomplete."
        )
    predecessors = sorted(
        {
            str(turn.input_snapshot["restored_from_discovery"])
            for turn in turns
            if turn.input_snapshot.get("restored_from_discovery")
        }
    )
    if predecessors:
        limitations.append(
            "Cost totals cover this discovery only. Original model work in predecessor "
            "discoveries is not duplicated or included here: " + ", ".join(predecessors) + "."
        )
    if final:
        limitations.extend(evidence_gaps(discovery, final))
        cited = cited_ids(final.content)
        stale = [
            (row, age)
            for row in evidence
            if str(row.pk) in cited
            and (age := evidence_age_months(row)) is not None
            and age > STALE_EVIDENCE_MONTHS
        ]
        if stale:
            limitations.append(
                f"Cited evidence older than {STALE_EVIDENCE_MONTHS} months at retrieval "
                "(an observation; it did not block acceptance): "
                + "; ".join(
                    f"`{row.pk}` {row.title or row.url} ({age:.0f} months)" for row, age in stale
                )
                + "."
            )
    if discovery.error_summary:
        limitations.append(f"Run error: {discovery.error_summary}")
    evidence_rows = [
        {
            "id": str(e.pk),
            "connector": e.connector,
            "kind": e.kind,
            "source_type": e.source_type,
            "title": e.title,
            "url": e.url,
            "origin": e.origin,
            "retrieved_at": e.retrieved_at.isoformat(),
            "published_at": e.published_at.isoformat() if e.published_at else None,
            "published_at_source": e.metadata.get("published_at_source"),
            "age_months": evidence_age_months(e),
            "content_hash": e.content_hash,
        }
        for e in evidence
    ]
    turn_rows = [
        {
            "number": turn.number,
            "role": turn.role,
            "submission": turn.submission,
            "model": turn.model,
            "prompt_hash": turn.prompt_hash,
        }
        for turn in turns
    ]
    quality = compute_quality(discovery)
    verdict = discovery.verdicts.select_related("recorded_by").last()
    report = Report(
        discovery_id=str(discovery.pk),
        objective=discovery.objective,
        status=discovery.status,
        outcome=discovery.outcome,
        stop_reason=discovery.stop_reason,
        turns_completed=discovery.turns_completed,
        known_cost_usd=str(known),
        estimated_cost_usd=str(estimated),
        unknown_operations=unknown,
        final_proposal=final.content if final else None,
        evidence=evidence_rows,
        turns=turn_rows,
        unresolved_objections=unresolved,
        limitations=limitations,
        judgments=judgment_rows,
        latest_judgment=judgments[-1].content if judgments else None,
        judge_required=judge_required,
        judge_approved=judge_approved,
        quality=quality,
        human_verdict=verdict.as_dict() if verdict else None,
    ).model_dump(mode="json")
    duration = (
        (discovery.ended_at - (discovery.started_at or discovery.created_at)).total_seconds()
        if discovery.ended_at
        else 0
    )
    outcome_label = OUTCOME_LABELS.get(discovery.outcome, discovery.outcome)
    if judge_required:
        if judge_approved:
            outcome_label = "Judge approved an opportunity for human validation"
        elif current_judgment and current_judgment.verdict == "reject":
            outcome_label = "Judge no-go; no approved opportunity"
        elif current_judgment and current_judgment.verdict == "revise":
            outcome_label = "Judge requested revision; no approved opportunity"
        elif final_review and final_review.verdict == "accept":
            outcome_label = "Finder and Validator agreed; Judge approval not completed"
        elif discovery.outcome == "converged":
            outcome_label = "Judge approval missing; no approved opportunity"
    lines = [
        "# Opportunity discovery report",
        "",
        f"**Outcome: {outcome_label}**",
        "",
        f"Objective: {discovery.objective}",
        "",
        f"Constraints: {discovery.constraints or 'None specified.'}",
        "",
        f"Turns completed: {discovery.turns_completed} · Elapsed: {duration:.0f} seconds · "
        f"Known spend: USD {known:.6f} · Estimated charges: USD {estimated:.6f}",
        "",
        f"Stop reason: {discovery.stop_reason} · Lifecycle: {discovery.status}",
        "",
        "## Final assessment"
        if judge_approved or (not judge_required and discovery.outcome == "converged")
        else "## Strongest remaining candidate (not validated)",
        "",
    ]
    if final:
        content = final.content
        lines.extend([f"### {content['title']}", "", content["summary"], ""])
        labels = {
            "target_customer": "Customer",
            "buyer": "Buyer",
            "problem": "Problem",
            "offering": "Offering",
            "business_model": "Business model",
            "customer_relationship": "Customer relationship",
            "revenue_hypothesis": "Revenue hypothesis",
            "delivery_model": "Delivery/production model",
            "distribution_hypothesis": "Distribution",
            "resource_needs": "Resources and capital",
        }
        for key, label in labels.items():
            lines.extend([f"**{label}:** {content.get(key, 'Not recorded.')}", ""])
            if key == "business_model":
                lines.extend([f"**Business model kind:** {proposal_kind(content)}", ""])
        lines.extend(
            [
                "### Operating assumptions",
                "",
                _bullets(content.get("operating_assumptions", [])),
                "",
            ]
        )
        lines.extend(["### Remaining risks", "", _bullets(content.get("risks", [])), ""])
    else:
        lines.extend(["No Finder proposal was completed before this run stopped.", ""])
    if verdict:
        lines.extend([_verdict_line(verdict), ""])
    lines.extend(["## Judge assessment", ""])
    if current_judgment:
        judgment = current_judgment.content
        lines.extend([f"**Verdict: {current_judgment.verdict}**", "", judgment["summary"], ""])
        for key, value in judgment.get("assessment", {}).items():
            if key == "schema_version":
                continue
            lines.extend([f"**{key.replace('_', ' ').title()}:** {value}", ""])
        if current_judgment.verdict == "reject":
            lines.extend(
                [
                    "This opportunity is a no-go. Finder and Validator must find a new "
                    "opportunity within the remaining run limits.",
                    "",
                ]
            )
    elif judge_required:
        lines.extend(["No completed Judge assessment of the latest agreed candidate.", ""])
    else:
        lines.extend(["Judge review was not enabled for this discovery.", ""])
    if judgments:
        lines.extend(["### Judge review history", ""])
        for row in judgments:
            lines.extend(
                [
                    f"- Turn {row.turn.number}: **{row.verdict}**, proposal `{row.proposal_id}` "
                    f"— {row.content['summary']}",
                ]
            )
        lines.append("")
    lines.extend(["## Evidence for and against", ""])
    lines.extend(
        ["Current proposal and its latest review; earlier findings remain in the history.", ""]
    )
    if final:
        for claim in _current_claims(final.content):
            lines.extend([f"- **{claim['kind']}**: {claim['text']}"])
            for reference in claim.get("evidence", []):
                lines.append(
                    f"  - {reference['relationship']}: evidence `{reference['evidence_id']}`"
                    f"; locator: {reference.get('locator') or 'whole source'}"
                )
    latest_objections = list(final_review.content.get("objections", [])) if final_review else []
    if current_judgment:
        latest_objections.extend(current_judgment.content.get("objections", []))
    if latest_objections:
        objections = {objection["id"]: objection for objection in latest_objections}
        for objection in objections.values():
            if objection.get("resolved"):
                continue
            refs = (
                ", ".join(dict.fromkeys(objection.get("evidence_ids", []))) or "No source recorded"
            )
            lines.append(
                f"- **Counterevidence / {objection['severity']}**: {objection['text']} ({refs})"
            )
    if not evidence:
        lines.extend(["No source evidence was captured.", ""])
    lines.extend(["", "### Source register", ""])
    for e in evidence_rows:
        lines.append(
            f"- `{e['id']}` [{e['title'] or e['url']}]({e['url']}) — "
            f"{e['connector']}, {e['kind']}, type {e['source_type']}; "
            f"retrieved {e['retrieved_at']}; {_published(e)}; origin {e['origin']}."
        )
    lines.extend(["", "## How the idea evolved", ""])
    for turn in turns:
        content = turn.submission
        lines.extend(
            [f"### Turn {turn.number}: {turn.role.title()}", "", content.get("summary", ""), ""]
        )
        if turn.role == "finder":
            lines.extend([f"Proposal: {content.get('title', '')}", ""])
            for response in content.get("objection_responses", []):
                lines.append(
                    f"- {response['objection_id']} — {response['disposition']}: "
                    f"{response['rationale']}"
                )
        else:
            lines.extend([f"Verdict: {content.get('verdict', '')}", ""])
            for objection in content.get("objections", []):
                lines.append(f"- {objection['id']} ({objection['severity']}): {objection['text']}")
            if content.get("counterproposal"):
                lines.extend(["", f"Suggested alternative: {content['counterproposal']}", ""])
            lines.extend(["", "Next research:", "", _bullets(content.get("next_research", [])), ""])
        if content.get("next_tests"):
            lines.extend(
                [
                    "",
                    "Human tests proposed at this turn (historical):",
                    "",
                    _bullets(content["next_tests"]),
                    "",
                ]
            )
    lines.extend(["", "## Unresolved objections", ""])
    lines.extend([_bullets([f"{o['id']} ({o['severity']}): {o['text']}" for o in unresolved]), ""])
    lines.extend(["## Next human tests", ""])
    tests = list(final.content.get("next_tests", [])) if final else []
    if final_review and final_review.content.get("next_tests"):
        # The latest review refines the proposal's checklist. Concatenating both
        # duplicates the same tests with slightly different wording.
        tests = final_review.content["next_tests"]
    if current_judgment:
        tests = current_judgment.content.get("next_tests", [])
    lines.extend(
        [
            _bullets(_distinct_text(tests)),
            "",
            "## Limitations and provenance",
            "",
            _bullets(limitations),
            "",
            *_evidence_quality(quality, verdict),
        ]
    )
    lines.extend(
        [
            f"Discovery ID: `{discovery.pk}`",
            "",
            f"Evidence policy: {run_config.evidence_policy}; minimum Validator query novelty: "
            f"{run_config.min_validator_query_novelty:.2f}.",
            "",
            "Configuration:",
            "",
            "```json",
            json.dumps(discovery.config_snapshot, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    for turn in turns:
        lines.append(f"- Turn {turn.number}: model `{turn.model}`, prompt `{turn.prompt_hash}`.")
    if unsettled:
        lines.extend(["", "### Unsettled operations and possible charges", ""])
        for operation in unsettled:
            batch_id = operation.response.get("batch_id", "not recorded")
            lines.append(
                f"- Operation `{operation.pk}` — {operation.state}; batch `{batch_id}`; "
                f"reserved USD {operation.reserved_usd}; known charge USD {operation.cost_usd}."
            )
    lines.extend(["", "### Research and model call ledger", ""])
    for operation in discovery.operations.order_by("created_at"):
        cost_label = (
            "unsettled (charge unknown), recorded"
            if operation.state in {"pending", "started", "unknown"}
            or operation.response.get("cost_unknown")
            else "estimated"
            if operation.response.get("cost_is_estimate")
            else "known"
        )
        lines.append(
            f"- `{operation.pk}` — {operation.kind}/{operation.connector or 'model'}, "
            f"{operation.state}; "
            f"{cost_label} "
            f"cost USD {operation.cost_usd}; "
            f"tokens {operation.input_tokens} input / {operation.output_tokens} output / "
            f"{operation.cached_tokens} cached."
        )
    failures = discovery.operations.exclude(error_summary="")
    if failures.exists():
        lines.extend(["", "### Research failures", ""])
        lines.extend(f"- {op.connector or op.kind}: {op.error_summary}" for op in failures)
    return "\n".join(lines) + "\n", report
