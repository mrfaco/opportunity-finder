"""Focused checks for Judge authority, rejection feedback and historical routing."""

from copy import deepcopy
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone
from pydantic import ValidationError

from discovery.models import Evidence, ResearchOperation
from discovery.orchestrator import claim_task, commit_turn, start_discovery
from discovery.schemas import DiscoveryConfig, Judgment
from discovery.termination import SubmissionError, unresolved_objections


def _start(*, max_turns=100, schema_version="1.2"):
    config = {
        "schema_version": schema_version,
        "max_turns": max_turns,
        "connectors": ["duckduckgo", "web_fetch"],
        "finder_provider": "ollama",
        "finder_model": "deepseek-v4.1-flash:cloud",
        "validator_provider": "ollama",
        "validator_model": "glm-5.3-flash:cloud",
    }
    with patch("discovery.runtime.check_configuration", return_value={"profiles": {}}):
        return start_discovery(
            "Find a worthwhile Argentina cybersecurity business", config, enqueue=False
        )


def _claim(discovery):
    return claim_task(discovery.tasks.get(state="open").pk)


def _proposal(task):
    sources = []
    for i in (1, 2):
        sources.append(
            Evidence.objects.create(
                discovery=task.discovery,
                turn=task.turn,
                connector="web_fetch",
                external_id=str(i),
                url=f"https://source{i}.example/",
                title=f"Source {i}",
                content=f"Independent source {i}",
                kind="fetched_page",
                origin=f"source{i}.example",
                content_hash=str(i),
                retrieved_at=timezone.now(),
            )
        )
    return {
        "title": "A concrete service",
        "target_customer": "Argentine manufacturers",
        "buyer": "Operations manager",
        "problem": "Observed recurring workflow cost",
        "offering": "Deliver the missing workflow",
        "business_model": "Fee per job",
        "customer_relationship": "B2B",
        "revenue_hypothesis": "Meaningful contribution if observed volumes hold",
        "delivery_model": "An operator and tools",
        "distribution_hypothesis": "Established distributor",
        "demand_evidence_ids": [str(sources[0].pk)],
        "competitor_evidence_ids": [str(sources[1].pk)],
        "summary": "A business worth comparing against the founder's alternatives.",
    }


def _accept(task, *, objections=None):
    proposal = task.discovery.proposals.latest("created_at")
    ResearchOperation.objects.create(
        discovery=task.discovery,
        task=task,
        operation_key=f"independent-search:{task.pk}",
        kind="research",
        state="completed",
        request={"action": "search", "query": f"independent alternatives {task.pk}"},
        response={"status": "success", "documents": [{"url": "https://competitor.example/"}]},
    )
    return {
        "reviewed_proposal_id": str(proposal.pk),
        "reviewed_proposal_hash": proposal.content_hash,
        "verdict": "accept",
        "summary": "Evidence supports this precise proposal.",
        "falsification_checks": ["Compared demand and alternatives"],
        "objections": objections or [],
    }


def _judge(task, *, verdict="approve", objections=None):
    proposal = task.discovery.proposals.latest("created_at")
    validation = task.discovery.validations.latest("created_at")
    return {
        "reviewed_proposal_id": str(proposal.pk),
        "reviewed_proposal_hash": proposal.content_hash,
        "reviewed_validation_id": str(validation.pk),
        "reviewed_validation_hash": validation.content_hash,
        "verdict": verdict,
        "worth_pursuing": verdict != "reject",
        "summary": "An independent commercial judgment.",
        "assessment": {
            key: "A reasoned assessment with explicit assumptions."
            for key in (
                "economics",
                "opportunity_cost",
                "observed_transaction",
                "competitive_pressure",
                "objective_fit",
                "convergence_risk",
            )
        },
        "objections": objections or [],
    }


def _agreement(discovery, *, objections=None):
    finder = _claim(discovery)
    proposal = _proposal(finder)
    commit_turn(finder.pk, finder.lease_token, proposal, enqueue=False)
    validator = _claim(discovery)
    commit_turn(
        validator.pk,
        validator.lease_token,
        _accept(validator, objections=objections),
        enqueue=False,
    )
    discovery.refresh_from_db()
    return proposal


@pytest.mark.django_db
def test_agreement_requires_exact_judge_approval_before_convergence():
    discovery = _start()
    _agreement(discovery)
    assert discovery.status == "running"
    assert discovery.turns_completed == 2
    judge = _claim(discovery)
    assert judge.role == "judge"
    submission = _judge(judge)
    wrong = {**submission, "reviewed_validation_hash": "stale"}
    with pytest.raises(SubmissionError, match="exact latest accepted"):
        commit_turn(judge.pk, judge.lease_token, wrong, enqueue=False)
    assert discovery.judgments.count() == 0
    result = commit_turn(judge.pk, judge.lease_token, submission, enqueue=False)
    assert result.outcome == "converged"
    assert result.stop_reason == "judge_approval"
    assert result.turns_completed == 3
    assert result.judgments.get().validation.verdict == "accept"
    again = commit_turn(judge.pk, judge.lease_token, submission, enqueue=False)
    assert again.judgments.count() == 1


