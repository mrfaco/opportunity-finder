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
import time

from django.conf import settings
from pydantic import BaseModel, Field

from agents import prompts as prompt_loader
from clusters.models import Cluster
from core.anthropic_client import get_client

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
    client = get_client()
    model = settings.MODEL_FILTER  # cheap-model tier; same as classifier + summarizer

    started = time.monotonic()
    response = client.messages.parse(
        model=model,
        max_tokens=_MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": prompt.content,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": _build_user_content(cluster_a, cluster_b, centroid_similarity),
            }
        ],
        output_format=_MergeResponse,
    )
    latency_ms = int((time.monotonic() - started) * 1000)

    parsed = response.parsed_output
    if parsed is None:
        raise RuntimeError(
            f"Merge judge returned no parsed output for clusters "
            f"{cluster_a.id} + {cluster_b.id} (stop_reason={response.stop_reason!r})."
        )

    usage = response.usage
    return MergeVerdict(
        verdict=parsed.verdict,
        confidence=parsed.confidence,
        reasoning=parsed.reasoning.strip(),
        prompt_hash=prompt.hash,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        latency_ms=latency_ms,
    )


# Re-export so callers see all judges from one module without reaching into
# unrelated submodules later when ``judge_split`` lands.
__all__ = ["MergeVerdict", "judge_merge"]
