"""Tests for the classifier filter, eval set loader, and eval metrics.

The live Anthropic call is mocked everywhere — these tests never hit the API.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from ingestion import tasks as ingestion_tasks
from ingestion.filter import ClassifierResponse, FilterVerdict, classify_content
from ingestion.metrics import compute_metrics, compute_metrics_by_tier
from ingestion.models import (
    DifficultyTier,
    FilterEvalClassification,
    FilterEvalRun,
    FilterEvalSet,
    HumanConfidence,
    HumanLabel,
    SourcedFrom,
)


# ---------------------------------------------------------------------------
# Metrics — pure functions
# ---------------------------------------------------------------------------
def test_compute_metrics_perfect_score():
    pairs = [("yes", "yes"), ("no", "no"), ("yes", "yes"), ("no", "no")]
    m = compute_metrics(pairs)
    assert m["precision"] == 1.0
    assert m["recall"] == 1.0
    assert m["f1"] == 1.0
    assert m["accuracy"] == 1.0
    assert m["n"] == 4


def test_compute_metrics_mixed():
    # truth: yes yes no no  |  pred: yes no no no
    pairs = [("yes", "yes"), ("yes", "no"), ("no", "no"), ("no", "no")]
    m = compute_metrics(pairs)
    assert m["true_positives"] == 1
    assert m["false_negatives"] == 1
    assert m["false_positives"] == 0
    assert m["true_negatives"] == 2
    assert m["precision"] == 1.0
    assert m["recall"] == 0.5
    assert m["f1"] == round(2 * 1.0 * 0.5 / 1.5, 4)


def test_compute_metrics_empty_is_zero_not_error():
    m = compute_metrics([])
    assert m["precision"] == 0.0
    assert m["recall"] == 0.0
    assert m["f1"] == 0.0
    assert m["n"] == 0


def test_compute_metrics_by_tier_groups():
    rows = [
        ("clear_yes", "yes", "yes"),
        ("clear_yes", "yes", "yes"),
        ("clear_no", "no", "no"),
        ("adversarial", "yes", "no"),
    ]
    by_tier = compute_metrics_by_tier(rows)
    assert set(by_tier.keys()) == {"clear_yes", "clear_no", "adversarial"}
    assert by_tier["clear_yes"]["recall"] == 1.0
    assert by_tier["adversarial"]["recall"] == 0.0


# ---------------------------------------------------------------------------
# Eval set loader
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_load_eval_set_loads_all_tiers():
    call_command("load_eval_set")
    total = FilterEvalSet.objects.count()
    assert total == 200, f"expected 200 seed items, got {total}"
    # 50 per difficulty tier.
    for tier in DifficultyTier.values:
        assert FilterEvalSet.objects.filter(difficulty_tier=tier).count() == 50
    # All hand-curated, all labelled yes/no for binary scoring.
    assert FilterEvalSet.objects.filter(sourced_from=SourcedFrom.HAND_CURATED).count() == 200
    assert set(FilterEvalSet.objects.values_list("human_label", flat=True)) <= {
        HumanLabel.YES,
        HumanLabel.NO,
    }


@pytest.mark.django_db
def test_load_eval_set_is_idempotent():
    call_command("load_eval_set")
    call_command("load_eval_set")
    assert FilterEvalSet.objects.count() == 200


# ---------------------------------------------------------------------------
# classify_content — mocked Anthropic client
# ---------------------------------------------------------------------------
def _fake_cheap_response(is_opportunity: bool):
    """Return a ``CheapModelResponse`` shape that the callsite expects.

    The dispatcher's provider selection is exercised in ``test_llm.py``;
    these tests just inject the final result at the callsite import
    binding, keeping them small and provider-agnostic.
    """
    from core.llm import CheapModelResponse  # noqa: PLC0415

    return CheapModelResponse(
        parsed=ClassifierResponse(
            is_opportunity=is_opportunity, confidence=0.88, reason="mocked reason"
        ),
        model_used="claude-haiku-4-5",
        provider="anthropic",
        input_tokens=140,
        output_tokens=22,
        cached_tokens=0,
        latency_ms=42,
    )


def test_classify_content_parses_verdict(monkeypatch):
    captured = {}

    def _fake_call(**kwargs):
        captured.update(kwargs)
        return _fake_cheap_response(is_opportunity=True)

    monkeypatch.setattr("ingestion.filter.call_cheap_model", _fake_call)

    verdict = classify_content("I wish a tool existed that did X.")

    assert isinstance(verdict, FilterVerdict)
    assert verdict.is_opportunity is True
    assert verdict.confidence == 0.88
    assert verdict.input_tokens == 140
    assert verdict.output_tokens == 22
    assert len(verdict.prompt_hash) == 64
    # The dispatcher receives the loaded prompt content as system_prompt
    # and the rendered user content separately — Anthropic-specific
    # cache_control wiring is covered in test_llm.py.
    assert "system_prompt" in captured
    assert captured["output_schema"] is ClassifierResponse


def test_classify_content_raises_when_dispatcher_raises(monkeypatch):
    """The dispatcher's loud-fail (no parsed output, schema mismatch, etc.)
    propagates verbatim — this callsite has no fallback path."""

    def _fake_call(**kwargs):
        raise RuntimeError("dispatcher returned no parsed output")

    monkeypatch.setattr("ingestion.filter.call_cheap_model", _fake_call)
    with pytest.raises(RuntimeError, match="no parsed output"):
        classify_content("something")


# ---------------------------------------------------------------------------
# run_filter_eval — mocked classifier
# ---------------------------------------------------------------------------
def _make_eval_item(content: str, label: str, tier: str) -> FilterEvalSet:
    return FilterEvalSet.objects.create(
        content=content,
        content_context={},
        source="hand_curated",
        human_label=label,
        human_confidence=HumanConfidence.HIGH,
        difficulty_tier=tier,
        sourced_from=SourcedFrom.HAND_CURATED,
    )


@pytest.mark.django_db
def test_run_filter_eval_records_metrics(monkeypatch):
    items = [
        _make_eval_item("A real unmet need", HumanLabel.YES, DifficultyTier.CLEAR_YES),
        _make_eval_item("Another real need", HumanLabel.YES, DifficultyTier.CLEAR_YES),
        _make_eval_item("Just a news post", HumanLabel.NO, DifficultyTier.CLEAR_NO),
        _make_eval_item("Pure praise", HumanLabel.NO, DifficultyTier.CLEAR_NO),
    ]
    # Mock classifier: correct on items 0, 2, 3; wrong on item 1 (a false negative).
    wrong_content = items[1].content

    def fake_classify(content, context=None, prompt=None):
        is_opp = content != wrong_content and "real" in content and "news" not in content
        return FilterVerdict(
            is_opportunity=is_opp,
            confidence=0.9,
            reason="mocked",
            prompt_hash="x" * 64,
            model="claude-haiku-4-5",
            input_tokens=100,
            output_tokens=20,
            cached_tokens=0,
            latency_ms=12,
        )

    monkeypatch.setattr(ingestion_tasks, "classify_content", fake_classify)

    result = ingestion_tasks.run_filter_eval()

    run = FilterEvalRun.objects.get(pk=result["eval_run_id"])
    assert run.eval_set_size == 4
    assert run.precision == 1.0  # no false positives
    assert run.recall == 0.5  # one of two real needs missed
    assert run.model == "claude-haiku-4-5"
    assert run.total_cost_usd > 0
    assert "clear_yes" in run.metrics_by_tier
    assert FilterEvalClassification.objects.filter(eval_run=run).count() == 4
    # The missed item is recorded as a disagreement.
    missed = FilterEvalClassification.objects.get(eval_item=items[1])
    assert missed.agrees_with_label is False


@pytest.mark.django_db
def test_run_filter_eval_raises_on_empty_eval_set(monkeypatch):
    monkeypatch.setattr(ingestion_tasks, "classify_content", lambda *a, **k: None)
    with pytest.raises(ValueError, match="empty"):
        ingestion_tasks.run_filter_eval()
