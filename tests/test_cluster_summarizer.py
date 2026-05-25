"""Tests for the cluster title/summary generator.

Covers:
* The cluster_summary prompt loads + validates.
* The summarizer parses a mocked Anthropic response into a TitleSummary.
* Loud failure when the model returns no parsed output.
* Refinement task gates: multi-item gets summarized; singletons get skipped;
  small drift doesn't re-bill.
* Singleton clusters get their title from the underlying item title at
  creation time (no LLM call needed).

The Anthropic SDK is mocked throughout — no live network calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.utils import timezone

from agents import prompts as prompt_loader
from clusters import clustering
from clusters.models import EMBEDDING_DIM, Cluster, ClusterItem, ClusterStatus, Source
from clusters.summarizer import TitleSummary, generate_title_and_summary
from clusters.tasks import refine_clusters_nightly

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _vec() -> list[float]:
    return [0.01] * EMBEDDING_DIM


def _make_cluster(*, size: int = 2, title: str | None = None) -> Cluster:
    now = timezone.now()
    cluster = Cluster.objects.create(
        status=ClusterStatus.PENDING,
        size=size,
        first_seen_at=now,
        last_seen_at=now,
        sources=[Source.HACKER_NEWS],
        centroid_embedding=_vec(),
        classifier_score=0.8,
        title=title,
    )
    for i in range(size):
        ClusterItem.objects.create(
            cluster=cluster,
            source=Source.HACKER_NEWS,
            source_item_id=f"hn-{cluster.id}-{i}",
            url=f"https://news.ycombinator.com/item?id={i}",
            title=f"Item title {i}",
            posted_at=now,
            raw_text=f"Body of item {i}",
            snippet=f"Snippet {i}",
            classifier_verdict="opportunity",
            classifier_confidence=0.8,
            embedding=_vec(),
            added_to_cluster_at=now,
            assigned_at=now,
        )
    return cluster


def _fake_response(title: str, summary: str) -> SimpleNamespace:
    from clusters.summarizer import _ModelResponse  # noqa: PLC0415

    return SimpleNamespace(
        parsed_output=_ModelResponse(title=title, summary=summary),
        usage=SimpleNamespace(input_tokens=210, output_tokens=42, cache_read_input_tokens=0),
        stop_reason="end_turn",
    )


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def test_cluster_summary_prompt_loads_with_frontmatter():
    prompt = prompt_loader.load_prompt("cluster_summary", "system")
    assert prompt.frontmatter.get("schema_version") == "1.0"
    assert prompt.frontmatter.get("description")
    assert prompt.content.strip().startswith("# Role")
    assert len(prompt.hash) == 64


# ---------------------------------------------------------------------------
# generate_title_and_summary
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_generate_title_and_summary_parses_mocked_response(monkeypatch):
    cluster = _make_cluster(size=3)

    fake_client = MagicMock()
    fake_client.messages.parse.return_value = _fake_response(
        title="Self-hosted Stripe alternative for solo SaaS",
        summary="Solo founders want a self-hosted payments stack without per-transaction lock-in.",
    )
    monkeypatch.setattr("clusters.summarizer.get_client", lambda: fake_client)

    result = generate_title_and_summary(cluster)
    assert isinstance(result, TitleSummary)
    assert result.title == "Self-hosted Stripe alternative for solo SaaS"
    assert result.summary.startswith("Solo founders")
    assert result.input_tokens == 210
    assert result.item_count_used == 3
    assert len(result.prompt_hash) == 64

    # Cache_control breakpoint is set on the system prompt.
    _, kwargs = fake_client.messages.parse.call_args
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.django_db
def test_generate_raises_when_model_returns_no_parsed_output(monkeypatch):
    cluster = _make_cluster(size=2)

    bad = _fake_response("ignored title", "ignored body — long enough for validation")
    bad.parsed_output = None
    bad.stop_reason = "refusal"
    fake_client = MagicMock()
    fake_client.messages.parse.return_value = bad
    monkeypatch.setattr("clusters.summarizer.get_client", lambda: fake_client)

    with pytest.raises(RuntimeError, match="no parsed output"):
        generate_title_and_summary(cluster)


@pytest.mark.django_db
def test_generate_raises_when_cluster_has_no_items():
    # Cluster with size > 0 but no actual items rows — guards against
    # accidentally summarizing a cluster whose items were deleted.
    cluster = Cluster.objects.create(
        status=ClusterStatus.PENDING,
        size=2,
        first_seen_at=timezone.now(),
        last_seen_at=timezone.now(),
        sources=[Source.HACKER_NEWS],
        centroid_embedding=_vec(),
        classifier_score=0.8,
    )
    with pytest.raises(ValueError, match="no items"):
        generate_title_and_summary(cluster)


# ---------------------------------------------------------------------------
# Refinement task — gating + side effects
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_refine_titles_multi_item_clusters_only(monkeypatch):
    multi = _make_cluster(size=3)
    singleton = _make_cluster(size=1)

    fake_client = MagicMock()
    fake_client.messages.parse.return_value = _fake_response(
        title="The labeled need", summary="A one-sentence summary of the need."
    )
    monkeypatch.setattr("clusters.summarizer.get_client", lambda: fake_client)

    stats = refine_clusters_nightly()
    # Exactly one cluster was titled (the multi-item one); the singleton was
    # skipped at the size gate. Earlier refinement steps (centroid recompute,
    # orphan reassignment) may have shuffled item counts, so we assert on the
    # gating behavior rather than a hardcoded post-refine size.
    assert stats["titles_regenerated"] == 1
    assert stats["titles_skipped_too_small"] >= 1

    multi.refresh_from_db()
    singleton.refresh_from_db()
    assert multi.title == "The labeled need"
    assert multi.last_titled_size == multi.size  # snapshot taken at title time
    # The singleton fixture was constructed directly (not via
    # assign_item_to_cluster), so its last_titled_size starts as None and
    # the refinement task must NOT touch it — the gate skips size<2.
    assert singleton.last_titled_size is None


@pytest.mark.django_db
def test_refine_skips_clusters_with_unchanged_size(monkeypatch):
    # Cluster already titled, size unchanged → no LLM call.
    cluster = _make_cluster(size=4, title="Existing title")
    cluster.last_titled_size = 4
    cluster.save(update_fields=["last_titled_size"])

    fake_client = MagicMock()
    monkeypatch.setattr("clusters.summarizer.get_client", lambda: fake_client)

    stats = refine_clusters_nightly()
    assert stats["titles_regenerated"] == 0
    assert stats["titles_skipped_size_unchanged"] >= 1
    # Crucial: the model was NOT invoked.
    fake_client.messages.parse.assert_not_called()


@pytest.mark.django_db
def test_refine_regenerates_when_size_drifted_past_threshold(monkeypatch):
    # Cluster grew 4 → 5 (25% drift, above 20% threshold). Should re-title.
    cluster = _make_cluster(size=5, title="Stale title from when size was 4")
    cluster.last_titled_size = 4
    cluster.save(update_fields=["last_titled_size"])

    fake_client = MagicMock()
    fake_client.messages.parse.return_value = _fake_response(
        title="Fresh title at size 5", summary="Updated summary reflecting new members."
    )
    monkeypatch.setattr("clusters.summarizer.get_client", lambda: fake_client)

    stats = refine_clusters_nightly()
    assert stats["titles_regenerated"] == 1
    cluster.refresh_from_db()
    assert cluster.title == "Fresh title at size 5"
    assert cluster.last_titled_size == 5


@pytest.mark.django_db
def test_refine_skips_small_drift_within_threshold(monkeypatch):
    # Cluster grew 10 → 11 (10% drift, below threshold). Skip.
    cluster = _make_cluster(size=11, title="Title from size 10")
    cluster.last_titled_size = 10
    cluster.save(update_fields=["last_titled_size"])

    fake_client = MagicMock()
    monkeypatch.setattr("clusters.summarizer.get_client", lambda: fake_client)

    stats = refine_clusters_nightly()
    assert stats["titles_regenerated"] == 0
    fake_client.messages.parse.assert_not_called()


# ---------------------------------------------------------------------------
# Singleton title fallback (no LLM call)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_new_singleton_cluster_inherits_item_title():
    """When clustering creates a new singleton, the cluster's title should
    match the originating item's title — no LLM call, just a verbatim copy.
    """
    item = ClusterItem(
        source=Source.HACKER_NEWS,
        source_item_id="hn-test-1",
        url="https://example.com",
        title="Resume parsers that actually preserve formatting",
        posted_at=timezone.now(),
        raw_text="full body",
        snippet="...",
        classifier_verdict="opportunity",
        classifier_confidence=0.8,
        embedding=_vec(),
    )
    cluster = clustering.assign_item_to_cluster(item)
    assert cluster.size == 1
    assert cluster.title == "Resume parsers that actually preserve formatting"
    assert cluster.last_titled_size == 1


@pytest.mark.django_db
def test_singleton_with_no_item_title_leaves_cluster_title_null():
    """An item with no title → cluster.title stays NULL (rather than empty
    string) so downstream code can rely on ``title or fallback``.
    """
    item = ClusterItem(
        source=Source.HACKER_NEWS,
        source_item_id="hn-test-2",
        url="https://example.com",
        title=None,
        posted_at=timezone.now(),
        raw_text="full body",
        snippet="...",
        classifier_verdict="opportunity",
        classifier_confidence=0.8,
        embedding=_vec(),
    )
    cluster = clustering.assign_item_to_cluster(item)
    assert cluster.title is None
