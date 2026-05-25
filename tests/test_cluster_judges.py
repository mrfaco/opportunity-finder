"""Tests for the cluster-refinement LLM judges.

Covers:
* The cluster_judge/merge prompt loads + validates.
* ``judge_merge`` parses a mocked Anthropic response into a MergeVerdict.
* Loud failure when the model returns no parsed output.
* Loud failure on empty clusters (precondition).
* ``refine_clusters_nightly`` populates llm_judge_* on each created
  ClusterMergeProposal — replacing the previous "leave NULL" behavior.

The Anthropic SDK is mocked throughout — no live network calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.utils import timezone

from agents import prompts as prompt_loader
from clusters.judges import MergeVerdict, judge_merge
from clusters.models import (
    EMBEDDING_DIM,
    Cluster,
    ClusterItem,
    ClusterMergeProposal,
    ClusterStatus,
    ProposalStatus,
    Source,
)
from clusters.tasks import refine_clusters_nightly

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _vec(seed: float = 0.01) -> list[float]:
    """Deterministic embedding seeded by a scalar — different seeds → non-identical clusters.

    Identical vectors across clusters would make merge-candidate detection
    nondeterministic in earlier refinement steps and pollute these tests.
    """
    return [seed] * EMBEDDING_DIM


def _make_cluster(*, size: int = 3, title: str = "A cluster", seed: float = 0.01) -> Cluster:
    now = timezone.now()
    cluster = Cluster.objects.create(
        status=ClusterStatus.PENDING,
        size=size,
        first_seen_at=now,
        last_seen_at=now,
        sources=[Source.HACKER_NEWS],
        centroid_embedding=_vec(seed),
        classifier_score=0.8,
        title=title,
        summary=f"Summary of {title}",
    )
    for i in range(size):
        ClusterItem.objects.create(
            cluster=cluster,
            source=Source.HACKER_NEWS,
            source_item_id=f"hn-{cluster.id}-{i}",
            url=f"https://news.ycombinator.com/item?id={i}",
            title=f"{title} — item {i}",
            posted_at=now,
            raw_text="body",
            snippet=f"snippet {i}",
            classifier_verdict="opportunity",
            classifier_confidence=0.85,
            embedding=_vec(seed),
            added_to_cluster_at=now,
            assigned_at=now,
        )
    return cluster


def _fake_response(
    verdict: bool,
    confidence: float = 0.87,
    reasoning: str = "they describe the same workflow being broken",
) -> SimpleNamespace:
    from clusters.judges import _MergeResponse  # noqa: PLC0415

    return SimpleNamespace(
        parsed_output=_MergeResponse(verdict=verdict, confidence=confidence, reasoning=reasoning),
        usage=SimpleNamespace(input_tokens=420, output_tokens=58, cache_read_input_tokens=0),
        stop_reason="end_turn",
    )


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def test_merge_judge_prompt_loads_with_frontmatter():
    prompt = prompt_loader.load_prompt("cluster_judge", "merge")
    assert prompt.frontmatter.get("schema_version") == "1.0"
    assert prompt.frontmatter.get("description")
    assert "merge" in prompt.frontmatter["description"].lower()
    assert len(prompt.hash) == 64


# ---------------------------------------------------------------------------
# judge_merge
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_judge_merge_parses_positive_verdict(monkeypatch):
    a = _make_cluster(title="approval queues for AI agents", seed=0.01)
    b = _make_cluster(title="human-in-the-loop gates for agent runs", seed=0.011)

    fake_client = MagicMock()
    fake_client.messages.parse.return_value = _fake_response(
        verdict=True,
        confidence=0.91,
        reasoning="Both describe operator approval flows for production agents.",
    )
    monkeypatch.setattr("clusters.judges.get_client", lambda: fake_client)

    result = judge_merge(a, b, centroid_similarity=0.86)
    assert isinstance(result, MergeVerdict)
    assert result.verdict is True
    assert result.confidence == 0.91
    assert result.input_tokens == 420
    assert len(result.prompt_hash) == 64
    # cache_control breakpoint is set on the system prompt (matches the
    # filter + summarizer call patterns).
    _, kwargs = fake_client.messages.parse.call_args
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.django_db
def test_judge_merge_parses_negative_verdict(monkeypatch):
    a = _make_cluster(title="deployment cost dashboards", seed=0.02)
    b = _make_cluster(title="local-dev environment switching", seed=0.021)

    fake_client = MagicMock()
    fake_client.messages.parse.return_value = _fake_response(
        verdict=False,
        confidence=0.78,
        reasoning="A is about deploys, B is about local dev — different workflows.",
    )
    monkeypatch.setattr("clusters.judges.get_client", lambda: fake_client)

    result = judge_merge(a, b, centroid_similarity=0.83)
    assert result.verdict is False
    assert "different workflows" in result.reasoning


@pytest.mark.django_db
def test_judge_merge_raises_when_model_returns_no_parsed_output(monkeypatch):
    a = _make_cluster(seed=0.03)
    b = _make_cluster(seed=0.031)

    bad = _fake_response(verdict=True)
    bad.parsed_output = None
    bad.stop_reason = "refusal"
    fake_client = MagicMock()
    fake_client.messages.parse.return_value = bad
    monkeypatch.setattr("clusters.judges.get_client", lambda: fake_client)

    with pytest.raises(RuntimeError, match="no parsed output"):
        judge_merge(a, b, centroid_similarity=0.85)


@pytest.mark.django_db
def test_judge_merge_raises_on_empty_cluster():
    a = Cluster.objects.create(
        status=ClusterStatus.PENDING,
        size=2,
        first_seen_at=timezone.now(),
        last_seen_at=timezone.now(),
        sources=[Source.HACKER_NEWS],
        centroid_embedding=_vec(),
        classifier_score=0.7,
    )
    b = _make_cluster(seed=0.04)
    with pytest.raises(ValueError, match="no items"):
        judge_merge(a, b, centroid_similarity=0.9)


# ---------------------------------------------------------------------------
# Wiring into refine_clusters_nightly
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_refine_runs_merge_judge_and_persists_verdict(monkeypatch, settings):
    """When refinement queues a merge proposal, the judge's verdict +
    confidence + reasoning must end up on the row (replacing the
    previous leave-NULL behavior)."""
    # Force the merge threshold low so the test fixtures cross it.
    settings.CLUSTER_MERGE_THRESHOLD = 0.0
    # Disable split path so it doesn't interfere.
    settings.SPLIT_SIZE_THRESHOLD = 999

    # Two clusters with overlapping tag-set so find_merge_candidates
    # doesn't skip them at the tag filter.
    a = _make_cluster(title="A cluster", seed=0.5)
    b = _make_cluster(title="B cluster", seed=0.51)
    a.category_tags = ["dev-tools"]
    b.category_tags = ["dev-tools"]
    a.save(update_fields=["category_tags"])
    b.save(update_fields=["category_tags"])

    fake_client = MagicMock()
    fake_client.messages.parse.return_value = _fake_response(
        verdict=True,
        confidence=0.92,
        reasoning="Both describe the same operator-approval need.",
    )
    monkeypatch.setattr("clusters.judges.get_client", lambda: fake_client)
    # Also stub the summarizer's client — step 5 fires on multi-item clusters.
    monkeypatch.setattr(
        "clusters.summarizer.get_client",
        lambda: MagicMock(
            messages=MagicMock(
                parse=MagicMock(
                    return_value=SimpleNamespace(
                        parsed_output=SimpleNamespace(
                            title="X", summary="Y is a one-sentence summary."
                        ),
                        usage=SimpleNamespace(
                            input_tokens=10, output_tokens=10, cache_read_input_tokens=0
                        ),
                        stop_reason="end_turn",
                    )
                )
            )
        ),
    )

    stats = refine_clusters_nightly()
    assert stats["merge_proposals_queued"] >= 1

    proposal = ClusterMergeProposal.objects.filter(status=ProposalStatus.PENDING_REVIEW).first()
    assert proposal is not None
    assert proposal.llm_judge_verdict is True
    assert proposal.llm_judge_confidence == 0.92
    assert "operator-approval" in proposal.llm_judge_reasoning


@pytest.mark.django_db
def test_refine_skips_judge_when_proposal_already_pending(monkeypatch, settings):
    """A pre-existing pending proposal blocks the duplicate, so the judge
    must NOT be called again on the same pair."""
    settings.CLUSTER_MERGE_THRESHOLD = 0.0
    settings.SPLIT_SIZE_THRESHOLD = 999

    a = _make_cluster(title="A", seed=0.6)
    b = _make_cluster(title="B", seed=0.61)

    ClusterMergeProposal.objects.create(
        cluster_a=a,
        cluster_b=b,
        centroid_similarity=0.9,
        status=ProposalStatus.PENDING_REVIEW,
    )

    fake_client = MagicMock()
    monkeypatch.setattr("clusters.judges.get_client", lambda: fake_client)
    monkeypatch.setattr(
        "clusters.summarizer.get_client",
        lambda: MagicMock(
            messages=MagicMock(
                parse=MagicMock(
                    return_value=SimpleNamespace(
                        parsed_output=SimpleNamespace(
                            title="X", summary="Y is a one-sentence summary."
                        ),
                        usage=SimpleNamespace(
                            input_tokens=10, output_tokens=10, cache_read_input_tokens=0
                        ),
                        stop_reason="end_turn",
                    )
                )
            )
        ),
    )

    stats = refine_clusters_nightly()
    assert stats["merge_proposals_queued"] == 0
    fake_client.messages.parse.assert_not_called()
