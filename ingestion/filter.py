"""Binary opportunity classifier — Haiku-powered filter.

Given the raw text of an ingested item, decide whether it describes an unmet
user need worth investigating. Output is a structured ``FilterVerdict``.

This module is intentionally separate from the agent loop. It is a workflow
step (single deterministic call), not an agentic one.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agents import prompts as prompt_loader
from clusters.models import ClusterItem


class FilterVerdict(BaseModel):
    is_opportunity: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    prompt_hash: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    latency_ms: int


def classify_item(item: ClusterItem) -> FilterVerdict:
    """Classify an ingested item.

    TODO(v1-followup): implement the actual Anthropic Haiku call. The
    classifier prompt lives in ``prompts/filter/classifier.md``; load it via
    ``prompt_loader.load_prompt('filter', 'classifier')`` and pass through
    the SDK. The prompt's canonical hash MUST be recorded on the resulting
    ``FilterClassification`` row so eval runs can key by version.
    """
    _ = prompt_loader.load_prompt("filter", "classifier")
    raise NotImplementedError("TODO(v1-followup): wire up the Anthropic Haiku classifier call")
