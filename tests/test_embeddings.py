"""Tests for the Voyage embedding provider and the re-embed backfill command.

The Voyage client is mocked everywhere — these tests never hit the API.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import voyageai.error
from django.core.management import call_command
from django.utils import timezone

from clusters import clustering, embeddings
from clusters.models import (
    EMBEDDING_DIM,
    ClassifierVerdict,
    Cluster,
    ClusterItem,
    ClusterStatus,
    Source,
)


def _vec(value: float = 0.01) -> list[float]:
    """A 1024-dim constant vector."""
    return [value] * EMBEDDING_DIM


def _fake_client(vectors: list[list[float]]):
    """A stand-in Voyage client whose .embed returns the given vectors."""
    client = SimpleNamespace()

    def embed(batch, model, input_type):  # noqa: ARG001
        # Return one vector per input, cycling through the supplied set.
        return SimpleNamespace(embeddings=[vectors[i % len(vectors)] for i in range(len(batch))])

    client.embed = embed
    return client


# ---------------------------------------------------------------------------
# embed_texts / embed_text
# ---------------------------------------------------------------------------
def test_embed_texts_returns_one_vector_per_input(monkeypatch):
    monkeypatch.setattr(embeddings, "get_voyage_client", lambda: _fake_client([_vec()]))
    out = embeddings.embed_texts(["a", "b", "c"])
    assert len(out) == 3
    assert all(len(v) == EMBEDDING_DIM for v in out)


def test_embed_texts_empty_returns_empty():
    # No client call at all for an empty input.
    assert embeddings.embed_texts([]) == []


def test_embed_texts_batches_beyond_chunk_size(monkeypatch):
    monkeypatch.setattr(embeddings, "_BATCH_SIZE", 2)
    calls = []

    def fake_get_client():
        client = SimpleNamespace()

        def embed(batch, model, input_type):  # noqa: ARG001
            calls.append(len(batch))
            return SimpleNamespace(embeddings=[_vec()] * len(batch))

        client.embed = embed
        return client

    monkeypatch.setattr(embeddings, "get_voyage_client", fake_get_client)
    out = embeddings.embed_texts(["a", "b", "c", "d", "e"])
    assert len(out) == 5
    assert calls == [2, 2, 1]  # three batches


def test_embed_texts_raises_on_wrong_dimension(monkeypatch):
    monkeypatch.setattr(embeddings, "get_voyage_client", lambda: _fake_client([[0.1] * 512]))
    with pytest.raises(RuntimeError, match="512-dim"):
        embeddings.embed_texts(["a"])


def test_embed_text_single(monkeypatch):
    monkeypatch.setattr(embeddings, "get_voyage_client", lambda: _fake_client([_vec(0.5)]))
    vec = embeddings.embed_text("hello")
    assert len(vec) == EMBEDDING_DIM
    assert vec[0] == 0.5


def test_get_voyage_client_requires_key(monkeypatch, settings):
    settings.VOYAGE_API_KEY = ""
    with pytest.raises(RuntimeError, match="VOYAGE_API_KEY"):
        embeddings.get_voyage_client()


def test_compute_embedding_delegates_to_provider(monkeypatch):
    monkeypatch.setattr(embeddings, "get_voyage_client", lambda: _fake_client([_vec(0.7)]))
    vec = clustering.compute_embedding("some text")
    assert len(vec) == EMBEDDING_DIM
    assert vec[0] == 0.7


# ---------------------------------------------------------------------------
# _embed_with_retry — RateLimitError backoff
# ---------------------------------------------------------------------------
def _flaky_client(rate_limit_count: int, then_vectors: list[list[float]]):
    """Client that raises RateLimitError N times, then returns vectors."""
    calls = {"n": 0}

    def embed(batch, model, input_type):  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] <= rate_limit_count:
            raise voyageai.error.RateLimitError("simulated rate limit")
        vecs = [then_vectors[i % len(then_vectors)] for i in range(len(batch))]
        return SimpleNamespace(embeddings=vecs)

    return SimpleNamespace(embed=embed), calls


def test_embed_retries_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: sleeps.append(s))
    client, calls = _flaky_client(rate_limit_count=2, then_vectors=[_vec()])
    monkeypatch.setattr(embeddings, "get_voyage_client", lambda: client)

    out = embeddings.embed_texts(["a"])
    assert len(out) == 1
    assert calls["n"] == 3  # two failures + one success
    assert sleeps == [2.0, 4.0]  # exponential: 2*1, 2*2


def test_embed_exhausts_retries_and_raises(monkeypatch):
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: None)
    client, calls = _flaky_client(rate_limit_count=999, then_vectors=[_vec()])
    monkeypatch.setattr(embeddings, "get_voyage_client", lambda: client)

    with pytest.raises(voyageai.error.RateLimitError):
        embeddings.embed_texts(["a"])
    # All MAX_RETRY_ATTEMPTS were used (5 retried + 1 final un-caught).
    assert calls["n"] == embeddings._MAX_RETRY_ATTEMPTS


def test_embed_does_not_retry_on_other_errors(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def embed(batch, model, input_type):  # noqa: ARG001
        calls["n"] += 1
        raise voyageai.error.AuthenticationError("bad key")

    monkeypatch.setattr(embeddings, "get_voyage_client", lambda: SimpleNamespace(embed=embed))

    with pytest.raises(voyageai.error.AuthenticationError):
        embeddings.embed_texts(["a"])
    assert calls["n"] == 1  # no retry — fail loud on the first attempt
    assert sleeps == []


def test_embed_backoff_capped_at_max(monkeypatch):
    """Wait values are capped by ``_MAX_RETRY_WAIT_S`` so a long backoff curve
    doesn't run away on a tight free-tier quota."""
    sleeps: list[float] = []
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(embeddings, "_BASE_RETRY_WAIT_S", 100.0)
    monkeypatch.setattr(embeddings, "_MAX_RETRY_WAIT_S", 10.0)
    client, _ = _flaky_client(rate_limit_count=999, then_vectors=[_vec()])
    monkeypatch.setattr(embeddings, "get_voyage_client", lambda: client)

    with pytest.raises(voyageai.error.RateLimitError):
        embeddings.embed_texts(["a"])
    assert all(s <= 10.0 for s in sleeps)


