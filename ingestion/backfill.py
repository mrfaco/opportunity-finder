"""Pull a historical window from a source adapter and feed it through the pipeline.

The regular ingest path only moves forward — it pulls items posted after the
per-source ``IngestionCheckpoint``. Backfill is the opposite: explicit window,
dedup against existing ``ClusterItem`` rows (so reruns are safe and the unique
constraint never trips), checkpoint untouched (it already reflects the newest
seen).

Used by both the ``backfill_source`` management command and the
``backfill_source_task`` celery task — keeping the logic in one place so the
admin operations page and the CLI behave identically.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.utils import timezone

from clusters.models import ClusterItem
from ingestion.adapters.base import SourceAdapter
from ingestion.pipeline import _process_item

logger = logging.getLogger(__name__)


def backfill_from_adapter(adapter: SourceAdapter, days: int) -> dict[str, Any]:
    """Run a ``days``-day backfill through ``adapter``.

    Returns a stats dict with ``source``, ``fetched``, ``processed``,
    ``opportunities``, ``discarded``. Raises ``ValueError`` on
    non-positive ``days``.
    """
    if days <= 0:
        raise ValueError("days must be positive")

    since = timezone.now() - timedelta(days=days)
    source = adapter.source
    existing_ids = set(
        ClusterItem.objects.filter(source=source).values_list("source_item_id", flat=True)
    )
    fetched = list(adapter.fetch_new_items(since=since))
    to_process = [i for i in fetched if i.source_item_id not in existing_ids]
    # Oldest first — matches the regular pipeline so per-item failures leave a
    # coherent partial result if the run is interrupted.
    to_process.sort(key=lambda i: i.posted_at)

    opportunities = 0
    for item in to_process:
        if _process_item(item):
            opportunities += 1

    stats = {
        "source": source,
        "fetched": len(fetched),
        "processed": len(to_process),
        "opportunities": opportunities,
        "discarded": len(to_process) - opportunities,
    }
    logger.info("backfill_from_adapter complete: %s", stats)
    return stats
