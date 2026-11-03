"""Readable reports derived from committed artifacts, without an extra model call."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from discovery.models import Discovery
from discovery.schemas import Report
from discovery.termination import evidence_gaps, unresolved_objections

OUTCOME_LABELS = {
    "converged": "Converged on an opportunity",
    "not_converged": "Did not converge",
    "inconclusive": "Insufficient evidence or incomplete validation",
}


def _bullets(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values) or "- None recorded."


def render_report(discovery: Discovery) -> tuple[str, dict[str, Any]]:
    proposals = list(discovery.proposals.order_by("turn__number"))
    final = proposals[-1] if proposals else None
    turns = list(discovery.turns.filter(completed_at__isnull=False))
    evidence = list(discovery.evidence.all())
    unsettled = list(discovery.operations.filter(state__in=["pending", "started", "unknown"]))
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
        "Agreement between two models is not proof of demand or commercial viability.",
        "Customer interviews and real purchasing tests remain necessary.",
        "Research covers the enabled connectors and retrieved sources, not the entire market.",
    ]
    if any(op.response.get("cloud_charges_unverified") for op in discovery.operations.all()):
        limitations.append(
            "Ollama cloud-plan charges were not verified; "
            "zero recorded incremental API cost is not proof of free cloud usage."
        )
    if unknown:
        limitations.append(
            "Local cancellation or termination does not cancel outstanding remote batches; "
            "they may still complete and bill. Reconcile the listed operation/batch IDs."
        )
        limitations.append(
            f"{unknown} operation(s) have unknown billing outcomes; spend is incomplete."
        )
    if final:
        limitations.extend(evidence_gaps(discovery, final))
    if discovery.error_summary:
        limitations.append(f"Run error: {discovery.error_summary}")
    evidence_rows = [
        {
            "id": str(e.pk),
            "connector": e.connector,
            "kind": e.kind,
            "title": e.title,
            "url": e.url,
            "origin": e.origin,
            "retrieved_at": e.retrieved_at.isoformat(),
            "published_at": e.published_at.isoformat() if e.published_at else None,
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
    ).model_dump(mode="json")
    duration = (
        (discovery.ended_at - (discovery.started_at or discovery.created_at)).total_seconds()
        if discovery.ended_at
        else 0
    )
    lines = [
        "# Opportunity discovery report",
        "",
        f"**Outcome: {OUTCOME_LABELS.get(discovery.outcome, discovery.outcome)}**",
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
        "## Final opportunity"
        if discovery.outcome == "converged"
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
    lines.extend(["## Evidence for and against", ""])
    for turn in turns:
        content = turn.submission
        for claim in content.get("claims", []):
            lines.extend([f"- **{claim['kind']}**: {claim['text']}"])
            for reference in claim.get("evidence", []):
                lines.append(
                    f"  - {reference['relationship']}: evidence `{reference['evidence_id']}`"
                    f"; locator: {reference.get('locator') or 'whole source'}"
                )
        for objection in content.get("objections", []):
            refs = ", ".join(objection.get("evidence_ids", [])) or "No source recorded"
            lines.append(
                f"- **Counterevidence / {objection['severity']}**: {objection['text']} ({refs})"
            )
    if not evidence:
        lines.extend(["No source evidence was captured.", ""])
    lines.extend(["", "### Source register", ""])
    for e in evidence_rows:
        lines.append(
            f"- `{e['id']}` [{e['title'] or e['url']}]({e['url']}) — "
            f"{e['connector']}, {e['kind']}; retrieved {e['retrieved_at']}; "
            f"published {e['published_at'] or 'unknown'}; origin {e['origin']}."
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
    lines.extend(["", "## Unresolved objections", ""])
    lines.extend([_bullets([f"{o['id']} ({o['severity']}): {o['text']}" for o in unresolved]), ""])
    lines.extend(["## Next human tests", ""])
    tests = list(final.content.get("next_tests", [])) if final else []
    for turn in turns:
        tests.extend(turn.submission.get("next_tests", []))
    lines.extend(
        [
            _bullets(list(dict.fromkeys(tests))),
            "",
            "## Limitations and provenance",
            "",
            _bullets(limitations),
            "",
        ]
    )
    lines.extend(
        [
            f"Discovery ID: `{discovery.pk}`",
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
        lines.append(
            f"- `{operation.pk}` — {operation.kind}/{operation.connector or 'model'}, "
            f"{operation.state}; "
            f"{'estimated' if operation.response.get('cost_is_estimate') else 'known'} "
            f"cost USD {operation.cost_usd}; "
            f"tokens {operation.input_tokens} input / {operation.output_tokens} output / "
            f"{operation.cached_tokens} cached."
        )
    failures = discovery.operations.exclude(error_summary="")
    if failures.exists():
        lines.extend(["", "### Research failures", ""])
        lines.extend(f"- {op.connector or op.kind}: {op.error_summary}" for op in failures)
    return "\n".join(lines) + "\n", report
