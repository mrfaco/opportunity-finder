"""Backfill historical items from a source beyond the normal incremental window.

The regular ``ingest_source`` task pulls items posted after the per-source
``IngestionCheckpoint`` — it only moves forward. This command does the
opposite: pull an explicit time window from the adapter, dedup against
``ClusterItem.source_item_id`` so we never double-count or hit the unique
constraint, and run only the new items through the classify → embed → cluster
pipeline. The checkpoint is left alone (it already reflects the newest seen).

Cost: each item runs through the live classifier (~$0.002 per Haiku call).
A 30-day Ask HN backfill is typically ~$1.00.

    python manage.py backfill_source hacker_news --days 30
    python manage.py backfill_source hacker_news --days 7   # short window

Discards from prior runs are not tracked by source_item_id (only opportunities
are persisted as ``ClusterItem`` rows), so items in the overlap with an earlier
ingest may be re-classified. In practice this is a handful of items at the
boundary — negligible cost vs. the value of guaranteed-disjoint operation.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from clusters.models import ClusterItem
from ingestion.adapters.base import SourceAdapter
from ingestion.adapters.hacker_news import HackerNewsAdapter
from ingestion.pipeline import _process_item

_ADAPTERS: dict[str, type[SourceAdapter]] = {
    HackerNewsAdapter.source: HackerNewsAdapter,
}


class Command(BaseCommand):
    help = "Backfill historical items from a source over a specified day window."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "source",
            help=f"Adapter key. One of: {sorted(_ADAPTERS)}.",
        )
        parser.add_argument(
            "--days",
            type=int,
            required=True,
            help="How many days back from now to pull.",
        )

    def handle(self, *args: Any, source: str, days: int, **options: Any) -> None:
        adapter_cls = _ADAPTERS.get(source)
        if adapter_cls is None:
            raise CommandError(
                f"No ingestion adapter registered for source {source!r}. "
                f"Known sources: {sorted(_ADAPTERS)}"
            )
        if days <= 0:
            raise CommandError("--days must be positive")

        since = timezone.now() - timedelta(days=days)
        existing_ids = set(
            ClusterItem.objects.filter(source=source).values_list("source_item_id", flat=True)
        )
        self.stdout.write(
            f"Backfilling {source} from {since.isoformat()}; "
            f"{len(existing_ids)} existing items will be skipped."
        )

        adapter = adapter_cls()
        fetched = list(adapter.fetch_new_items(since=since))
        to_process = [i for i in fetched if i.source_item_id not in existing_ids]
        # Oldest first — matches the regular pipeline so per-item failures
        # leave a coherent partial result if the run is interrupted.
        to_process.sort(key=lambda i: i.posted_at)
        self.stdout.write(
            f"Fetched {len(fetched)} items in window; processing {len(to_process)} new."
        )

        opportunities = 0
        for i, item in enumerate(to_process, 1):
            if _process_item(item):
                opportunities += 1
            if i % 50 == 0:
                self.stdout.write(
                    f"  ... {i}/{len(to_process)} processed, {opportunities} opportunities"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill complete: {len(to_process)} processed, "
                f"{opportunities} opportunities, {len(to_process) - opportunities} discarded."
            )
        )
