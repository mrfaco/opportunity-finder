"""Cluster title + summary generator.

A single deterministic Haiku call that turns a cluster's member items into
a short title + one-sentence summary. Mirrors the shape of
``ingestion/filter.py``: pull a git-managed prompt, build a JSON-encoded
user turn, parse a structured response, no fallback values on error.

Used by:
* ``clusters/tasks.py::refine_clusters_nightly`` — for multi-item clusters
  whose size has changed materially since the last titling pass.
* ``clusters/management/commands/backfill_cluster_titles.py`` — one-shot
  to title every existing untitled multi-item cluster.

Singletons do NOT pass through here — they get their title set directly
from the underlying item title at cluster-creation time (see
``clusters.clustering.assign_to_cluster``). Burning a Haiku call to
restate a single item's own title is wasteful.
"""

from __future__ import annotations

import json
import logging
import time

from django.conf import settings
from pydantic import BaseModel, Field

from agents import prompts as prompt_loader
from clusters.models import Cluster, ClusterItem
from core.anthropic_client import get_client

logger = logging.getLogger(__name__)

# A title + one sentence — 256 tokens is generous overhead.
_MAX_TOKENS = 256

# How many member items to feed the model. Beyond ~8 we hit diminishing
# returns; the goal is the common thread, not exhaustive coverage.
_MAX_REPRESENTATIVE_ITEMS = 8


class _ModelResponse(BaseModel):
    """Structured shape the model is constrained to return."""

    title: str = Field(min_length=3, max_length=140)
    summary: str = Field(min_length=10, max_length=400)


class TitleSummary(BaseModel):
    """Public result type. Includes provenance for replay."""

    title: str
    summary: str
    prompt_hash: str
    model: str
    item_count_used: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    latency_ms: int


def _representative_items(cluster: Cluster) -> list[ClusterItem]:
    """Return up to ``_MAX_REPRESENTATIVE_ITEMS`` of the cluster's items.

    Picks highest-confidence first — those are the items most diagnostic of
    the underlying need. Returns a plain list (not a queryset) because the
    caller iterates it twice (count + serialization).
    """
    return list(cluster.items.order_by("-classifier_confidence")[:_MAX_REPRESENTATIVE_ITEMS])


def _build_user_content(cluster: Cluster, items: list[ClusterItem]) -> str:
    """Render the cluster as deterministic JSON for the user turn."""
    payload = {
        "size": cluster.size,
        "sources": sorted(set(cluster.sources)),
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
    return json.dumps(payload, sort_keys=True, indent=2)


def generate_title_and_summary(cluster: Cluster) -> TitleSummary:
    """Produce a title + summary for ``cluster``.

    Raises:
        ValueError: if the cluster has no items (nothing to summarize).
        RuntimeError: if the model returns no parsed output. No fallback
            value — caller decides how to record the failure.

    Errors from the SDK (auth, rate limit, server) propagate.
    """
    items = _representative_items(cluster)
    if not items:
        raise ValueError(f"Cluster {cluster.id} has no items — nothing to summarize.")

    prompt = prompt_loader.load_prompt("cluster_summary", "system")
    client = get_client()
    model = settings.MODEL_FILTER  # cheap-model tier; same as the classifier

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
        messages=[{"role": "user", "content": _build_user_content(cluster, items)}],
        output_format=_ModelResponse,
    )
    latency_ms = int((time.monotonic() - started) * 1000)

    parsed = response.parsed_output
    if parsed is None:
        raise RuntimeError(
            f"Cluster summarizer returned no parsed output for cluster "
            f"{cluster.id} (stop_reason={response.stop_reason!r})."
        )

    usage = response.usage
    return TitleSummary(
        title=parsed.title.strip(),
        summary=parsed.summary.strip(),
        prompt_hash=prompt.hash,
        model=model,
        item_count_used=len(items),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        latency_ms=latency_ms,
    )
