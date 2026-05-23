"""Tests for the agent tool implementations.

Tools are dispatched through ``Tool.dispatch`` which runs Pydantic
validation on the input before calling the impl. These tests go through
that public path so the input/output schemas are exercised too.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from agents.tools import get_tool
from clusters.models import (
    EMBEDDING_DIM,
    ClassifierVerdict,
    Cluster,
    ClusterItem,
    ClusterStatus,
    Source,
)


def _vec() -> list[float]:
    return [0.01] * EMBEDDING_DIM


def _make_cluster(**overrides) -> Cluster:
    now = timezone.now()
    defaults = {
        "status": ClusterStatus.PENDING,
        "size": 0,
        "first_seen_at": now,
        "last_seen_at": now,
        "sources": [Source.HACKER_NEWS],
        "centroid_embedding": _vec(),
        "title": "Painful PDF tooling",
        "summary": "Multiple users want a better PDF merge experience",
        "category_tags": ["dev-tools"],
        "classifier_score": 0.9,
    }
    defaults.update(overrides)
    return Cluster.objects.create(**defaults)


def _make_item(cluster: Cluster, sid: str, confidence: float, posted_at=None) -> ClusterItem:
    now = posted_at or timezone.now()
    return ClusterItem.objects.create(
        cluster=cluster,
        source=Source.HACKER_NEWS,
        source_item_id=sid,
        url=f"https://news.ycombinator.com/item?id={sid}",
        title=f"Ask HN: item {sid}",
        author="someone",
        posted_at=now,
        raw_text=f"Full body for item {sid} — describes a real unmet need.",
        snippet=f"Snippet for item {sid}",
        classifier_verdict=ClassifierVerdict.OPPORTUNITY,
        classifier_confidence=confidence,
        embedding=_vec(),
        added_to_cluster_at=now,
        assigned_at=now,
    )


# ---------------------------------------------------------------------------
# query_cluster
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_query_cluster_returns_summary_and_key_items():
    cluster = _make_cluster()
    _make_item(cluster, "a", confidence=0.7)
    _make_item(cluster, "b", confidence=0.95)
    _make_item(cluster, "c", confidence=0.8)

    out = get_tool("query_cluster").dispatch({"cluster_id": str(cluster.id)})

    assert out.status == "success"
    assert out.cluster is not None
    assert out.cluster.id == str(cluster.id)
    assert out.cluster.title == "Painful PDF tooling"
    assert out.cluster.summary.startswith("Multiple users")
    assert out.cluster.sources == [Source.HACKER_NEWS]
    assert len(out.cluster.key_items) == 3
    # Ordered by classifier_confidence DESC.
    assert [i.source_item_id for i in out.cluster.key_items] == ["b", "c", "a"]
    # Items carry the public fields the agent needs.
    item = out.cluster.key_items[0]
    assert item.snippet.startswith("Snippet")
    assert item.url.endswith("?id=b")


@pytest.mark.django_db
def test_query_cluster_respects_max_items():
    cluster = _make_cluster()
    for i in range(8):
        _make_item(cluster, str(i), confidence=0.5 + i / 100)

    out = get_tool("query_cluster").dispatch({"cluster_id": str(cluster.id), "max_items": 3})

    assert out.status == "success"
    assert len(out.cluster.key_items) == 3


@pytest.mark.django_db
def test_query_cluster_not_found_for_unknown_id():
    out = get_tool("query_cluster").dispatch({"cluster_id": "00000000-0000-0000-0000-000000000000"})
    assert out.status == "not_found"
    assert out.cluster is None
    assert "No cluster" in out.error_reason


@pytest.mark.django_db
def test_query_cluster_not_found_for_invalid_uuid():
    out = get_tool("query_cluster").dispatch({"cluster_id": "not-a-uuid"})
    assert out.status == "not_found"
    assert out.cluster is None


def test_query_cluster_validation_fails_on_bad_input():
    # Missing required field -> dispatch returns validation_failed.
    out = get_tool("query_cluster").dispatch({})
    assert out.status == "validation_failed"


def test_query_cluster_definition_round_trips_to_json_schema():
    tool = get_tool("query_cluster")
    definition = tool.definition()
    assert definition.name == "query_cluster"
    assert definition.input_schema["type"] == "object"
    # The nested ClusterSummary should show up in the output schema's $defs.
    assert "ClusterSummary" in str(definition.output_schema)
