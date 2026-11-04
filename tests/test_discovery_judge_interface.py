"""Judge verdicts remain visible without promoting stale agreement to approval."""

import pytest
from django.utils import timezone

from discovery.api import discovery_view
from discovery.models import (
    Discovery,
    DiscoveryTask,
    DiscoveryTurn,
    JudgmentRevision,
    ProposalRevision,
    ValidationRevision,
)
from discovery.reporting import render_report


def _turn(discovery, number, role, submission):
    task = DiscoveryTask.objects.create(
        discovery=discovery, role=role, turn_number=number, state="done"
    )
    return DiscoveryTurn.objects.create(
        discovery=discovery,
        task=task,
        number=number,
        role=role,
        submission=submission,
        completed_at=timezone.now(),
    )


@pytest.mark.django_db
def test_judge_rejection_and_current_approval_are_distinguished_in_report_and_api():
    run = Discovery.objects.create(
        objective="Argentine buyers",
        request_hash="interface",
        config_snapshot={"schema_version": "1.2", "judge_enabled": True},
        outcome="converged",
    )
    proposal_content = {
        "title": "First candidate",
        "summary": "Candidate",
        "next_tests": ["Old test"],
    }
    proposal = ProposalRevision.objects.create(
        discovery=run,
        turn=_turn(run, 1, "finder", proposal_content),
        content=proposal_content,
        content_hash="proposal",
    )
    validation_content = {
        "verdict": "accept",
        "summary": "Agreement",
        "next_tests": ["Review test"],
        "objections": [{"id": "V-FIX", "severity": "major", "text": "Corrected issue."}],
    }
    validation = ValidationRevision.objects.create(
        discovery=run,
        turn=_turn(run, 2, "validator", validation_content),
        proposal=proposal,
        content=validation_content,
        content_hash="validation",
        verdict="accept",
    )
    text, report = render_report(run)
    assert "Judge approval not completed" in text
    assert not report["judge_approved"]
    assert not discovery_view(run)["judge_approved"]

    rejection = {
        "verdict": "reject",
        "worth_pursuing": False,
        "summary": "Margins do not justify the founder's time.",
        "assessment": {"opportunity_cost": "Contribution is below the operator's day rate."},
        "next_tests": [],
    }
    JudgmentRevision.objects.create(
        discovery=run,
        turn=_turn(run, 3, "judge", rejection),
        proposal=proposal,
        validation=validation,
        verdict="reject",
        content=rejection,
        content_hash="judgment",
    )
    text, report = render_report(run)
    current_tests = text.split("## Next human tests\n")[1].split("## Limitations")[0]
    assert "Judge no-go; no approved opportunity" in text
    assert "must find a new opportunity" in text
    assert "Old test" not in current_tests and "Review test" not in current_tests
    assert report["latest_judgment"]["verdict"] == "reject"
    assert len(report["judgments"]) == 1
    assert not discovery_view(run)["judge_approved"]

    approval = {
        **rejection,
        "verdict": "approve",
        "worth_pursuing": True,
        "summary": "The new opportunity merits a bounded customer test.",
        "next_tests": ["One call"],
        "objections": [
            {"id": "V-FIX", "severity": "major", "text": "Corrected issue.", "resolved": True}
        ],
    }
    next_content = {"title": "New opportunity", "summary": "Pivot", "next_tests": ["Current test"]}
    next_proposal = ProposalRevision.objects.create(
        discovery=run,
        turn=_turn(run, 4, "finder", next_content),
        content=next_content,
        content_hash="new-proposal",
    )
    next_validation = ValidationRevision.objects.create(
        discovery=run,
        turn=_turn(run, 5, "validator", validation_content),
        proposal=next_proposal,
        content=validation_content,
        content_hash="new-validation",
        verdict="accept",
    )
    JudgmentRevision.objects.create(
        discovery=run,
        turn=_turn(run, 6, "judge", approval),
        proposal=next_proposal,
        validation=next_validation,
        verdict="approve",
        content=approval,
        content_hash="new-judgment",
    )
    text, report = render_report(run)
    assert report["judge_approved"]
    assert len(report["judgments"]) == 2
    assert report["judgments"][0]["content"]["verdict"] == "reject"
    assert discovery_view(run)["judge_approved"]
    assert "Judge approved an opportunity" in text
    assert "One call" in text.split("## Next human tests\n")[1].split("## Limitations")[0]
    assert (
        "Corrected issue."
        not in text.split("## Evidence for and against\n")[1].split("### Source register")[0]
    )

    ProposalRevision.objects.create(
        discovery=run,
        turn=_turn(run, 7, "finder", next_content),
        content=next_content,
        content_hash="new-proposal",
    )
    text, report = render_report(run)
    assert not report["judge_approved"]
    assert not discovery_view(run)["judge_approved"]
    assert report["latest_judgment"]["verdict"] == "approve"
    assert "Judge approval missing; no approved opportunity" in text
