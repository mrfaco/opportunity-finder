"""Celery tasks for ingestion + classification."""

from __future__ import annotations

import logging
import time
from decimal import Decimal

from celery import shared_task

from agents import prompts as prompt_loader
from agents.cost import compute_cost
from ingestion.adapters.base import SourceAdapter
from ingestion.adapters.hacker_news import HackerNewsAdapter
from ingestion.backfill import backfill_from_adapter
from ingestion.filter import classify_content
from ingestion.metrics import compute_metrics, compute_metrics_by_tier
from ingestion.models import FilterEvalClassification, FilterEvalRun, FilterEvalSet
from ingestion.pipeline import ingest_from_adapter

logger = logging.getLogger(__name__)

# Registry of source name -> adapter class. New adapters slot in here. Public
# so the management command and the operations admin can both read it without
# duplicating the mapping.
ADAPTERS: dict[str, type[SourceAdapter]] = {
    HackerNewsAdapter.source: HackerNewsAdapter,
}


def _resolve_adapter_cls(source: str) -> type[SourceAdapter]:
    adapter_cls = ADAPTERS.get(source)
    if adapter_cls is None:
        raise ValueError(
            f"No ingestion adapter registered for source {source!r}. "
            f"Known sources: {sorted(ADAPTERS)}"
        )
    return adapter_cls


@shared_task
def ingest_source(source: str) -> dict:
    """Run one source adapter through the classify → embed → cluster pipeline.

    ``source`` is an adapter key (e.g. ``"hacker_news"``). Unknown sources
    raise rather than silently no-op.
    """
    return ingest_from_adapter(_resolve_adapter_cls(source)())


@shared_task
def backfill_source_task(source: str, days: int) -> dict:
    """Backfill ``days`` of history from ``source``.

    Wraps ``backfill_from_adapter`` so the admin operations page can kick
    off long-running backfills without blocking the request. The same
    function is what the ``backfill_source`` management command calls.
    """
    return backfill_from_adapter(_resolve_adapter_cls(source)(), days=days)


def _resolve_prompt(prompt_hash: str | None) -> prompt_loader.Prompt:
    """Pick which classifier prompt version to evaluate.

    ``None`` → the current ``prompts/filter/classifier.md`` on disk.
    A hash equal to the current prompt → same thing.
    Any other hash → reconstruct it from a past ``FilterEvalRun`` that stored
    that prompt's full content. Unknown hash raises — we do not silently fall
    back to the current prompt.
    """
    current = prompt_loader.load_prompt("filter", "classifier")
    if prompt_hash is None or prompt_hash == current.hash:
        return current

    historical = (
        FilterEvalRun.objects.filter(prompt_hash=prompt_hash)
        .exclude(prompt_content="")
        .order_by("-run_at")
        .first()
    )
    if historical is None:
        raise ValueError(
            f"No stored prompt content for hash {prompt_hash!r}. The current "
            f"on-disk prompt is {current.hash!r}; a historical version can "
            "only be re-run if a past FilterEvalRun captured its content."
        )
    return prompt_loader.prompt_from_content("filter", "classifier", historical.prompt_content)


@shared_task
def run_filter_eval(prompt_hash: str | None = None) -> dict:
    """Run the classifier across the whole eval set and record the metrics.

    Classifies every ``FilterEvalSet`` item with the chosen prompt version,
    writes one ``FilterEvalClassification`` per item, computes overall and
    per-tier precision / recall / F1, and persists a ``FilterEvalRun``.

    Requires a configured ``ANTHROPIC_API_KEY`` — each item is a live Haiku
    call. Cost is a few cents for the full 200-item set.
    """
    prompt = _resolve_prompt(prompt_hash)
    eval_items = list(FilterEvalSet.objects.all())
    if not eval_items:
        raise ValueError("FilterEvalSet is empty — run `manage.py load_eval_set` first.")

    started = time.monotonic()
    eval_run = FilterEvalRun.objects.create(
        prompt_hash=prompt.hash,
        prompt_content=prompt.content,
        model="",  # filled in from the first verdict below
        eval_set_size=len(eval_items),
        precision=0.0,
        recall=0.0,
        f1=0.0,
        eval_set_snapshot=[str(item.id) for item in eval_items],
    )

    overall_pairs: list[tuple[str, str]] = []
    tier_rows: list[tuple[str, str, str]] = []
    total_cost = Decimal("0")
    model_name = ""

    for item in eval_items:
        verdict = classify_content(item.content, item.content_context, prompt=prompt)
        model_verdict = "yes" if verdict.is_opportunity else "no"
        model_name = verdict.model
        total_cost += compute_cost(
            verdict.model,
            verdict.input_tokens,
            verdict.output_tokens,
            verdict.cached_tokens,
        )

        FilterEvalClassification.objects.create(
            eval_run=eval_run,
            eval_item=item,
            model_verdict=model_verdict,
            model_confidence=verdict.confidence,
            model_reason=verdict.reason,
            agrees_with_label=(model_verdict == item.human_label),
        )
        overall_pairs.append((item.human_label, model_verdict))
        tier_rows.append((item.difficulty_tier, item.human_label, model_verdict))

    overall = compute_metrics(overall_pairs)
    by_tier = compute_metrics_by_tier(tier_rows)

    eval_run.model = model_name
    eval_run.precision = float(overall["precision"])
    eval_run.recall = float(overall["recall"])
    eval_run.f1 = float(overall["f1"])
    eval_run.metrics_by_tier = by_tier
    eval_run.total_cost_usd = total_cost.quantize(Decimal("0.0001"))
    eval_run.total_duration_s = int(time.monotonic() - started)
    eval_run.save()

    logger.info(
        "run_filter_eval complete: prompt=%s n=%d precision=%.3f recall=%.3f f1=%.3f",
        prompt.hash[:12],
        len(eval_items),
        overall["precision"],
        overall["recall"],
        overall["f1"],
    )
    return {
        "eval_run_id": str(eval_run.id),
        "prompt_hash": prompt.hash,
        **overall,
        "total_cost_usd": str(eval_run.total_cost_usd),
    }
