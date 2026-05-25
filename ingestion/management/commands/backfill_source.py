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

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from ingestion.backfill import backfill_from_adapter
from ingestion.tasks import ADAPTERS


class Command(BaseCommand):
    help = "Backfill historical items from a source over a specified day window."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "source",
            help=f"Adapter key. One of: {sorted(ADAPTERS)}.",
        )
        parser.add_argument(
            "--days",
            type=int,
            required=True,
            help="How many days back from now to pull.",
        )

    def handle(self, *args: Any, source: str, days: int, **options: Any) -> None:
        adapter_cls = ADAPTERS.get(source)
        if adapter_cls is None:
            raise CommandError(
                f"No ingestion adapter registered for source {source!r}. "
                f"Known sources: {sorted(ADAPTERS)}"
            )
        if days <= 0:
            raise CommandError("--days must be positive")

        self.stdout.write(f"Backfilling {source} for {days}d ...")
        stats = backfill_from_adapter(adapter_cls(), days=days)
        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill complete: {stats['processed']} processed, "
                f"{stats['opportunities']} opportunities, "
                f"{stats['discarded']} discarded "
                f"(out of {stats['fetched']} fetched)."
            )
        )
