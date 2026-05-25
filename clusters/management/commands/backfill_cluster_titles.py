"""One-shot backfill: title every cluster that doesn't have one yet.

Two paths:

* **Singletons** (size 1) — copy the item's own title verbatim onto the
  cluster. Free, no LLM call. Matches the cluster-creation behavior in
  ``clusters.clustering.assign_item_to_cluster`` so legacy clusters
  created before that change line up with new ones.
* **Multi-item clusters** (size >= 2) — call the Haiku summarizer at
  ``clusters.summarizer.generate_title_and_summary``. Same code path the
  nightly refinement task uses; this command just runs it across the
  existing untitled set.

By default skips clusters that already have a title. Pass ``--force`` to
re-title everything (useful after a prompt change). Pass ``--dry-run`` to
preview without writing or making any API calls.

Usage:

    python manage.py backfill_cluster_titles
    python manage.py backfill_cluster_titles --force --multi-only
    python manage.py backfill_cluster_titles --dry-run
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db.models import Q

from clusters.models import Cluster
from clusters.summarizer import generate_title_and_summary


class Command(BaseCommand):
    help = "Backfill cluster titles and summaries (singletons from item; multi via Haiku)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-title even clusters that already have a title.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing or calling the model.",
        )
        parser.add_argument(
            "--multi-only",
            action="store_true",
            help="Skip singletons; only process clusters with size >= 2.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Cap the number of multi-item LLM calls (singletons are always free).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        force: bool = options["force"]
        dry: bool = options["dry_run"]
        multi_only: bool = options["multi_only"]
        limit: int | None = options["limit"]

        base = Cluster.active.all()
        if not force:
            base = base.filter(Q(title__isnull=True) | Q(title__exact=""))

        singletons = list(base.filter(size=1).select_related())
        multi = list(base.filter(size__gte=2).select_related())

        self.stdout.write(
            f"Found {len(singletons)} untitled singleton(s) and "
            f"{len(multi)} untitled multi-item cluster(s)."
        )

        # ----- Singletons: copy from their sole item. Free. -----
        if not multi_only:
            singleton_count = self._backfill_singletons(singletons, dry=dry)
            self.stdout.write(self.style.SUCCESS(f"Singletons titled from item: {singleton_count}"))

        # ----- Multi-item: Haiku call per cluster, gated by --limit. -----
        if limit is not None and len(multi) > limit:
            self.stdout.write(
                f"Capping multi-item set at --limit={limit} (would otherwise process {len(multi)})."
            )
            multi = multi[:limit]

        multi_count = self._backfill_multi(multi, dry=dry)
        self.stdout.write(
            self.style.SUCCESS(f"Multi-item clusters titled via Haiku: {multi_count}")
        )

    def _backfill_singletons(self, clusters: list[Cluster], *, dry: bool) -> int:
        n = 0
        for c in clusters:
            item = c.items.order_by("-classifier_confidence").first()
            if item is None or not item.title:
                continue
            if dry:
                self.stdout.write(f"  [dry] {c.id} ← {item.title[:80]!r}")
            else:
                c.title = item.title
                c.last_titled_size = 1
                c.save(update_fields=["title", "last_titled_size", "updated_at"])
            n += 1
        return n

    def _backfill_multi(self, clusters: list[Cluster], *, dry: bool) -> int:
        n = 0
        for c in clusters:
            if dry:
                self.stdout.write(f"  [dry] would summarize cluster {c.id} (size={c.size})")
                n += 1
                continue
            result = generate_title_and_summary(c)
            c.title = result.title
            c.summary = result.summary
            c.last_titled_size = c.size
            c.save(update_fields=["title", "summary", "last_titled_size", "updated_at"])
            self.stdout.write(
                f"  {c.id}  size={c.size}  in/out={result.input_tokens}/{result.output_tokens}  "
                f"{result.latency_ms}ms  → {result.title!r}"
            )
            n += 1
        return n
