"""Current conclusions stay separate from the complete dialogue record."""

import pytest
from django.utils import timezone

from discovery.models import (
    Discovery,
    DiscoveryTask,
    DiscoveryTurn,
    ProposalRevision,
    ValidationRevision,
)
from discovery.reporting import render_report


def _turn(discovery, number, role, content):
    task = DiscoveryTask.objects.create(
        discovery=discovery, role=role, turn_number=number, state="done"
    )
    return DiscoveryTurn.objects.create(
        discovery=discovery,
        task=task,
        number=number,
        role=role,
        submission=content,
        completed_at=timezone.now(),
    )


def _proposal(discovery, number, content):
    return ProposalRevision.objects.create(
        discovery=discovery,
        turn=_turn(discovery, number, "finder", content),
        content=content,
        content_hash=str(number),
    )


def _review(discovery, number, proposal, content):
    return ValidationRevision.objects.create(
        discovery=discovery,
        turn=_turn(discovery, number, "validator", content),
        proposal=proposal,
        verdict="challenge",
        content=content,
        content_hash=str(number),
    )


@pytest.mark.django_db
def test_current_evidence_and_tests_exclude_abandoned_revisions_but_keep_history():
    discovery = Discovery.objects.create(
        objective="Find a service", request_hash="test", outcome="converged"
    )
    reference = {"evidence_id": "source-one", "relationship": "supports", "locator": "page"}
    claim = {"kind": "fact", "text": "Current buyer pays for this work.", "evidence": [reference]}
    original = _proposal(
        discovery,
        1,
        {
            "title": "Old verification SKU",
            "summary": "Original direction",
            "claims": [claim, {"kind": "fact", "text": "Abandoned fact.", "evidence": []}],
            "next_tests": ["Test the abandoned verification price."],
        },
    )
    _review(
        discovery,
        2,
        original,
        {"summary": "Old review", "next_tests": ["Interview Argentine founders."]},
    )
    final = _proposal(
        discovery,
        3,
        {
            "title": "Production pack",
            "summary": "Current direction",
            "claims": [claim, {**claim, "evidence": [reference, reference]}],
            "next_tests": ["Sell one production pack.", "Earlier cost-timer wording."],
        },
    )
    objection = {
        "id": "O1",
        "severity": "major",
        "text": "Frequency is unverified.",
        "evidence_ids": ["source-two", "source-two"],
    }
    _review(
        discovery,
        4,
        final,
        {
            "summary": "Current review",
            "objections": [
                objection,
                objection,
                {"id": "O2", "severity": "minor", "text": "Old problem solved.", "resolved": True},
            ],
            "next_tests": [" sell one production pack. ", "Measure delivery time."],
        },
    )

    text, report = render_report(discovery)
    current_evidence = text.split("## Evidence for and against\n", 1)[1].split(
        "### Source register", 1
    )[0]
    current_tests = text.split("## Next human tests\n", 1)[1].split("## Limitations", 1)[0]
    assert current_evidence.count("Current buyer pays for this work.") == 1
    assert current_evidence.count("source-one") == 1
    assert current_evidence.count("Frequency is unverified.") == 1
    assert "source-two, source-two" not in current_evidence
    assert "Abandoned fact." not in current_evidence
    assert "Old problem solved." not in current_evidence
    assert current_tests.casefold().count("sell one production pack.") == 1
    assert "Measure delivery time." in current_tests
    assert "Earlier cost-timer wording." not in current_tests
    assert "abandoned" not in current_tests
    assert "Argentine founders" not in current_tests
    assert "Test the abandoned verification price." in text
    assert "Interview Argentine founders." in text
    assert report["turns"][0]["submission"]["claims"][1]["text"] == "Abandoned fact."
    assert "Converged on an opportunity" not in text


@pytest.mark.django_db
def test_unreviewed_latest_proposal_does_not_inherit_prior_review_tests():
    discovery = Discovery.objects.create(
        objective="Find a service",
        request_hash="test",
        config_snapshot={
            "finder_model": "same",
            "validator_model": "same",
            "finder_provider": "ollama",
            "validator_provider": "ollama",
        },
    )
    original = _proposal(discovery, 1, {"title": "Old", "summary": "Old"})
    _review(discovery, 2, original, {"next_tests": ["Old review test."]})
    _proposal(discovery, 3, {"title": "New", "summary": "New", "next_tests": ["Current test."]})
    text, report = render_report(discovery)
    current_tests = text.split("## Next human tests\n", 1)[1].split("## Limitations", 1)[0]
    assert "Current test." in current_tests
    assert "Old review test." not in current_tests
    assert any("shared model blind spots" in value for value in report["limitations"])
