"""Deterministic quality metrics, asserted against hand-computed fixture values."""

from __future__ import annotations

from decimal import Decimal

import pytest

from discovery.models import Discovery
from discovery.quality import _novelty, _query_tokens, compute_quality
from discovery.schemas import QualityMetrics
from discovery.verdicts import record_verdict
from tests import discovery_fixtures as fx


def test_query_tokens_casefold_and_split_on_punctuation():
    assert _query_tokens("Precios, PyME—Córdoba (2026)!") == {"precios", "pyme", "córdoba", "2026"}
    assert _query_tokens("  ") == frozenset()


def test_novelty_separates_rewording_from_new_queries():
    assert _novelty(fx.REWORDED_QUERY, [fx.FINDER_QUERY]) == pytest.approx(2 / 11)
    assert _novelty(fx.DISTINCT_QUERY, [fx.FINDER_QUERY]) == 1.0
    assert _novelty(fx.FINDER_QUERY, [fx.FINDER_QUERY, fx.DISTINCT_QUERY]) == 0.0
    assert _novelty("anything", []) == 1.0


@pytest.mark.django_db
def test_every_metric_on_an_evidenced_discovery():
    row, _ = fx.evidenced_discovery()
    record_verdict(row, "weak", "Thin demand", user=None, via="api")
    metrics = compute_quality(row)
    assert metrics.schema_version == "1.0"
    assert metrics.discovery_id == str(row.pk)
    assert metrics.cited_evidence_count == 4
    # d.example is an exact content copy of a.example and is not a second origin.
    assert metrics.cited_origin_count == 3
    assert metrics.cited_connector_counts == {"bing": 1, "duckduckgo": 1, "web_fetch": 2}
    assert metrics.snippet_citation_share == 0.25
    assert metrics.fact_claims_with_quote_rate == 0.5
    assert metrics.claims_with_evidence_rate == pytest.approx(2 / 3)
    assert metrics.published_at_known_share == 0.5
    assert metrics.median_evidence_age_days == 20
    assert metrics.validator_query_novelty_min == pytest.approx(2 / 11)
    assert metrics.accepting_turn_query_novelty == 1.0
    assert metrics.validator_new_origin_share == pytest.approx(2 / 5)
    assert metrics.turns_completed == 5
    assert metrics.proposal_count == 2
    assert metrics.pivot_count == 1
    assert metrics.turns_to_first_accept == 4
    assert metrics.judge_verdict_counts == {"approve": 1}
    assert metrics.research_calls == 4
    assert metrics.research_failure_share == 0.25
    assert metrics.local_denials == 1
    assert metrics.known_cost_usd == Decimal("1.0")
    assert metrics.estimated_cost_usd == Decimal("0.5")
    assert metrics.unknown_operations == 1
    assert metrics.outcome == "converged" and metrics.stop_reason == "judge_approved"
    assert metrics.judge_required and metrics.judge_approved
    assert metrics.human_verdict == "weak"
    assert metrics.finder_model == metrics.validator_model == "deepseek/deepseek-v4.1-flash"
    assert metrics.judge_model == "kimi-k3:cloud"
    assert metrics.same_model_family
    assert metrics.prompt_hashes == {
        "finder": "finder-hash",
        "judge": "judge-hash",
        "validator": "validator-hash",
    }
    assert metrics.notes == []
    dumped = metrics.model_dump(mode="json")
    assert QualityMetrics.model_validate(dumped) == metrics


@pytest.mark.django_db
def test_unknowns_are_none_with_notes_never_zero():
    row = fx.discovery(
        outcome="not_converged",
        config_snapshot={**fx.CONFIG, "judge_enabled": False, "validator_provider": "anthropic"},
    )
    t1 = fx.turn(row, 1, "finder")
    fx.search(t1, fx.FINDER_QUERY)
    source = fx.evidence(t1, "a.example")
    fx.proposal(t1, {"demand_evidence_ids": [str(source.pk)], "claims": []})
    metrics = compute_quality(row)
    assert metrics.cited_evidence_count == 1
    assert metrics.median_evidence_age_days is None
    assert metrics.published_at_known_share == 0.0
    assert metrics.validator_query_novelty_min is None
    assert metrics.accepting_turn_query_novelty is None
    assert metrics.turns_to_first_accept is None
    assert metrics.claims_with_evidence_rate is None
    assert metrics.fact_claims_with_quote_rate is None
    assert metrics.validator_new_origin_share == 0.0
    assert metrics.judge_model is None and not metrics.same_model_family
    assert metrics.human_verdict is None
    notes = " ".join(metrics.notes)
    for name in (
        "median_evidence_age_days",
        "Validator never searched",
        "turns_to_first_accept",
        "accepting_turn_query_novelty",
        "claims_with_evidence_rate",
        "fact_claims_with_quote_rate",
    ):
        assert name in notes


@pytest.mark.django_db
def test_discovery_without_records_reports_unknown_evidence():
    row = Discovery.objects.create(objective="Failed early", request_hash="x", status="failed")
    metrics = compute_quality(row)
    assert metrics.cited_evidence_count is None and metrics.cited_origin_count is None
    assert metrics.snippet_citation_share is None
    assert metrics.validator_new_origin_share is None
    assert metrics.research_failure_share is None
    assert metrics.proposal_count == 0 and metrics.research_calls == 0
    assert metrics.prompt_hashes == {} and metrics.finder_model is None
    assert any("No Finder proposal" in note for note in metrics.notes)


@pytest.mark.django_db
def test_unmatched_legacy_citations_are_noted_not_counted():
    row = fx.discovery()
    t1 = fx.turn(row, 1, "finder")
    fx.proposal(
        t1,
        {
            "demand_evidence_ids": ["source-one"],
            "claims": [{"id": "c", "kind": "fact", "text": "x", "evidence": []}],
        },
    )
    metrics = compute_quality(row)
    assert metrics.cited_evidence_count == 0
    assert metrics.snippet_citation_share is None
    assert metrics.claims_with_evidence_rate == 0.0
    assert any("do not match captured evidence" in note for note in metrics.notes)
