"""Re-embed every ClusterItem and recompute affected cluster centroids.

Run this after changing ``EMBEDDING_MODEL`` so the stored vectors and the
new model agree — mixing vectors from two models in one pgvector column
makes cosine distances meaningless.

    python manage.py reembed_cluster_items

Items are embedded in batches; each touched cluster's centroid is recomputed
from its members afterwards.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from clusters.clustering import recompute_centroid
from clusters.embeddings import embed_texts
from clusters.models import Cluster, ClusterItem

_CHUNK = 128


class Command(BaseCommand):
    help = "Re-embed all ClusterItem rows and recompute affected centroids."

    def handle(self, *args: Any, **options: Any) -> None:
        items = list(ClusterItem.objects.all())
        if not items:
            self.stdout.write("No ClusterItem rows to re-embed.")
            return

        cluster_ids: set = set()
        reembedded = 0

        for start in range(0, len(items), _CHUNK):
            chunk = items[start : start + _CHUNK]
            vectors = embed_texts([item.raw_text for item in chunk])
            with transaction.atomic():
                for item, vector in zip(chunk, vectors, strict=True):
                    item.embedding = vector
                    item.assigned_at = timezone.now()
                    item.save(update_fields=["embedding", "assigned_at", "updated_at"])
                    cluster_ids.add(item.cluster_id)
            reembedded += len(chunk)
            self.stdout.write(f"Re-embedded {reembedded}/{len(items)} item(s)...")

        for cluster in Cluster.objects.filter(id__in=cluster_ids):
            recompute_centroid(cluster)

        self.stdout.write(
            self.style.SUCCESS(
                f"Re-embedded {reembedded} item(s); recomputed " f"{len(cluster_ids)} centroid(s)."
            )
        )
