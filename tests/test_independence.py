"""Validator independence measured by query novelty against earlier Finder searches."""

import pytest

from discovery.independence import novelty, query_tokens
from discovery.schemas import Validation
from discovery.termination import SubmissionError, has_independent_research, require_agreement
from tests import discovery_fixtures as fx


def test_helpers_match_the_one_point_zero_tokenizer():
    assert query_tokens("Precios, PyME—Córdoba (2026)!") == {"precios", "pyme", "córdoba", "2026"}
    assert novelty(fx.REWORDED_QUERY, [fx.FINDER_QUERY]) == pytest.approx(2 / 11)
    assert novelty(fx.DISTINCT_QUERY, [fx.FINDER_QUERY]) == 1.0


def _run(threshold: float | None):
    config = dict(fx.CONFIG)
    if threshold is not None:
        config.update(schema_version="1.3", min_validator_query_novelty=threshold)
    row = fx.discovery(config_snapshot=config)
    finder = fx.turn(row, 1, "finder")
    fx.search(finder, fx.FINDER_QUERY, found=1)
    demand = fx.evidence(finder, "a.example", source_type="forum")
    competitor = fx.evidence(finder, "b.example", source_type="vendor")
    proposal = fx.proposal(
        finder,
        {
            "demand_evidence_ids": [str(demand.pk)],
            "competitor_evidence_ids": [str(competitor.pk)],
            "business_model_kind": "services",
        },
    )
    return row, proposal, fx.turn(row, 2, "validator")


def _validation(proposal):
    return Validation(
        reviewed_proposal_id=str(proposal.pk),
        reviewed_proposal_hash=proposal.content_hash,
        verdict="accept",
        summary="Accept",
        falsification_checks=["Searched independently"],
    )


@pytest.mark.django_db
def test_reworded_query_cannot_accept_above_threshold_and_error_states_best_novelty():
    row, proposal, validator = _run(0.4)
    fx.search(validator, fx.REWORDED_QUERY, found=2)
    assert not has_independent_research(validator.task)
    with pytest.raises(SubmissionError) as refused:
        require_agreement(row, validator.task, proposal, _validation(proposal))
    assert "best query novelty against earlier Finder searches was 0.18" in str(refused.value)
    assert "the threshold is 0.40" in str(refused.value)
    assert not refused.value.finder_must_revise
    fx.search(validator, fx.DISTINCT_QUERY, found=1)
    assert has_independent_research(validator.task)
    require_agreement(row, validator.task, proposal, _validation(proposal))


@pytest.mark.django_db
@pytest.mark.parametrize("threshold", [None, 0.0])
def test_zero_threshold_reproduces_string_inequality(threshold):
    row, _, validator = _run(threshold)
    # Identical after whitespace/case normalisation never counts.
    fx.search(validator, "  " + fx.FINDER_QUERY.upper(), found=1)
    assert not has_independent_research(validator.task)
    # A failed or empty search never counts.
    fx.search(validator, fx.DISTINCT_QUERY, status="error")
    fx.search(validator, fx.DISTINCT_QUERY + " x")
    assert not has_independent_research(validator.task)
    # Any non-identical successful query counts, however similar.
    fx.search(validator, fx.REWORDED_QUERY, found=1)
    assert has_independent_research(validator.task)


@pytest.mark.django_db
def test_shortfall_without_any_successful_search():
    row, proposal, validator = _run(0.4)
    with pytest.raises(SubmissionError, match="no successful, non-identical search returned"):
        require_agreement(row, validator.task, proposal, _validation(proposal))