@pytest.mark.django_db
def test_judge_rejection_requires_new_business_and_stable_objection_response():
    discovery = _start()
    previous = _agreement(discovery)
    judge = _claim(discovery)
    objection = {
        "id": "J-1",
        "severity": "major",
        "text": "The base case is not worth the founder's time.",
    }
    commit_turn(
        judge.pk,
        judge.lease_token,
        _judge(judge, verdict="reject", objections=[objection]),
        enqueue=False,
    )
    discovery.refresh_from_db()
    assert discovery.status == "running"
    finder = _claim(discovery)
    assert finder.role == "finder"
    unchanged = deepcopy(previous)
    unchanged["parent_revision_id"] = str(discovery.proposals.latest("created_at").pk)
    with pytest.raises(SubmissionError, match="every unresolved Judge"):
        commit_turn(finder.pk, finder.lease_token, unchanged, enqueue=False)
    unchanged["objection_responses"] = [
        {
            "objection_id": "J-1",
            "disposition": "pivot",
            "rationale": "Abandon the rejected customer segment.",
        }
    ]
    with pytest.raises(SubmissionError, match="new opportunity"):
        commit_turn(finder.pk, finder.lease_token, unchanged, enqueue=False)
    pivot = {
        **unchanged,
        "target_customer": "A distinct, evidenced buyer segment",
        "offering": "A different recurring workflow",
    }
    commit_turn(finder.pk, finder.lease_token, pivot, enqueue=False)
    discovery.refresh_from_db()
    assert discovery.turns_completed == 4
    assert discovery.tasks.get(state="open").role == "validator"
    assert "J-1" in unresolved_objections(discovery)
    assert discovery.judgments.get().verdict == "reject"
    validator = _claim(discovery)
    review = _accept(validator)
    review.update(verdict="challenge", resolved_objection_ids=["J-1"])
    commit_turn(validator.pk, validator.lease_token, review, enqueue=False)
    followup = _claim(discovery)
    revision = {**pivot, "parent_revision_id": str(discovery.proposals.latest("created_at").pk)}
    revision.pop("objection_responses")
    commit_turn(followup.pk, followup.lease_token, revision, enqueue=False)
    discovery.refresh_from_db()
    assert discovery.turns_completed == 6
    assert "J-1" not in unresolved_objections(discovery)


@pytest.mark.django_db
def test_judge_cannot_omit_material_validator_objections_or_resolve_by_id_alone():
    discovery = _start()
    _agreement(
        discovery,
        objections=[{"id": "V-1", "severity": "major", "text": "Unit economics are unverified."}],
    )
    judge = _claim(discovery)
    submission = _judge(judge)
    with pytest.raises(SubmissionError, match="explicit evidenced Judge resolution"):
        commit_turn(judge.pk, judge.lease_token, submission, enqueue=False)
    submission["resolved_objection_ids"] = ["V-1"]
    with pytest.raises(SubmissionError, match="explicit evidenced Judge resolution"):
        commit_turn(judge.pk, judge.lease_token, submission, enqueue=False)


@pytest.mark.django_db
def test_turn_ceiling_never_treats_unjudged_agreement_as_convergence():
    discovery = _start(max_turns=2)
    _agreement(discovery)
    assert discovery.status == "completed"
    assert discovery.outcome == "inconclusive"
    assert discovery.stop_reason == "turn_limit"
    assert not discovery.tasks.filter(role="judge").exists()


@pytest.mark.django_db
@override_settings(DISCOVERY_JUDGE_ENABLED=True)
def test_old_configuration_stays_two_role_despite_new_host_judge_default():
    discovery = _start(schema_version="1.1")
    assert discovery.config_snapshot["judge_enabled"] is False
    assert "judge" not in discovery.prompt_snapshot
    _agreement(discovery)
    assert discovery.outcome == "converged"
    assert not discovery.tasks.filter(role="judge").exists()
    assert DiscoveryConfig.model_validate({"schema_version": "1.0"}).judge_enabled is False
    assert DiscoveryConfig().judge_enabled is True


def test_judge_schema_refuses_no_go_approval_and_unresolved_major_objections():
    base = {
        "reviewed_proposal_id": "p",
        "reviewed_proposal_hash": "ph",
        "reviewed_validation_id": "v",
        "reviewed_validation_hash": "vh",
        "verdict": "approve",
        "worth_pursuing": False,
        "summary": "Polite no-go",
        "assessment": {
            key: "A reason"
            for key in (
                "economics",
                "opportunity_cost",
                "observed_transaction",
                "competitive_pressure",
                "objective_fit",
                "convergence_risk",
            )
        },
    }
    with pytest.raises(ValidationError, match="no-go"):
        Judgment.model_validate(base)
    with pytest.raises(ValidationError, match="critical or major"):
        Judgment.model_validate(
            {
                **base,
                "worth_pursuing": True,
                "objections": [
                    {"id": "J-1", "severity": "major", "text": "No observed transaction"}
                ],
            }
        )
