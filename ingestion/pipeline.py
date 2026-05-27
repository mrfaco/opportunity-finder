"""Ingestion pipeline: adapter → classifier → embedding → clustering.

For each item an adapter produces, classify it. If it's an opportunity,
embed it and assign it to a cluster; otherwise record a discarded
classification. Every item — kept or discarded — gets a ``FilterClassification``
row so cost and verdict history are complete.

The per-source ``IngestionCheckpoint`` advances item-by-item, so a run that
fails partway resumes cleanly without re-classifying (and re-paying for) work
already done.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from agents.cost import compute_cost
from clusters.clustering import assign_item_to_cluster, compute_embedding
from clusters.models import ClassifierVerdict, ClusterItem
from ingestion.adapters.base import IngestedItem, SourceAdapter
from ingestion.filter import classify_content
from ingestion.models import DiscardReason, FilterClassification, IngestionCheckpoint, VerdictBand

logger = logging.getLogger(__name__)

# Confidence at or above which a verdict is "high" rather than "uncertain".
_HIGH_CONFIDENCE = 0.85


def _verdict_band(is_opportunity: bool, confidence: float) -> str:
    if confidence >= _HIGH_CONFIDENCE:
        return VerdictBand.HIGH_YES if is_opportunity else VerdictBand.HIGH_NO
    return VerdictBand.UNCERTAIN


def _process_item(item: IngestedItem) -> bool:
    """Classify one item and persist the result. Returns True if an opportunity.

    The network calls (classify, embed) happen before the DB transaction so a
    failure leaves nothing half-written. Exceptions propagate — the caller
    leaves the checkpoint un-advanced so the item is retried next run.

    If the item already exists (matched by ``(source, source_item_id)``)
    we return False without re-classifying. The adapter base class
    documents "the pipeline dedupes via ``(source, source_item_id)``
    uniqueness" — this is where that promise is kept. Without this
    check, re-runs over an overlapping ``since`` window would burn
    Haiku tokens on already-classified items and then crash on the
    unique-constraint insert.
    """
    if ClusterItem.objects.filter(source=item.source, source_item_id=item.source_item_id).exists():
        return False

    verdict = classify_content(item.raw_text)
    band = _verdict_band(verdict.is_opportunity, verdict.confidence)
    cost = compute_cost(
        verdict.model,
        verdict.input_tokens,
        verdict.output_tokens,
        verdict.cached_tokens,
    )

    if not verdict.is_opportunity:
        with transaction.atomic():
            FilterClassification.objects.create(
                item=None,
                prompt_hash=verdict.prompt_hash,
                model=verdict.model,
                is_opportunity=False,
                confidence=verdict.confidence,
                reason=verdict.reason,
                input_tokens=verdict.input_tokens,
                output_tokens=verdict.output_tokens,
                cached_tokens=verdict.cached_tokens,
                cost_usd=cost,
                latency_ms=verdict.latency_ms,
                verdict_band=band,
                discarded=True,
                discard_reason=DiscardReason.BELOW_THRESHOLD,
            )
        return False

    # Opportunity: embed, cluster, persist.
    embedding = compute_embedding(item.raw_text)
    now = timezone.now()
    cluster_item = ClusterItem(
        source=item.source,
        source_item_id=item.source_item_id,
        url=item.url,
        title=item.title,
        author=item.author,
        posted_at=item.posted_at,
        raw_text=item.raw_text,
        snippet=item.raw_text[:200],
        classifier_verdict=ClassifierVerdict.OPPORTUNITY,
        classifier_confidence=verdict.confidence,
        embedding=embedding,
        added_to_cluster_at=now,
        assigned_at=now,
        metadata=item.metadata,
    )
    with transaction.atomic():
        assign_item_to_cluster(cluster_item)
        cluster_item.save()
        FilterClassification.objects.create(
            item=cluster_item,
            prompt_hash=verdict.prompt_hash,
            model=verdict.model,
            is_opportunity=True,
            confidence=verdict.confidence,
            reason=verdict.reason,
            input_tokens=verdict.input_tokens,
            output_tokens=verdict.output_tokens,
            cached_tokens=verdict.cached_tokens,
            cost_usd=cost,
            latency_ms=verdict.latency_ms,
            verdict_band=band,
            discarded=False,
        )
    return True


def ingest_from_adapter(adapter: SourceAdapter) -> dict[str, str | int]:
    """Run the full pipeline for one source adapter.

    Pulls items newer than the source's checkpoint, processes them oldest
    first, and advances the checkpoint after each success.
    """
    checkpoint, _ = IngestionCheckpoint.objects.get_or_create(source=adapter.source)
    since = checkpoint.last_item_posted_at
    if since is None:
        since = timezone.now() - timedelta(days=settings.INGEST_HN_INITIAL_DAYS)

    # Process oldest first so the checkpoint advances monotonically and a
    # mid-run failure leaves a clean resume point.
    items = sorted(adapter.fetch_new_items(since=since), key=lambda i: i.posted_at)

    opportunities = 0
    for item in items:
        is_opportunity = _process_item(item)
        if is_opportunity:
            opportunities += 1
        checkpoint.last_item_posted_at = item.posted_at
        checkpoint.items_seen += 1
        checkpoint.opportunities_found += int(is_opportunity)
        checkpoint.save(update_fields=["last_item_posted_at", "items_seen", "opportunities_found"])

    checkpoint.last_run_at = timezone.now()
    checkpoint.save(update_fields=["last_run_at"])

    stats: dict[str, str | int] = {
        "source": adapter.source,
        "items_processed": len(items),
        "opportunities": opportunities,
        "discarded": len(items) - opportunities,
    }
    logger.info("ingest_from_adapter complete: %s", stats)
    return stats
