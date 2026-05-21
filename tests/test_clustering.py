"""Online clustering: singleton creation + nearest-centroid joining."""

from __future__ import annotations

import numpy as np
import pytest
from django.utils import timezone

from clusters import clustering
from clusters.models import (
    EMBEDDING_DIM,
    ClassifierVerdict,
    Cluster,
    ClusterItem,
    ClusterStatus,
    Source,
)


def _unit(vec: list[float]) -> list[float]:
    arr = np.array(vec, dtype=float)
    arr /= np.linalg.norm(arr) + 1e-12
    return arr.tolist()


def _item(
    embedding: list[float],
    source: str = Source.HACKER_NEWS,
    source_item_id: str = "1",
) -> ClusterItem:
    now = timezone.now()
    return ClusterItem(
        source=source,
        source_item_id=source_item_id,
        url=f"https://example.com/{source_item_id}",
        title=f"Item {source_item_id}",
        posted_at=now,
        raw_text="some raw text",
        snippet="some raw text",
        classifier_verdict=ClassifierVerdict.OPPORTUNITY,
        classifier_confidence=0.9,
        embedding=embedding,
        added_to_cluster_at=now,
        assigned_at=now,
    )


@pytest.mark.django_db
def test_new_item_creates_singleton_when_no_clusters():
    embedding = _unit([1, 0] + [0] * (EMBEDDING_DIM - 2))
    item = _item(embedding)
    cluster = clustering.assign_item_to_cluster(item)
    item.save()

    assert cluster.size == 1
    assert cluster.status == ClusterStatus.PENDING
    assert Cluster.objects.count() == 1


@pytest.mark.django_db
def test_similar_item_joins_existing_cluster(settings):
    settings.CLUSTER_JOIN_THRESHOLD = 0.75

    near = _unit([1, 0] + [0] * (EMBEDDING_DIM - 2))
    very_near = _unit([0.95, 0.05] + [0] * (EMBEDDING_DIM - 2))

    first = _item(near, source_item_id="a")
    clustering.assign_item_to_cluster(first)
    first.save()

    second = _item(very_near, source_item_id="b")
    cluster = clustering.assign_item_to_cluster(second)
    second.save()

    assert Cluster.objects.count() == 1
    assert cluster.size == 2


@pytest.mark.django_db
def test_dissimilar_item_creates_new_cluster(settings):
    settings.CLUSTER_JOIN_THRESHOLD = 0.75
    settings.CLUSTER_RECENCY_DAYS = 90

    far_a = _unit([1, 0, 0] + [0] * (EMBEDDING_DIM - 3))
    far_b = _unit([0, 0, 1] + [0] * (EMBEDDING_DIM - 3))

    first = _item(far_a, source_item_id="a")
    clustering.assign_item_to_cluster(first)
    first.save()

    second = _item(far_b, source_item_id="b")
    clustering.assign_item_to_cluster(second)
    second.save()

    assert Cluster.objects.count() == 2