# ---------------------------------------------------------------------------
# reembed_cluster_items command
# ---------------------------------------------------------------------------
def _make_item(cluster: Cluster, embedding: list[float], sid: str) -> ClusterItem:
    now = timezone.now()
    return ClusterItem.objects.create(
        cluster=cluster,
        source=Source.HACKER_NEWS,
        source_item_id=sid,
        url=f"https://example.com/{sid}",
        posted_at=now,
        raw_text=f"raw text for {sid}",
        snippet=f"raw text for {sid}",
        classifier_verdict=ClassifierVerdict.OPPORTUNITY,
        classifier_confidence=0.9,
        embedding=embedding,
        added_to_cluster_at=now,
        assigned_at=now,
    )


@pytest.mark.django_db
def test_reembed_command_updates_vectors_and_centroid(monkeypatch):
    old = _vec(0.001)
    cluster = Cluster.objects.create(
        status=ClusterStatus.PENDING,
        size=2,
        first_seen_at=timezone.now(),
        last_seen_at=timezone.now(),
        sources=[Source.HACKER_NEWS],
        centroid_embedding=old,
    )
    item_a = _make_item(cluster, old, "a")
    item_b = _make_item(cluster, old, "b")

    new = _vec(0.5)
    monkeypatch.setattr("clusters.embeddings.get_voyage_client", lambda: _fake_client([new]))

    call_command("reembed_cluster_items")

    item_a.refresh_from_db()
    item_b.refresh_from_db()
    assert list(item_a.embedding) == new
    assert list(item_b.embedding) == new
    # Centroid recomputed from the new (normalized) member vectors.
    cluster.refresh_from_db()
    assert list(cluster.centroid_embedding) != old


@pytest.mark.django_db
def test_reembed_command_no_items_is_noop():
    # No ClusterItem rows — command should run cleanly without a client call.
    call_command("reembed_cluster_items")
