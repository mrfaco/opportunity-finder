"""Role context 1.2: deterministic evidence profile and the turn's research budget."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from agents.prompts import load_prompt
from discovery import loop
from discovery.eval.benchmark import load_benchmark
from discovery.models import DiscoveryTask, ResearchOperation
from discovery.orchestrator import claim_task
from tests import discovery_fixtures as fx

pytestmark = pytest.mark.django_db


def _open_task(row, number: int, role: str) -> DiscoveryTask:
    return DiscoveryTask.objects.create(
        discovery=row,
        role=role,
        turn_number=number,
        state="working",
        lease_token=uuid4(),
        lease_expires_at=timezone.now() + timedelta(minutes=3),
    )


def _proposed(**fields):
    """Finder turn 1 searched twice (one repeat) and proposed a services business."""
    row = fx.discovery(status="running", outcome="", stop_reason="", ended_at=None, **fields)
    finder = fx.turn(row, 1, "finder")
    fx.search(finder, "bookkeeping complaints cordoba")
    fx.search(finder, "bookkeeping complaints cordoba")
    fx.search(finder, "monotributo accountant fee")
    forum = fx.evidence(finder, "reddit.com", source_type="forum", age_days=100)
    vendor = fx.evidence(finder, "acme.com", source_type="vendor")
    proposal = fx.proposal(
        finder,
        {
            "business_model_kind": "services",
            "demand_evidence_ids": [str(forum.pk)],
            "competitor_evidence_ids": [str(vendor.pk)],
        },
    )
    return row, proposal


def test_finder_first_turn_has_no_profile_and_a_full_budget():
    row = fx.discovery(status="running", outcome="", stop_reason="", ended_at=None)
    context = loop._initial_context(_open_task(row, 1, "finder"))
    assert context["schema_version"] == "1.2"
    assert context["evidence_profile"] is None
    assert context["research_budget"] == {
        "max_research_calls_per_turn": 8,
        "used": 0,
        "remaining": 8,
        "model_calls_remaining": 8,
    }


def test_validator_sees_finder_queries_and_the_proposal_type_counts():
    row, proposal = _proposed()
    task = _open_task(row, 2, "validator")
    context = loop._initial_context(task)
    assert context["schema_version"] == "1.2"
    profile = context["evidence_profile"]
    assert profile["proposal_id"] == str(proposal.pk)
    assert profile["finder_queries"] == [
        "bookkeeping complaints cordoba",
        "monotributo accountant fee",
    ]
    assert profile["cited_source_type_counts"] == {"forum": 1, "vendor": 1}
    assert profile["cited_evidence_count"] == 2
    assert profile["cited_origin_count"] == 2
    assert profile["snippet_citation_share"] == 0.0
    assert profile["published_at_known_share"] == 0.5
    assert profile["stale_cited_share"] == 0.0
    assert profile["demand_independent_source_share"] == 1.0
    assert profile["competitor_offer_source_share"] == 1.0
    assert profile["validator_query_novelty_min"] is None
    assert "Validator never searched; query novelty is unknown." in profile["notes"]
    assert context["research_budget"]["remaining"] == 8
    assert context["research_budget"]["used"] == 0


def test_budget_counts_this_turns_calls_and_a_recovery_cap():
    row, _ = _proposed()
    task = _open_task(row, 2, "validator")
    ResearchOperation.objects.create(
        discovery=row,
        task=task,
        operation_key=f"{task.pk}:research:1",
        kind="research",
        state="completed",
        request={"action": "search", "query": "independent"},
        response={"status": "success"},
    )
    ResearchOperation.objects.create(
        discovery=row, task=task, operation_key=f"{task.pk}:model:0", kind="model"
    )
    assert loop._initial_context(task)["research_budget"] == {
        "max_research_calls_per_turn": 8,
        "used": 1,
        "remaining": 7,
        "model_calls_remaining": 7,
    }
    task.context = {"claude_recovery_id": str(uuid4()), "claude_recovery_mode": "submission"}
    assert loop._initial_context(task)["research_budget"]["model_calls_remaining"] == 1


def test_benchmark_rubric_never_reaches_any_role():
    objective = load_benchmark().objectives[0]
    row, proposal = _proposed(benchmark_tag="t", benchmark_objective_id=objective.id)
    fx.validation(fx.turn(row, 2, "validator"), proposal, "accept")
    for number, role in ((3, "judge"), (4, "finder"), (5, "validator")):
        task = _open_task(row, number, role)
        text = json.dumps(loop._initial_context(task))
        DiscoveryTask.objects.filter(pk=task.pk).update(state="done")
        for marker in ("rubric", "must_cite", "red_flags"):
            assert marker not in text
        for phrase in objective.rubric.must_cite + objective.rubric.red_flags:
            assert phrase not in text


def test_every_profile_field_a_procedure_names_exists():
    row, _ = _proposed()
    profile = loop._initial_context(_open_task(row, 2, "validator"))["evidence_profile"]
    for role in ("finder", "validator", "judge"):
        names = set(re.findall(r"`([^`]+)`", load_prompt(role, "procedural").content))
        fields = {name.removeprefix("evidence_profile.") for name in names} - names
        fields |= {name for name in names if re.fullmatch(r"[a-z_]+_(share|count|counts)", name)}
        assert fields <= profile.keys(), f"{role}: {sorted(fields - profile.keys())}"


class _Halt(Exception):
    """Stops the batch loop at the first provider call."""


def test_stored_older_batch_context_is_not_rewritten():
    row = fx.batch_discovery()
    task = claim_task(row.tasks.get(state="open").pk)
    assert task is not None
    stored_inputs = {"schema_version": "1.1", "role": "finder", "turn_number": 1}
    stored = {
        "history": [
            {"role": "system", "content": "finder prompt"},
            {"role": "user", "content": json.dumps(stored_inputs)},
        ],
        "model_index": 0,
        "pending_model_key": None,
    }
    DiscoveryTask.objects.filter(pk=task.pk).update(context=stored)
    task.refresh_from_db()
    with patch("discovery.runtime.advance", side_effect=_Halt), pytest.raises(_Halt):
        loop._execute(task)
    task.refresh_from_db()
    assert task.context["history"] == stored["history"]
    assert task.turn.input_snapshot == {}
