"""LLM judges for cluster-refinement proposals.

The nightly refinement task surfaces *candidates* — pairs of clusters
with above-threshold centroid similarity (merge), or oversized clusters
with high internal variance (split). The judges in this module turn
those candidates into structured verdicts a human can scan in admin.

The judge does NOT auto-apply. ``ClusterMergeProposal`` rows still
require an explicit human approval action; the judge's verdict +
confidence + reasoning are advisory metadata that lets the operator
prioritize the obvious-yes / obvious-no proposals before spending time
on the borderline ones.

Currently implements: ``judge_merge``. ``judge_split`` is a deliberate
follow-on — your dataset can't exercise the split path
(``SPLIT_SIZE_THRESHOLD`` is 30, biggest cluster is 10) so the code
would be speculative.
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field

from agents import prompts as prompt_loader
from clusters.models import Cluster
from core.llm import call_cheap_model

logger = logging.getLogger(__name__)

# Boolean + one or two short sentences — 256 tokens is plenty of headroom.
_MAX_TOKENS = 256

# Items per side passed to the judge. The judge needs enough signal to
# discriminate same-need from same-keywords, but doesn't benefit much from
# the long tail. Top-5 by classifier_confidence is empirically sufficient
# for this kind of binary judgment.
_REPRESENTATIVE_ITEMS_PER_SIDE = 5


class _MergeResponse(BaseModel):
    """Structured shape the model is constrained to return."""

    verdict: bool
    confidence: float = Field(ge=0.0, le=1.0)
    # Cap raised from 600 → 2000 chars. Haiku regularly produces dense
    # 800-1500 char reasoning when comparing two adjacent clusters; the
    # original limit truncated useful signal that the operator wants in
    # the admin pending-review queue.
    reasoning: str = Field(min_length=10, max_length=2000)


class MergeVerdict(BaseModel):
    """Public result type. Includes provenance for replay + audit."""

    verdict: bool
    confidence: float
    reasoning: str
    prompt_hash: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    latency_ms: int


def _cluster_payload(cluster: Cluster) -> dict:
    """Compact JSON-friendly view of one cluster for the judge prompt."""
    items = list(cluster.items.order_by("-classifier_confidence")[:_REPRESENTATIVE_ITEMS_PER_SIDE])
    return {
        "title": cluster.title or "",
        "summary": cluster.summary or "",
        "size": cluster.size,
        "items": [
            {
                "title": item.title or "",
                "snippet": item.snippet,
                "source": item.source,
                "confidence": round(item.classifier_confidence, 3),
            }
            for item in items
        ],
    }


def _build_user_content(cluster_a: Cluster, cluster_b: Cluster, centroid_similarity: float) -> str:
    payload = {
        "centroid_similarity": round(centroid_similarity, 4),
        "cluster_a": _cluster_payload(cluster_a),
        "cluster_b": _cluster_payload(cluster_b),
    }
    return json.dumps(payload, sort_keys=True, indent=2)


def judge_merge(cluster_a: Cluster, cluster_b: Cluster, centroid_similarity: float) -> MergeVerdict:
    """Decide whether two clusters describe the same underlying user need.

    Args:
        cluster_a, cluster_b: the candidate pair.
        centroid_similarity: the cosine similarity that put this pair in
            front of the judge. Passed through to the prompt as context;
            the judge is told it's a hint, not authority.

    Returns:
        ``MergeVerdict`` with the model's structured opinion.

    Raises:
        ValueError: if either cluster has no items (nothing to judge).
        RuntimeError: if the model returns no parsed output. No fallback
            verdict — the caller decides whether to skip the proposal
            entirely vs. retry.
    """
    if not cluster_a.items.exists():
        raise ValueError(f"Cluster {cluster_a.id} has no items — nothing to judge.")
    if not cluster_b.items.exists():
        raise ValueError(f"Cluster {cluster_b.id} has no items — nothing to judge.")

    prompt = prompt_loader.load_prompt("cluster_judge", "merge")
    response = call_cheap_model(
        system_prompt=prompt.content,
        user_content=_build_user_content(cluster_a, cluster_b, centroid_similarity),
        output_schema=_MergeResponse,
        max_tokens=_MAX_TOKENS,
    )
    parsed = response.parsed
    assert isinstance(parsed, _MergeResponse)
    return MergeVerdict(
        verdict=parsed.verdict,
        confidence=parsed.confidence,
        reasoning=parsed.reasoning.strip(),
        prompt_hash=prompt.hash,
        model=response.model_used,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cached_tokens=response.cached_tokens,
        latency_ms=response.latency_ms,
    )


# Re-export so callers see all judges from one module without reaching into
# unrelated submodules later when ``judge_split`` lands.
__all__ = ["MergeVerdict", "judge_merge"]
