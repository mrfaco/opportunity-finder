"""Re-cluster every ``ClusterItem`` at a chosen ``CLUSTER_JOIN_THRESHOLD``.

The online clustering at ingestion time is a one-way trip — each item is
assigned to the nearest existing cluster (or spawned as a singleton) at
the configured threshold and never revisited. When you tune the
threshold (or change the embedding model), the stored cluster shape no
longer reflects what the algorithm would produce today.

This command tears down the cluster set and rebuilds it from scratch by
walking the existing items in chronological order through the same
``assign_item_to_cluster`` path that ingestion uses. Investigated
clusters are *preserved* (deleting them would cascade-delete the
``Investigation`` rows that reference them); their items are pushed
back through the assignment so they may end up elsewhere.

Idempotency note: the online algorithm is order-dependent. Two runs
with the same threshold over the same items produce the same shape
(items are sorted by ``posted_at``), so the command is reproducible.

Usage:

    python manage.py re_cluster_items                    # default 0.65 threshold
    python manage.py re_cluster_items --threshold 0.70   # explicit
    python manage.py re_cluster_items --dry-run          # report current shape, change nothing
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from clusters.clustering import assign_item_to_cluster, recompute_centroid
from clusters.models import EMBEDDING_DIM, Cluster, ClusterItem, ClusterStatus
from investigations.models import Investigation

_DEFAULT_THRESHOLD = 0.65


class Command(BaseCommand):
    help = "Re-cluster every ClusterItem at a chosen CLUSTER_JOIN_THRESHOLD."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--threshold",
            type=float,
            default=_DEFAULT_THRESHOLD,
            help=(
                "Cosine similarity threshold for joining the nearest cluster. "
                f"Default {_DEFAULT_THRESHOLD}. The current configured value is "
                "in settings.CLUSTER_JOIN_THRESHOLD."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report current cluster shape without mutating anything.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        new_threshold: float = options["threshold"]
        dry: bool = options["dry_run"]

        items_before = ClusterItem.objects.count()
        clusters_before = Cluster.active.count()
        size_dist_before = self._size_distribution()

        self.stdout.write("=== Before ===")
        self.stdout.write(f"  Items: {items_before}")
        self.stdout.write(f"  Active clusters: {clusters_before}")
        self.stdout.write(f"  Size dist: {size_dist_before}")
        self.stdout.write(f"  Threshold currently configured: {settings.CLUSTER_JOIN_THRESHOLD}")
        self.stdout.write(f"  Threshold for this run: {new_threshold}")

        if dry:
            self.stdout.write(self.style.WARNING("\n(--dry-run; no changes made)"))
            return

        with transaction.atomic():
            self._rebuild(new_threshold)

        clusters_after = Cluster.active.count()
        size_dist_after = self._size_distribution()
        multi_before = sum(n for s, n in size_dist_before.items() if s >= 2)
        multi_after = sum(n for s, n in size_dist_after.items() if s >= 2)

        self.stdout.write(self.style.SUCCESS("\n=== After ==="))
        self.stdout.write(f"  Active clusters: {clusters_after}")
        self.stdout.write(f"  Size dist: {size_dist_after}")
        self.stdout.write(f"  Multi-item clusters: {multi_before} -> {multi_after}")
        self.stdout.write(
            self.style.WARNING(
                "\nReminder: investigated clusters were preserved (their items may have "
                "moved). Run refinement to regenerate titles + judge merge candidates."
            )
        )

    def _size_distribution(self) -> dict[int, int]:
        sizes: dict[int, int] = {}
        for s in Cluster.active.values_list("size", flat=True):
            sizes[s] = sizes.get(s, 0) + 1
        return dict(sorted(sizes.items()))

    def _rebuild(self, new_threshold: float) -> None:
        # Override the threshold for the duration of this transaction.
        # ``assign_item_to_cluster`` reads it via ``settings.CLUSTER_JOIN_THRESHOLD``
        # at call time, so the monkey-patch is picked up. The command runs
        # synchronously in this Python process; Celery workers and other
        # processes are unaffected.
        original_threshold = settings.CLUSTER_JOIN_THRESHOLD
        settings.CLUSTER_JOIN_THRESHOLD = new_threshold
        try:
            self._rebuild_inner()
        finally:
            settings.CLUSTER_JOIN_THRESHOLD = original_threshold

    def _rebuild_inner(self) -> None:
        # 1. Snapshot the protected cluster ids — those with at least one
        #    investigation referencing them. Deleting an investigated
        #    cluster would cascade-delete the investigation, which is
        #    historical work we must not lose.
        investigated_ids = set(Investigation.objects.values_list("cluster_id", flat=True))
        self.stdout.write(
            f"  Protecting {len(investigated_ids)} investigated cluster(s) from deletion."
        )

        # 2. Park every item in a sentinel cluster so we can delete the
        #    original clusters without losing the items themselves.
        #    (``ClusterItem.cluster`` is non-null with CASCADE delete, so
        #    we can't just orphan them.)
        sentinel = Cluster.objects.create(
            status=ClusterStatus.DISCARDED,
            size=0,
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
            sources=[],
            centroid_embedding=[0.0] * EMBEDDING_DIM,
            classifier_score=0.0,
            title="__sentinel_recluster__",
        )
        ClusterItem.objects.exclude(cluster_id=sentinel.id).update(cluster=sentinel)

        # 3. Delete every cluster that's not protected and not the sentinel.
        deleted, _ = Cluster.objects.exclude(pk__in={*investigated_ids, sentinel.id}).delete()
        self.stdout.write(f"  Deleted {deleted} non-investigated cluster row(s).")

        # 4. Reset the protected clusters' size + last_titled_size so that
        #    refinement will re-evaluate them. Their centroid stays — items
        #    may rejoin if they match — and recompute_centroid will fix it
        #    based on actual current members at the end.
        Cluster.objects.filter(pk__in=investigated_ids).update(size=0, last_titled_size=None)

        # 5. Re-assign each item chronologically. ``assign_item_to_cluster``
        #    mutates the item but doesn't save it; we save explicitly below.
        items_list = list(ClusterItem.objects.select_related("cluster").order_by("posted_at"))
        moved_existing = 0
        new_clusters_created = 0
        for item in items_list:
            before_active_ids = set(Cluster.active.values_list("id", flat=True))
            assign_item_to_cluster(item)
            item.save(
                update_fields=[
                    "cluster",
                    "added_to_cluster_at",
                    "assigned_at",
                    "updated_at",
                ]
            )
            after_active_ids = set(Cluster.active.values_list("id", flat=True))
            if after_active_ids - before_active_ids:
                new_clusters_created += 1
            else:
                moved_existing += 1
        self.stdout.write(
            f"  Re-assigned {len(items_list)} items: "
            f"{new_clusters_created} new singletons, {moved_existing} joined existing."
        )

        # 6. Sentinel should be empty now — every item was re-assigned.
        leftover = sentinel.items.count()
        if leftover:
            raise RuntimeError(
                f"Sentinel cluster has {leftover} item(s) left after re-assignment; "
                "this is a bug — the transaction will roll back."
            )
        sentinel.delete()

        # 7. Recompute centroids on every active cluster. The running-average
        #    updates inside assign_item_to_cluster accumulate drift; the
        #    full average from current members is what we want as the new
        #    baseline.
        for cluster in Cluster.active.all():
            recompute_centroid(cluster)
